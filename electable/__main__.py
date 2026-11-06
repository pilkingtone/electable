"""Electable command line.

  python -m electable calibrate            # historical error terms
  python -m electable fetch                # pull polls, ratings, results, FEC
  python -m electable build [--as-of D] [--backfill-days 60]
  python -m electable all                  # fetch + build
  python -m electable results              # after the election: score the frozen final forecast
  python -m electable news                 # compile the News channel
  python -m electable auto                 # news + (forecast before Election Day | results after)
"""
from __future__ import annotations

import argparse
from datetime import date

from . import calibrate, news, pipeline
from .config import races_config


def main() -> None:
    ap = argparse.ArgumentParser(prog="electable")
    ap.add_argument("command", choices=["calibrate", "fetch", "build", "all", "results", "news", "auto"])
    ap.add_argument("--as-of", type=date.fromisoformat, default=None)
    ap.add_argument("--backfill-days", type=int, default=0)
    ap.add_argument("--step", type=int, default=7)
    a = ap.parse_args()
    if a.command == "calibrate":
        r = calibrate.run()
        print("wrote config/calibrated.json:", r["overrides"])
    if a.command in ("fetch", "all"):
        pipeline.fetch()
    if a.command == "auto":
        # Forecast until Election Day; after it, score the frozen final forecast.
        election = date.fromisoformat(races_config()["election_date"])
        a.command = "all" if date.today() <= election else "results"
        print(f"[electable] auto -> {a.command}", flush=True)
        news.run()
    if a.command == "news":
        news.run()
    if a.command == "results":
        pipeline.results()
    if a.command in ("build", "all"):
        pipeline.build(as_of=a.as_of, backfill_days=a.backfill_days, step=a.step)


if __name__ == "__main__":
    main()
