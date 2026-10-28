"""Prediction-market prices, shown for comparison only (never a model input).

Configure markets in config/markets.json:
  [{"label": "Democrats win Senate", "chamber": "senate", "outcome": "D",
    "venue": "kalshi", "ticker": "<market ticker>"},
   {"label": "...", "chamber": "house", "outcome": "D",
    "venue": "polymarket", "slug": "<market slug>"}]
"""
from __future__ import annotations

import json

from ..config import CONFIG_DIR
from ..http import get

KALSHI = "https://api.elections.kalshi.com/trade-api/v2/markets/"
POLYMARKET = "https://gamma-api.polymarket.com/markets"


def fetch_all() -> tuple[list[dict], list[str]]:
    path = CONFIG_DIR / "markets.json"
    if not path.exists():
        return [], []
    out, warnings = [], []
    for m in json.loads(path.read_text()):
        try:
            if m["venue"] == "kalshi":
                d = get(KALSHI + m["ticker"], as_json=True, max_age_hours=1)["market"]
                price = (d.get("last_price") or d.get("yes_bid") or 0) / 100
            elif m["venue"] == "polymarket":
                d = get(POLYMARKET, {"slug": m["slug"]}, as_json=True, max_age_hours=1)[0]
                price = float(json.loads(d["outcomePrices"])[0])
            else:
                continue
            out.append({**m, "probability": price})
        except Exception as e:  # noqa: BLE001 - comparison data is best-effort
            warnings.append(f"market {m.get('label')}: {e}")
    return out, warnings
