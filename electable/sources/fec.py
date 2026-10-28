"""Federal Election Commission (OpenFEC API): candidate fundraising totals.

Set FEC_API_KEY (free at https://api.open.fec.gov/developers/); DEMO_KEY works
but is heavily rate limited. Governor races are state-level and not covered.
"""
from __future__ import annotations

import os

from ..http import get
from .wikipedia import surname

API = "https://api.open.fec.gov/v1/candidates/totals/"


def totals(office: str, state: str, district: int | None = None, cycle: int = 2026) -> list[dict]:
    params = {"api_key": os.environ.get("FEC_API_KEY", "DEMO_KEY"),
              "election_year": cycle, "office": office, "state": state,
              "per_page": 100, "sort": "-receipts", "is_active_candidate": "true"}
    if district is not None:
        params["district"] = f"{int(district):02d}"
    data = get(API, params, as_json=True, max_age_hours=24)
    return data.get("results", [])


def receipts_for(results: list[dict], name: str | None, party: str) -> float | None:
    """Receipts for the candidate whose FEC name contains the nominee's surname."""
    if not name:
        return None
    sn = surname(name).upper()
    for r in results:
        if sn and sn in (r.get("name") or "").upper():
            return float(r.get("receipts") or 0)
    return None
