"""SQLite database with WAL mode and event store."""

import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

import aiosqlite

from fortress.core.event_bus import Event

logger = logging.getLogger("fortress.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    source TEXT NOT NULL,
    payload TEXT,
    severity INTEGER DEFAULT 0,
    timestamp REAL NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT,
    event_type TEXT,
    rule_name TEXT,
    action_type TEXT,
    action_params TEXT,
    result TEXT,
    llm_used INTEGER DEFAULT 0,
    latency_ms REAL,
    timestamp REAL,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id INTEGER,
    action_type TEXT NOT NULL,
    action_params TEXT,
    status TEXT DEFAULT 'pending',
    result TEXT,
    error TEXT,
    dry_run INTEGER DEFAULT 0,
    timestamp REAL,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_events_type ON events(type);
CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp);
CREATE INDEX IF NOT EXISTS idx_decisions_timestamp ON decisions(timestamp);
"""


class Database:
    """Async SQLite database with WAL mode."""

    def __init__(self, db_path: str = "data/fortress.db"):
        self.db_path = db_path
        self._conn: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        db_path = Path(self.db_path).expanduser()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(str(db_path))
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()
        logger.info(f"Database connected: {db_path}")

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()

    async def store_event(self, event: Event) -> None:
        await self._conn.execute(
            "INSERT OR IGNORE INTO events (id, type, source, payload, severity, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
            (event.id, event.type, event.source, json.dumps(event.payload), event.severity, event.timestamp),
        )
        await self._conn.commit()

    async def store_decision(self, event_id: str, event_type: str, rule_name: str | None,
                              action_type: str, action_params: dict, result: str,
                              llm_used: bool, latency_ms: float) -> int:
        cursor = await self._conn.execute(
            "INSERT INTO decisions (event_id, event_type, rule_name, action_type, action_params, result, llm_used, latency_ms, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (event_id, event_type, rule_name, action_type, json.dumps(action_params), result, int(llm_used), latency_ms, time.time()),
        )
        await self._conn.commit()
        return cursor.lastrowid

    async def store_action(self, decision_id: int, action_type: str, action_params: dict,
                            status: str, result: str = "", error: str = "", dry_run: bool = False) -> None:
        await self._conn.execute(
            "INSERT INTO actions (decision_id, action_type, action_params, status, result, error, dry_run, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (decision_id, action_type, json.dumps(action_params), status, result, error, int(dry_run), time.time()),
        )
        await self._conn.commit()

    async def get_recent_events(self, limit: int = 100) -> list[dict]:
        cursor = await self._conn.execute(
            "SELECT id, type, source, payload, severity, timestamp FROM events ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
        return [{"id": r[0], "type": r[1], "source": r[2], "payload": json.loads(r[3] or "{}"),
                 "severity": r[4], "timestamp": r[5]} for r in rows]

    async def get_recent_decisions(self, limit: int = 20) -> list[dict]:
        cursor = await self._conn.execute(
            "SELECT event_type, action_type, rule_name, result, timestamp FROM decisions ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
        return [{"event_type": r[0], "action_type": r[1], "rule_name": r[2],
                 "result": r[3], "timestamp": r[4]} for r in rows]

    async def get_stats(self) -> dict:
        cursor = await self._conn.execute("SELECT COUNT(*) FROM events")
        events = (await cursor.fetchone())[0]
        cursor = await self._conn.execute("SELECT COUNT(*) FROM decisions")
        decisions = (await cursor.fetchone())[0]
        cursor = await self._conn.execute("SELECT COUNT(*) FROM actions")
        actions = (await cursor.fetchone())[0]
        return {"events": events, "decisions": decisions, "actions": actions}

    async def cleanup_old(self, days: int = 30) -> int:
        cutoff = time.time() - days * 86400
        c1 = await self._conn.execute("DELETE FROM actions WHERE timestamp < ?", (cutoff,))
        c2 = await self._conn.execute("DELETE FROM decisions WHERE timestamp < ?", (cutoff,))
        c3 = await self._conn.execute("DELETE FROM events WHERE timestamp < ?", (cutoff,))
        await self._conn.commit()
        return c1.rowcount + c2.rowcount + c3.rowcount
