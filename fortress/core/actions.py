"""Action runner — executes decisions with approval, dry-run, and logging."""

import asyncio
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from fortress.core.event_bus import Event
from fortress.core.rules import Action

logger = logging.getLogger("fortress.actions")

# Allowed action types
ALLOWED_ACTION_TYPES = {"move", "copy", "delete", "notify", "log", "execute_bash"}

# Allowed shell commands — whitelist (not blocklist)
ALLOWED_SHELL_COMMANDS = {
    "dir", "ls", "cat", "type", "echo", "date", "time",
    "ipconfig", "ifconfig", "ping", "netstat", "tasklist",
    "ps", "top", "df", "du", "wc", "head", "tail",
    "find", "where", "which", "whoami", "hostname",
}

# Dangerous argument patterns
_DANGEROUS_ARG_PATTERNS = (
    ";", "&&", "||", "|", "`", "$(",
    ">", "<", ">>",
    "rm ", "del ", "format", "shutdown", "reboot",
    "mkfs", "fdisk", "dd ",
)


@dataclass
class ActionResult:
    status: str = "ok"
    result: str = ""
    error: str = ""


class ActionRunner:
    """Execute actions with safety checks."""

    DESTRUCTIVE_TYPES = {"execute_bash", "write_file", "delete_file", "ha_call", "move", "copy", "delete"}

    def __init__(self, dry_run: bool = False, require_approval: bool = True, allowed_paths: list[str] = None):
        self.dry_run = dry_run
        self.require_approval = require_approval
        self.allowed_paths = [Path(p).expanduser().resolve() for p in (allowed_paths or [])]

    def _is_safe_path(self, path: str) -> bool:
        """Check if path is strictly within allowed directories (resolves symlinks)."""
        if not self.allowed_paths:
            return False  # No allowed paths = deny all
        try:
            resolved = Path(path).expanduser().resolve()
            return any(resolved == root or str(resolved).startswith(str(root) + os.sep)
                       for root in self.allowed_paths)
        except Exception:
            return False

    def _is_safe_command(self, cmd: str) -> bool:
        """Check if shell command uses only allowed programs and no injection."""
        import shlex
        cmd = cmd.strip()
        if not cmd:
            return False
        # Check for dangerous patterns first
        cmd_lower = cmd.lower()
        for pattern in _DANGEROUS_ARG_PATTERNS:
            if pattern in cmd_lower:
                return False
        # Parse command and check program name
        try:
            parts = shlex.split(cmd)
        except ValueError:
            return False  # Malformed command
        if not parts:
            return False
        program = Path(parts[0]).name.lower()
        return program in ALLOWED_SHELL_COMMANDS

    async def execute(self, action: Action, event: Event | None = None) -> ActionResult:
        if self.dry_run:
            logger.info(f"[DRY-RUN] {action.type}: {action.params}")
            return ActionResult(status="dry_run", result=f"dry_run: {action.type}")

        # Validate action type
        if action.type not in ALLOWED_ACTION_TYPES:
            logger.warning(f"Blocked unknown action type: {action.type}")
            return ActionResult(status="denied", error=f"Unknown action type: {action.type}")

        # Path safety check for file operations
        if action.type in ("move", "copy", "delete"):
            paths_to_check = [action.params.get("src", ""), action.params.get("dst", ""), action.params.get("path", "")]
            for p in paths_to_check:
                if p and not self._is_safe_path(p):
                    logger.warning(f"Path blocked: {p}")
                    return ActionResult(status="denied", error=f"Path outside allowed directories: {p}")

        # Command safety check is done in _handle_bash

        # Approval for destructive actions
        if self.require_approval and action.type in self.DESTRUCTIVE_TYPES:
            if not await self._request_approval(action):
                logger.info(f"Action denied: {action.type}")
                return ActionResult(status="denied", result="user denied")

        try:
            result = await self._dispatch(action, event)
            logger.info(f"Action OK: {action.type} → {result.status}")
            return result
        except Exception as e:
            logger.error(f"Action failed: {action.type} — {e}")
            return ActionResult(status="error", error=str(e))

    async def _dispatch(self, action: Action, event: Event | None = None) -> ActionResult:
        handlers = {
            "move": self._handle_move,
            "copy": self._handle_copy,
            "delete": self._handle_delete,
            "notify": self._handle_notify,
            "log": self._handle_log,
            "execute_bash": self._handle_bash,
        }
        handler = handlers.get(action.type)
        if handler:
            return await handler(action, event)
        return ActionResult(status="error", error=f"Unknown action type: {action.type}")

    async def _handle_move(self, action: Action, event: Event | None) -> ActionResult:
        src = action.params.get("src", "")
        dst = action.params.get("dst", "")
        if not src or not dst:
            return ActionResult(status="error", error="Missing src/dst")
        src_path = Path(src).expanduser()
        dst_path = Path(dst).expanduser()
        if not src_path.exists():
            return ActionResult(status="error", error=f"Source not found: {src}")
        dst_path.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(shutil.move, str(src_path), str(dst_path / src_path.name))
        return ActionResult(status="ok", result=f"Moved {src_path.name} → {dst_path}")

    async def _handle_copy(self, action: Action, event: Event | None) -> ActionResult:
        src = action.params.get("src", "")
        dst = action.params.get("dst", "")
        if not src or not dst:
            return ActionResult(status="error", error="Missing src/dst")
        src_path = Path(src).expanduser()
        if not src_path.exists():
            return ActionResult(status="error", error=f"Source not found: {src}")
        await asyncio.to_thread(shutil.copy2, str(src_path), dst)
        return ActionResult(status="ok", result=f"Copied → {dst}")

    async def _handle_delete(self, action: Action, event: Event | None) -> ActionResult:
        path = action.params.get("path", "")
        if not path:
            return ActionResult(status="error", error="Missing path")
        p = Path(path).expanduser()
        if not p.exists():
            return ActionResult(status="error", error=f"Path not found: {path}")
        if p.is_file():
            await asyncio.to_thread(p.unlink)
        elif p.is_dir():
            await asyncio.to_thread(shutil.rmtree, p)
        return ActionResult(status="ok", result=f"Deleted {path}")

    async def _handle_notify(self, action: Action, event: Event | None) -> ActionResult:
        message = action.params.get("message", "Fortress notification")
        logger.info(f"NOTIFY: {message}")
        return ActionResult(status="ok", result=f"Notified: {message[:50]}")

    async def _handle_log(self, action: Action, event: Event | None) -> ActionResult:
        message = action.params.get("message", "")
        if event:
            message = message or f"Event {event.type} from {event.source}"
        logger.info(f"LOG: {message}")
        return ActionResult(status="ok", result=f"Logged: {message[:50]}")

    async def _handle_bash(self, action: Action, event: Event | None) -> ActionResult:
        cmd = action.params.get("command", "")
        if not cmd:
            return ActionResult(status="error", error="Missing command")
        if not self._is_safe_command(cmd):
            return ActionResult(status="denied", error="Command blocked by security policy")
        # Use exec instead of shell to prevent injection
        import shlex
        try:
            parts = shlex.split(cmd)
        except ValueError:
            return ActionResult(status="error", error="Malformed command")
        proc = await asyncio.create_subprocess_exec(
            *parts, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return ActionResult(status="error", error="Command timed out (30s)")
        return ActionResult(
            status="ok" if proc.returncode == 0 else "error",
            result=stdout.decode(errors="replace").strip(),
            error=stderr.decode(errors="replace").strip(),
        )

    @staticmethod
    async def _request_approval(action: Action) -> bool:
        """Request user approval. Denies by default (no approval channel configured)."""
        logger.warning(f"Action requires approval but no channel configured: {action.type}")
        return False
