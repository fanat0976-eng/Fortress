"""Context manager — sliding window of recent decisions for LLM."""

import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fortress.core.database import Database

logger = logging.getLogger("fortress.context")


class ContextManager:
    """Provides recent decision context for LLM reasoning."""

    def __init__(self, db: "Database", window_size: int = 20):
        self.db = db
        self.window_size = window_size

    async def get_recent(self, n: int | None = None) -> str:
        """Return formatted context from recent decisions."""
        n = n if n is not None else self.window_size
        decisions = await self.db.get_recent_decisions(limit=n)
        if not decisions:
            return "No recent decisions."
        lines = []
        for d in reversed(decisions):
            rule = f" (rule: {d['rule_name']})" if d.get("rule_name") else " (LLM)"
            lines.append(f"- {d['event_type']} → {d['action_type']}{rule}")
        return "\n".join(lines)

    async def get_event_summary(self, hours: int = 24) -> str:
        """Summarize events from last N hours."""
        cutoff = time.time() - hours * 3600
        all_events = await self.db.get_recent_events(limit=500)
        # Filter by time window
        events = [e for e in all_events if e["timestamp"] >= cutoff]
        if not events:
            return f"No events in last {hours}h."

        type_counts: dict[str, int] = {}
        for e in events:
            t = e["type"]
            type_counts[t] = type_counts.get(t, 0) + 1

        lines = [f"Event summary ({len(events)} events, {hours}h window):"]
        for t, count in sorted(type_counts.items(), key=lambda x: -x[1]):
            lines.append(f"  {t}: {count}")
        return "\n".join(lines)
