"""Authentication — token generation, validation, and middleware."""

import logging
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("fortress.auth")

TOKEN_LENGTH = 32  # bytes → 43 chars urlsafe
TOKEN_FILE = Path.home() / ".fortress" / "auth_token"


@dataclass
class AuthToken:
    """A single auth token with metadata."""
    token: str
    name: str = "default"
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0.0  # 0 = never
    ip_whitelist: list[str] = field(default_factory=list)  # empty = any IP


class AuthManager:
    """Token-based authentication for dashboard, API, and camera streams."""

    def __init__(self):
        self._tokens: dict[str, AuthToken] = {}
        self._master_token: str = ""
        self._load_or_create()

    def _load_or_create(self) -> None:
        """Load master token from file or generate new one."""
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        if TOKEN_FILE.exists():
            self._master_token = TOKEN_FILE.read_text().strip()
            if len(self._master_token) < 20:
                self._master_token = secrets.token_urlsafe(TOKEN_LENGTH)
        else:
            self._master_token = secrets.token_urlsafe(TOKEN_LENGTH)
        TOKEN_FILE.write_text(self._master_token)
        TOKEN_FILE.chmod(0o600)  # Owner read/write only
        logger.info(f"Auth token: {TOKEN_FILE}")

    def validate(self, token: str, client_ip: str = "") -> bool:
        """Validate a token. Returns True if valid."""
        if not token:
            return False
        # Master token always works
        if secrets.compare_digest(token, self._master_token):
            return True
        # Check registered tokens
        auth_token = self._tokens.get(token)
        if auth_token is None:
            return False
        # Check expiry
        if auth_token.expires_at and time.time() > auth_token.expires_at:
            del self._tokens[token]
            return False
        # Check IP whitelist
        if auth_token.ip_whitelist and client_ip not in auth_token.ip_whitelist:
            logger.warning(f"IP {client_ip} not in whitelist for token {auth_token.name}")
            return False
        return True

    def create_token(self, name: str, expires_in: float = 0,
                     ip_whitelist: list[str] = None) -> str:
        """Create a new named token. Returns the token string."""
        token = secrets.token_urlsafe(TOKEN_LENGTH)
        self._tokens[token] = AuthToken(
            token=token,
            name=name,
            expires_at=time.time() + expires_in if expires_in else 0,
            ip_whitelist=ip_whitelist or [],
        )
        logger.info(f"Created token: {name}")
        return token

    def revoke_token(self, token: str) -> bool:
        """Revoke a token. Returns True if found."""
        if token in self._tokens:
            del self._tokens[token]
            return True
        return False

    def list_tokens(self) -> list[dict]:
        """List all tokens (without the actual token strings)."""
        return [
            {"name": t.name, "created_at": t.created_at,
             "expires_at": t.expires_at, "ip_whitelist": t.ip_whitelist}
            for t in self._tokens.values()
        ]

    @property
    def master_token(self) -> str:
        return self._master_token
