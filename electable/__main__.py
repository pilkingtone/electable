"""Electable command line.

  python -m electable calibrate            # historical error terms
  python -m electable fetch                # pull polls, ratings, results, FEC
  python -m electable build [--as-of D] [--backfill-days 60]
  python -m electable all                  # fetch + build
"""
from __future__ import annotations

import argparse
from datetime import date

from . import calibrate, pipeline


def main() -> None:
    ap = argparse.ArgumentParser(prog="electable")
    ap.add_argument("command", choices=["calibrate", "fetch", "build", "all"])
    ap.add_argument("--as-of", type=date.fromisoformat, default=None)
    ap.add_argument("--backfill-days", type=int, default=0)
    ap.add_argument("--step", type=int, default=7)
    a = ap.parse_args()
    if a.command == "calibrate":
        r = calibrate.run()
        print("wrote config/calibrated.json:", r["overrides"])
    if a.command in ("fetch", "all"):
        pipeline.fetch()
    if a.command in ("build", "all"):
        pipeline.build(as_of=a.as_of, backfill_days=a.backfill_days, step=a.step)


if __name__ == "__main__":
    main()
