"""Hindsight check: re-run the final forecast's exact inputs (data/final/) with the
current model, calibrated WITHOUT the cycle being scored, and compare it with the
forecast as published. The published forecast itself is never changed.
"""
from __future__ import annotations

import copy
import json
from datetime import date, datetime, timezone

import numpy as np

from . import calibrate, pipeline
from .config import CONFIG_DIR, WEB_DATA_DIR, _deep_update

FINAL = pipeline.FINAL_DIR


def _score(chambers: dict, results: dict) -> dict:
    out = {}
    for ch, c in chambers.items():
        if c.get("status") != "ok":
            continue
        s = [(r["p_dem"], results[r["id"]]["winner"] == "D", r["median"], results[r["id"]].get("margin"))
             for r in c["races"] if results.get(r["id"], {}).get("called") and not r.get("uncontested")]
        if not s:
            continue
        p = np.array([x[0] for x in s]); won = np.array([x[1] for x in s], float)
        m = [x[3] - x[2] for x in s if x[3] is not None]
        out[ch] = {"right": int(((p >= .5) == won).sum()), "n": len(s),
                   "brier": round(float(((p - won) ** 2).mean()), 4),
                   "margin_bias": round(float(np.mean(m)), 2) if m else None,
                   "p_dem_control": round(c["p_dem_control"], 4)}
    return out


def run() -> dict:
    final = json.loads((FINAL / "forecast.json").read_text())
    results = json.loads((WEB_DATA_DIR / "results.json").read_text())["races"]
    cfg = json.loads((CONFIG_DIR / "model.json").read_text())
    # Calibrate on history only (no 2026), so the cycle being scored is out of sample.
    _deep_update(cfg, calibrate.run(include_recent=False, write=False)["overrides"])
    inp = pipeline.load_inputs(FINAL / "processed", FINAL / "manual")
    v2 = pipeline.run_model(inp, date.fromisoformat(final["as_of"]), cfg=copy.deepcopy(cfg),
                            electit_cfg={"ensemble_weight": 0.0})
    out = {"generated_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds") + "Z",
           "note": ("Model v2 re-runs the final forecast's exact Nov 3 inputs with a calibration fix "
                    "(the fundamentals' error is now the plain standard deviation, which 1998-2022 Senate "
                    "races show weights polls correctly). v2 is calibrated without 2026 data, so this is "
                    "an out-of-sample check. The published forecast is unchanged."),
           "published": _score(final["chambers"], results),
           "model_v2": _score(v2["chambers"], results)}
    (WEB_DATA_DIR / "hindsight.json").write_text(json.dumps(out))
    for ch in out["published"]:
        a, b = out["published"][ch], out["model_v2"].get(ch, {})
        print(f"[electable] hindsight {ch}: published {a['right']}/{a['n']} (Brier {a['brier']}) "
              f"-> v2 {b.get('right')}/{b.get('n')} (Brier {b.get('brier')})", flush=True)
    return out
