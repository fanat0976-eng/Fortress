"""Fortress V2.1.1D — entry point.

Usage:
    python -m fortress              → Orchestrator (Tkinter GUI)
    python -m fortress daemon       → Headless daemon (old mode)
    python -m fortress eye          → Eye module only
    python -m fortress strazh       → Strazh module only
    python -m fortress --help       → Show this help
"""

import sys


def main():
    args = sys.argv[1:] if len(sys.argv) > 1 else []

    if args and args[0] in ("--help", "-h"):
        print("Fortress V2.1.1D (Dual)")
        print()
        print("Usage:")
        print("  python -m fortress              Launch orchestrator (GUI)")
        print("  python -m fortress daemon       Run headless daemon")
        print("  python -m fortress eye          Launch Eye (camera) only")
        print("  python -m fortress strazh       Launch Strazh (monitoring) only")
        print("  python -m fortress --help       Show this help")
        return

    if not args:
        # No args → launch orchestrator GUI
        from fortress.main_tk import main as tk_main
        tk_main()
        return

    mode = args[0].lower()

    if mode == "daemon":
        from fortress.main import main as daemon_main
        daemon_main()

    elif mode == "eye":
        from fortress.eye.launcher import main as eye_main
        eye_main()

    elif mode == "strazh":
        from fortress.strazh.launcher import main as strazh_main
        strazh_main()

    else:
        print(f"Unknown command: {mode}")
        print("Use --help for usage")


if __name__ == "__main__":
    main()
