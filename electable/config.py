"""Paths, configuration loading and static geography."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
MANUAL_DIR = DATA_DIR / "manual"
PROCESSED_DIR = DATA_DIR / "processed"
WEB_DATA_DIR = ROOT / "web" / "data"

STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia",
}
NAME_TO_ABBR = {v.upper(): k for k, v in STATES.items()}

# Census regions, used for the regionally correlated error term.
REGION = {}
for _r, _states in {
    "Northeast": "CT ME MA NH RI VT NJ NY PA",
    "Midwest": "IL IN MI OH WI IA KS MN MO NE ND SD",
    "South": "DE FL GA MD NC SC VA DC WV AL KY MS TN AR LA OK TX",
    "West": "AZ CO ID MT NV NM UT WY AK CA HI OR WA",
}.items():
    for _s in _states.split():
        REGION[_s] = _r


def _load(name: str) -> dict:
    with open(CONFIG_DIR / name) as f:
        return json.load(f)


def races_config() -> dict:
    return _load("races_2026.json")


def model_config() -> dict:
    """Model parameters, with calibrated values (if any) layered on top."""
    cfg = _load("model.json")
    cal_path = CONFIG_DIR / "calibrated.json"
    if cal_path.exists():
        _deep_update(cfg, json.loads(cal_path.read_text()).get("overrides", {}))
    return cfg


def _deep_update(base: dict, upd: dict) -> None:
    for k, v in upd.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_update(base[k], v)
        else:
            base[k] = v


def race_id(chamber: str, state: str, district: str | int | None = None) -> str:
    if chamber == "house":
        return f"house-{state}-{int(district):02d}"
    return f"{chamber}-{state}"
