#!/usr/bin/env python3
"""Start the Fleet & Field dashboard.

    python3 run.py                 # mode from config.json (mock by default)
    python3 run.py --mode live     # all sources live
    python3 run.py --port 9000
    python3 run.py --navbot        # Needs, gates, warehouse and ops log from navbot's data/bot.db
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from server.app import load_config, serve  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Fleet & Field dashboard")
    ap.add_argument("--config", default=str(Path(__file__).resolve().parent / "config.json"))
    ap.add_argument("--mode", choices=["mock", "live"], default=os.environ.get("DASH_MODE"))
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--navbot", action="store_true",
                    help="read tickets, cell and messages from the Discord bot's database (live.navbot.db_path)")
    args = ap.parse_args()
    cfg = load_config(args.config, args.mode, args.port)
    if args.navbot:
        cfg["sources"] = {**(cfg.get("sources") or {}), "tickets": "navbot", "cell": "navbot", "messages": "navbot"}
    serve(cfg)
