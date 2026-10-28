"""SYNTHETIC inputs for tests and UI development. Not real data.

Leans, polls and candidates are random draws; names are placeholders. Never
publish output built from these files (forecast.json is marked "demo": true).
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from electable.config import races_config, race_id


def make(processed: Path, manual: Path, seed: int = 1, today: date = date(2026, 10, 28)) -> None:
    rng = np.random.default_rng(seed)
    rc = races_config()
    processed.mkdir(parents=True, exist_ok=True)
    manual.mkdir(parents=True, exist_ok=True)

    rows, polls = [], []
    for ch in ("senate", "governor"):
        for seat in rc[ch]["seats_up"]:
            st = seat["state"]
            lean = rng.normal(0, 14) + (6 if seat["holder"] == "D" else -6)
            rid = race_id(ch, st)
            rows.append({"race_id": rid, "chamber": ch, "state": st, "district": None,
                         "holder": seat["holder"], "lean": lean,
                         "dem_name": f"Test Dem{st}", "rep_name": f"Test Rep{st}",
                         "dem_label": "D", "incumbent_party": seat["holder"] if rng.random() < .5 else None,
                         "ratings": json.dumps([{"source": "Synthetic", "rating": "Tossup", "score": 0}]),
                         "source_url": "https://example.org/synthetic"})
            if abs(lean) < 12:
                truth = lean + 3
                for k in range(rng.integers(2, 9)):
                    m = truth + rng.normal(0, 4)
                    polls.append({"race_id": rid, "pollster": f"Synthetic Poll {k % 4}",
                                  "partisan": k == 0,
                                  "end_date": (today - timedelta(days=int(rng.integers(1, 60)))).isoformat(),
                                  "sample": int(rng.integers(400, 1500)), "population": "LV",
                                  "dem_pct": 46 + m / 2, "rep_pct": 46 - m / 2,
                                  "source_url": "https://example.org/synthetic"})
    pd.DataFrame(rows).to_csv(processed / "races.csv", index=False)
    pd.DataFrame(polls).to_csv(processed / "polls.csv", index=False)

    gb = [{"pollster": f"Synthetic Natl {k % 6}", "partisan": False,
           "end_date": (today - timedelta(days=int(d))).isoformat(), "sample": 1000,
           "population": "RV", "dem_pct": 46 + rng.normal(1.5, 1.5), "rep_pct": 45,
           "source_url": "https://example.org/synthetic"}
          for k, d in enumerate(rng.integers(1, 120, 60))]
    pd.DataFrame(gb).to_csv(processed / "generic_ballot.csv", index=False)

    hd = []
    for i in range(435):
        st = list(rc["governor"]["seats_up"])[i % 36]["state"]
        m = rng.normal(-2, 25)
        hd.append({"state": st, "district": i // 36 + 1, "holder": "D" if m > 0 else "R",
                   "incumbent_running": rng.random() < 0.85,
                   "dem_name": f"Test D{i}", "rep_name": f"Test R{i}",
                   "pres_margin_2024": m, "uncontested": ("D" if m > 45 else "R" if m < -45 else "")})
    pd.DataFrame(hd).to_csv(manual / "house_districts.csv", index=False)

    (processed / "meta.json").write_text(json.dumps({
        "fetched_at": "synthetic", "national_pres_margin_2024": -1.6,
        "national_house_margin_2024": -2.7, "warnings": ["SYNTHETIC DEMO DATA - not a forecast"],
        "sources": [{"name": "Synthetic fixture (tests/fixtures.py)", "url": ""}]}))
