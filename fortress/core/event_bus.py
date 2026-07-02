"""Event bus with deduplication, rate limiting, and pub/sub."""

import asyncio
import hashlib
import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger("fortress.bus")


@dataclass
class Event:
    """An event emitted by a plugin or system component."""

    type: str            # "file.created", "sensor.motion", "network.new_device"
    source: str          # "plugin.file_watcher", "plugin.network_monitor"
    payload: dict = field(default_factory=dict)
    severity: int = 0    # 0=info, 1=warning, 2=critical
    timestamp: float = field(default_factory=time.time)
    id: str = ""

    def __post_init__(self):
        if not self.id:
            self.id = f"{self.type}:{self.source}:{int(self.timestamp * 1000)}"


class RateLimiter:
    """Simple token bucket rate limiter."""

    def __init__(self, max_per_second: float = 10.0):
        self.max_per_second = max_per_second
        self.tokens = max_per_second
        self.last_refill = time.monotonic()

    def allow(self) -> bool:
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.max_per_second, self.tokens + elapsed * self.max_per_second)
        self.last_refill = now
        if self.tokens >= 1:
            self.tokens -= 1
            return True
        return False


class EventBus:
    """Async pub/sub event bus with deduplication and rate limiting."""

    def __init__(self, dedup_window: float = 5.0, max_rate: float = 10.0):
        self._subscribers: list[tuple[str, Callable]] = []
        self._dedup_window = dedup_window
        self._recent: dict[str, float] = {}
        self._rate_limiter = RateLimiter(max_rate)
        self._queue: asyncio.Queue[Event | None] = asyncio.Queue(maxsize=1000)
        self._history: deque[Event] = deque(maxlen=500)

    async def emit(self, event: Event) -> bool:
        """Emit an event. Returns True if processed, False if deduped/throttled."""
        # Deduplication — use content hash for efficiency
        payload_hash = hashlib.md5(
            json.dumps(event.payload, sort_keys=True, default=str).encode()
        ).hexdigest()[:16]
        dedup_key = f"{event.type}:{event.source}:{payload_hash}"
        now = time.time()
        if dedup_key in self._recent and now - self._recent[dedup_key] < self._dedup_window:
            logger.debug(f"Deduped: {event.type}")
            return False
        self._recent[dedup_key] = now

        # Cleanup old dedup entries
        if len(self._recent) > 1000:
            cutoff = now - self._dedup_window * 2
            self._recent = {k: v for k, v in self._recent.items() if v > cutoff}

        # Rate limit
        if not self._rate_limiter.allow():
            logger.debug(f"Rate limited: {event.type}")
            return False

        # Store in history (deque auto-trims to maxlen)
        self._history.append(event)

        # Queue for main loop
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("Event queue full, dropping event")

        # Notify subscribers concurrently
        matched = [(pattern, handler) for pattern, handler in self._subscribers
                   if self._match_type(pattern, event.type)]
        if matched:
            results = await asyncio.gather(
                *[handler(event) for _, handler in matched],
                return_exceptions=True,
            )
            for (pattern, _), result in zip(matched, results):
                if isinstance(result, Exception):
                    logger.error(f"Subscriber error for {pattern}: {result}")

        return True

    def subscribe(self, type_pattern: str, handler: Callable) -> None:
        """Subscribe to events matching a type pattern (e.g., 'file.*', 'sensor.*')."""
        self._subscribers.append((type_pattern, handler))
        logger.debug(f"Subscribed to: {type_pattern}")

    def unsubscribe(self, handler: Callable) -> None:
        """Remove a subscription."""
        self._subscribers = [(p, h) for p, h in self._subscribers if h != handler]

    async def next(self, timeout: float = 1.0) -> Optional[Event]:
        """Get next event from queue with timeout."""
        try:
            return await asyncio.wait_for(self._queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    def history(self, limit: int = 100) -> list[Event]:
        """Return recent events."""
        items = list(self._history)
        return items[-limit:]

    def stats(self) -> dict:
        """Return bus statistics."""
        return {
            "subscribers": len(self._subscribers),
            "history_size": len(self._history),
            "queue_size": self._queue.qsize(),
        }

    @staticmethod
    def _match_type(pattern: str, event_type: str) -> bool:
        """Match event type against pattern with wildcard support."""
        if pattern == "*":
            return True
        if "*" not in pattern:
            return pattern == event_type
        prefix = pattern.rstrip("*")
        return event_type.startswith(prefix)
