"""Email monitor plugin — IMAP subscription for incoming emails."""

import asyncio
import email as email_pkg
import logging
import re
import time
from email.header import decode_header
from typing import TYPE_CHECKING

from fortress.core.plugin import BasePlugin
from fortress.core.event_bus import Event

if TYPE_CHECKING:
    from fortress.core.config import PluginConfig
    from fortress.core.event_bus import EventBus

logger = logging.getLogger("fortress.plugins.email")


class EmailMonitorPlugin(BasePlugin):
    """Monitor email inbox via IMAP IDLE for new messages."""

    name = "email_monitor"
    description = "Watch IMAP inbox for new emails, emit events"

    def __init__(self, config: "PluginConfig"):
        self.config = config
        self._bus = None
        self._running = False
        self._task = None
        self._seen_uids: set[str] = set()
        self._check_interval = 60  # seconds between checks (fallback if IDLE not supported)

    async def start(self, bus: "EventBus") -> None:
        if not self.config.imap_server or not self.config.imap_email:
            logger.warning("Email monitor disabled: no imap_server/email configured")
            return

        self._bus = bus
        self._running = True
        self._task = asyncio.create_task(self._monitor_loop())
        logger.info(f"Email monitor started ({self.config.imap_server})")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Email monitor stopped")

    async def _monitor_loop(self) -> None:
        """Poll IMAP inbox for new emails."""
        while self._running:
            try:
                new_emails = await self._check_inbox()
                for email_data in new_emails:
                    await self._bus.emit(Event(
                        type="email.new",
                        source="plugin.email_monitor",
                        payload=email_data,
                        severity=1 if email_data.get("is_important") else 0,
                    ))
                await asyncio.sleep(self._check_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Email check error: {e}")
                await asyncio.sleep(30)

    async def _check_inbox(self) -> list[dict]:
        """Connect to IMAP and check for new emails."""
        import imaplib

        server = self.config.imap_server
        port = getattr(self.config, "imap_port", 993)
        email_addr = self.config.imap_email
        password = getattr(self.config, "imap_password", "")
        use_ssl = getattr(self.config, "imap_ssl", True)

        if not password:
            logger.warning("No IMAP password configured")
            return []

        try:
            # Connect in thread to avoid blocking
            result = await asyncio.to_thread(
                self._fetch_emails, server, port, email_addr, password, use_ssl
            )
            return result
        except Exception as e:
            logger.error(f"IMAP error: {type(e).__name__}: {e}")
            return []

    def _fetch_emails(self, server: str, port: int, email_addr: str,
                      password: str, use_ssl: bool) -> list[dict]:
        """Fetch new emails from IMAP (blocking, called in thread)."""
        import imaplib

        new_emails = []
        conn = None

        try:
            if use_ssl:
                conn = imaplib.IMAP4_SSL(server, port)
            else:
                conn = imaplib.IMAP4(server, port)

            conn.login(email_addr, password)
            conn.select("INBOX")

            # Search for unseen emails
            status, data = conn.search(None, "UNSEEN")
            if status != "OK":
                return []

            msg_ids = data[0].split()

            for msg_id in msg_ids:
                msg_id_str = msg_id.decode()
                if msg_id_str in self._seen_uids:
                    continue

                # Fetch email headers
                status, msg_data = conn.fetch(msg_id, "(RFC822.HEADER)")
                if status != "OK" or not msg_data or not isinstance(msg_data, list):
                    continue

                # Bounds check IMAP response structure
                try:
                    raw = msg_data[0][1]
                except (IndexError, TypeError):
                    continue

                if isinstance(raw, bytes):
                    msg = email_pkg.message_from_bytes(raw)
                else:
                    continue

                subject = self._decode_header(msg.get("Subject", ""))
                sender = msg.get("From", "")
                date = msg.get("Date", "")
                message_id = msg.get("Message-ID", msg_id_str)

                # Check for importance
                is_important = self._check_importance(msg, subject, sender)

                email_data = {
                    "subject": subject,
                    "from": sender,
                    "date": date,
                    "message_id": message_id,
                    "uid": msg_id_str,
                    "is_important": is_important,
                }

                # Extract body preview (first 500 chars)
                body = self._extract_body_preview(msg)
                if body:
                    email_data["preview"] = body[:500]

                new_emails.append(email_data)
                self._seen_uids.add(msg_id_str)

        except Exception as e:
            logger.error(f"IMAP fetch error: {type(e).__name__}")
        finally:
            if conn:
                try:
                    conn.logout()
                except Exception:
                    pass

        return new_emails

    def _decode_header(self, header: str) -> str:
        """Decode MIME-encoded email header."""
        if not header:
            return ""
        try:
            decoded_parts = decode_header(header)
            result = []
            for part, charset in decoded_parts:
                if isinstance(part, bytes):
                    result.append(part.decode(charset or "utf-8", errors="replace"))
                else:
                    result.append(part)
            return " ".join(result)
        except Exception:
            return header

    def _extract_body_preview(self, msg) -> str:
        """Extract plain text body preview from email message."""
        try:
            if msg.is_multipart():
                for part in msg.walk():
                    content_type = part.get_content_type()
                    if content_type == "text/plain":
                        payload = part.get_payload(decode=True)
                        if payload:
                            charset = part.get_content_charset() or "utf-8"
                            return payload.decode(charset, errors="replace")[:500]
            else:
                payload = msg.get_payload(decode=True)
                if payload:
                    charset = msg.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")[:500]
        except Exception:
            pass
        return ""

    def _check_importance(self, msg, subject: str, sender: str) -> bool:
        """Check if email is important based on headers and content."""
        # X-Priority header
        priority = msg.get("X-Priority", "")
        if priority in ("1", "2", "High"):
            return True

        # Importance header
        importance = msg.get("Importance", "").lower()
        if importance in ("high", "urgent"):
            return True

        # Subject keywords
        important_keywords = ["urgent", "important", "critical", "alert",
                              "security", "password", "breach", "suspicious"]
        subject_lower = subject.lower()
        if any(kw in subject_lower for kw in important_keywords):
            return True

        # Known important senders
        important_senders = getattr(self.config, "important_senders", [])
        if important_senders:
            sender_lower = sender.lower()
            if any(s.lower() in sender_lower for s in important_senders):
                return True

        return False

    def config_schema(self) -> dict:
        return {
            "imap_server": {"type": "string", "default": "imap.gmail.com"},
            "imap_port": {"type": "integer", "default": 993},
            "imap_email": {"type": "string"},
            "imap_password": {"type": "string"},
            "imap_ssl": {"type": "boolean", "default": True},
            "check_interval": {"type": "integer", "default": 60},
            "important_senders": {"type": "array", "items": {"type": "string"}},
        }
