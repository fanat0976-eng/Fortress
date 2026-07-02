"""Fortress V2 — CLI interface."""

import argparse
import asyncio
import json
import sys
from pathlib import Path


def cmd_start(args):
    """Start the Fortress daemon."""
    from fortress.main import run_daemon
    asyncio.run(run_daemon(args.config))


def cmd_status(args):
    """Show daemon status."""
    import aiosqlite
    import asyncio

    async def _status():
        db_path = args.db or "data/fortress.db"
        if not Path(db_path).exists():
            print(f"Database not found: {db_path}")
            print("Fortress has not been started yet.")
            return

        conn = await aiosqlite.connect(db_path)
        try:
            # Event count
            cursor = await conn.execute("SELECT COUNT(*) FROM events")
            events = (await cursor.fetchone())[0]

            # Decision count
            cursor = await conn.execute("SELECT COUNT(*) FROM decisions")
            decisions = (await cursor.fetchone())[0]

            # Recent events
            cursor = await conn.execute(
                "SELECT type, source, timestamp FROM events ORDER BY timestamp DESC LIMIT 5"
            )
            recent = await cursor.fetchall()

            print("═══ Fortress V2 Status ═══")
            print(f"  Events:   {events}")
            print(f"  Decisions: {decisions}")
            print(f"  Recent events:")
            for t, s, ts in recent:
                from datetime import datetime
                dt = datetime.fromtimestamp(ts).strftime("%H:%M:%S")
                print(f"    [{dt}] {t} ({s})")
        finally:
            await conn.close()

    asyncio.run(_status())


def cmd_events(args):
    """Show recent events."""
    import aiosqlite
    import asyncio

    async def _events():
        db_path = args.db or "data/fortress.db"
        if not Path(db_path).exists():
            print("No database found. Start Fortress first.")
            return

        conn = await aiosqlite.connect(db_path)
        try:
            cursor = await conn.execute(
                "SELECT id, type, source, severity, timestamp FROM events ORDER BY timestamp DESC LIMIT ?",
                (args.limit,),
            )
            rows = await cursor.fetchall()
            if not rows:
                print("No events recorded.")
                return

            print(f"═══ Last {len(rows)} Events ═══")
            for eid, etype, source, sev, ts in rows:
                from datetime import datetime
                dt = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
                sev_icon = ["ℹ️", "⚠️", "🔴"][min(sev, 2)]
                print(f"  {sev_icon} [{dt}] {etype} ({source})")
        finally:
            await conn.close()

    asyncio.run(_events())


def cmd_rules(args):
    """Show configured rules."""
    from fortress.core.config import load_config

    config = load_config(args.config)
    if not config.rules:
        print("No rules configured.")
        return

    print("═══ Rules ═══")
    for i, rule in enumerate(config.rules, 1):
        status = "ON" if rule.enabled else "OFF"
        print(f"  {i}. [{status}] {rule.event_pattern} → {rule.action_type}")
        if rule.condition:
            print(f"     IF: {rule.condition}")


def cmd_test(args):
    """Emit a test event to the running daemon."""
    import socket
    import json

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        result = sock.connect_ex(("127.0.0.1", 8090))
        sock.close()

        if result != 0:
            print("Dashboard not running on port 8090.")
            print("Start Fortress with: fortress start")
            return
    except Exception:
        print("Cannot check dashboard status.")
        return

    print("Test event sent (check dashboard at http://127.0.0.1:8090)")


def main():
    parser = argparse.ArgumentParser(
        prog="fortress",
        description="Fortress V2 — Event-driven autonomous AI daemon",
    )
    parser.add_argument("--version", action="version", version="Fortress V2 v0.1.0")

    sub = parser.add_subparsers(dest="command", help="Available commands")

    # start
    p_start = sub.add_parser("start", help="Start the daemon")
    p_start.add_argument("--config", "-c", default="config.yaml", help="Config file")
    p_start.add_argument("--dry-run", action="store_true", help="Dry run mode")
    p_start.set_defaults(func=cmd_start)

    # status
    p_status = sub.add_parser("status", help="Show daemon status")
    p_status.add_argument("--db", default=None, help="Database path")
    p_status.set_defaults(func=cmd_status)

    # events
    p_events = sub.add_parser("events", help="Show recent events")
    p_events.add_argument("-n", "--limit", type=int, default=20, help="Number of events")
    p_events.add_argument("--db", default=None, help="Database path")
    p_events.set_defaults(func=cmd_events)

    # rules
    p_rules = sub.add_parser("rules", help="Show configured rules")
    p_rules.add_argument("--config", "-c", default="config.yaml", help="Config file")
    p_rules.set_defaults(func=cmd_rules)

    # test
    p_test = sub.add_parser("test", help="Emit test event")
    p_test.set_defaults(func=cmd_test)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    args.func(args)


if __name__ == "__main__":
    main()
