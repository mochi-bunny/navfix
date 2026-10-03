#!/usr/bin/env python3
"""Start the Fleet & Field dashboard.

    python3 run.py                 # mode from config.json (mock by default)
    python3 run.py --mode live     # all sources live
    python3 run.py --port 9000
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
    args = ap.parse_args()
    serve(load_config(args.config, args.mode, args.port))
