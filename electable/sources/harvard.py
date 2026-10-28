"""Harvard data: Dataverse datasets and Gary King et al.'s ElectIt forecasts.

* Harvard Dataverse: every file of every dataset listed in config/harvard.json
  is mirrored to data/raw/dataverse/<doi>/ (original format where available).
* ElectIt (https://gking.harvard.edu/electit/, King, Ebanks & Katz): race-level
  2026 House and Senate forecasts and their historical inputs (House since
  1946, Senate since 1954). Files are fetched from configured URLs or dropped
  into data/raw/electit/ by hand.

ElectIt's file layout isn't hard-coded. Columns are recognised by name
(state / district / office / probability), and config/harvard.json's
`electit.column_map` overrides the guesses. Anything unrecognised is reported
rather than silently dropped.
"""
from __future__ import annotations

import json
import re
from io import BytesIO
from pathlib import Path

import pandas as pd

from ..config import CONFIG_DIR, NAME_TO_ABBR, RAW_DIR, STATES, race_id
from ..http import get

DATAVERSE = "https://dataverse.harvard.edu/api"
ELECTIT_DIR = RAW_DIR / "electit"
DATAVERSE_DIR = RAW_DIR / "dataverse"
TABULAR = (".csv", ".tsv", ".tab", ".txt", ".xlsx", ".xls", ".json", ".dta", ".rds")


def config() -> dict:
    path = CONFIG_DIR / "harvard.json"
    return json.loads(path.read_text()) if path.exists() else {"dataverse_datasets": [], "electit": {}}


# ------------------------------------------------------------- Dataverse mirror

def _doi_dir(doi: str) -> Path:
    return DATAVERSE_DIR / re.sub(r"[^A-Za-z0-9]+", "_", doi.replace("doi:", ""))


def mirror_dataset(doi: str) -> list[Path]:
    """Download every file in a Dataverse dataset. Returns the local paths."""
    meta = get(f"{DATAVERSE}/datasets/:persistentId/", {"persistentId": doi},
               as_json=True, max_age_hours=24)
    out_dir = _doi_dir(doi)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "dataset.json").write_text(json.dumps(meta, indent=1))
    paths = []
    for f in meta["data"]["latestVersion"]["files"]:
        df = f["dataFile"]
        # Tabular ingests are served as .tab unless we ask for the original upload.
        name = df.get("originalFileName") or df["filename"]
        params = {"format": "original"} if df.get("originalFileFormat") else None
        body = get(f"{DATAVERSE}/access/datafile/{df['id']}", params,
                   binary=True, max_age_hours=24 * 7, timeout=300)
        p = out_dir / name
        p.write_bytes(body)
        paths.append(p)
    return paths


def mirror_all() -> tuple[list[dict], list[str]]:
    """Mirror every configured dataset and ElectIt URL. Returns (sources, warnings)."""
    cfg, sources, warnings = config(), [], []
    for ds in cfg.get("dataverse_datasets", []):
        try:
            paths = mirror_dataset(ds["doi"])
            sources.append({"name": f"Harvard Dataverse: {ds['name']}",
                            "url": "https://doi.org/" + ds["doi"].replace("doi:", ""),
                            "files": len(paths)})
            if ds.get("role") == "electit_history":
                for p in paths:
                    if p.suffix.lower() in TABULAR:
                        dest = ELECTIT_DIR / "history" / p.name
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        dest.write_bytes(p.read_bytes())
        except Exception as e:  # noqa: BLE001
            warnings.append(f"Dataverse {ds['doi']} ({ds['name']}): {e}")
    ELECTIT_DIR.mkdir(parents=True, exist_ok=True)
    for url in cfg.get("electit", {}).get("urls", []):
        try:
            body = get(url, binary=True, max_age_hours=6, timeout=120)
            name = url.rstrip("/").split("/")[-1].split("?")[0] or "electit_download"
            (ELECTIT_DIR / name).write_bytes(body)
            sources.append({"name": f"ElectIt: {name}", "url": url})
        except Exception as e:  # noqa: BLE001
            warnings.append(f"ElectIt {url}: {e}")
    return sources, warnings


# ------------------------------------------------------------- reading files

def read_table(path: Path) -> pd.DataFrame:
    suf = path.suffix.lower()
    if suf in (".xlsx", ".xls"):
        return pd.read_excel(path)
    if suf == ".json":
        data = json.loads(path.read_text())
        if isinstance(data, dict):     # {"races": [...]} or {"senate": [...], "house": [...]}
            lists = {k: v for k, v in data.items() if isinstance(v, list)}
            if len(lists) == 1:
                return pd.DataFrame(next(iter(lists.values())))
            return pd.concat([pd.DataFrame(v).assign(_chamber_key=k) for k, v in lists.items()],
                             ignore_index=True)
        return pd.DataFrame(data)
    if suf == ".dta":
        return pd.read_stata(path)
    body = path.read_bytes()
    sep = "\t" if suf in (".tsv", ".tab") or body[:4000].count(b"\t") > body[:4000].count(b",") else ","
    return pd.read_csv(BytesIO(body), sep=sep, low_memory=False)


def _find(cols: list[str], patterns: list[str]) -> str | None:
    low = {c: re.sub(r"[^a-z0-9]+", "_", c.lower()).strip("_") for c in cols}
    for pat in patterns:
        rx = re.compile(pat)
        for c, lc in low.items():
            if rx.fullmatch(lc):
                return c
    return None


COLUMN_PATTERNS = {
    "state": [r"state_?po", r"state_?abb?r?e?v?", r"st", r"state", r"state_?name"],
    "district": [r"district", r"cd", r"dist", r"district_?num(ber)?", r"seat"],
    "chamber": [r"chamber", r"office", r"race_?type", r"body", r"_chamber_key"],
    "p_dem": [r"p_?(dem|d)(_?win)?", r"prob(ability)?_?(dem|d)(_?win)?", r"(dem|d)_?win_?prob(ability)?",
              r"pr_?(dem|d)", r"(dem|d)_?prob"],
    "p_rep": [r"p_?(rep|r)(_?win)?", r"prob(ability)?_?(rep|r)(_?win)?", r"(rep|r)_?win_?prob(ability)?"],
    "dem_share": [r"(pred|forecast|expected|mean)_?(dem|d)_?(vote_?)?share", r"(dem|d)_?(vote_?)?share(_?(pred|mean))?",
                  r"(dem|d)_?two_?party"],
    "lo": [r"(dem|d)_?(share_?)?(lo|lower|q05|q10|p05|p10)(80|90|95)?", r"(lo|lower)(80|90|95)?"],
    "hi": [r"(dem|d)_?(share_?)?(hi|upper|q90|q95|p90|p95)(80|90|95)?", r"(hi|upper)(80|90|95)?"],
    "set": [r"set", r"model", r"forecast_?type", r"version", r"polls?"],
    "year": [r"year", r"cycle", r"election_?year"],
}


def detect_columns(df: pd.DataFrame, overrides: dict | None = None) -> dict:
    cols = [str(c) for c in df.columns]
    found = {k: _find(cols, pats) for k, pats in COLUMN_PATTERNS.items()}
    found.update({k: v for k, v in (overrides or {}).items() if v in cols})
    return {k: v for k, v in found.items() if v}


def _state_abbr(v) -> str | None:
    s = str(v).strip().upper()
    if s in STATES:
        return s
    return NAME_TO_ABBR.get(s)


def _chamber(v, district) -> str | None:
    s = str(v).strip().lower() if v is not None and not pd.isna(v) else ""
    if s.startswith(("sen", "s")) and not s.startswith("st"):
        return "senate"
    if s.startswith(("house", "h", "rep", "cong")):
        return "house"
    if s.startswith("gov"):
        return "governor"
    return None


def _prob(v) -> float | None:
    try:
        x = float(str(v).strip().rstrip("%"))
    except ValueError:
        return None
    if pd.isna(x):
        return None
    return x / 100 if x > 1 else x


def standardize_forecasts(df: pd.DataFrame, *, file_name: str = "", overrides: dict | None = None) -> tuple[pd.DataFrame, list[str]]:
    """ElectIt-style table -> race_id, set, p_dem, dem_share, lo, hi."""
    cols = detect_columns(df, overrides)
    notes = []
    if "state" not in cols or not ({"p_dem", "p_rep", "dem_share"} & cols.keys()):
        return pd.DataFrame(), [f"{file_name}: no state + probability/share columns recognised "
                                f"(columns: {', '.join(map(str, df.columns[:20]))}); set electit.column_map"]
    file_chamber = ("senate" if re.search(r"sen", file_name, re.I) else
                    "house" if re.search(r"house|cong", file_name, re.I) else None)
    rows = []
    for _, r in df.iterrows():
        st = _state_abbr(r[cols["state"]])
        if not st:
            continue
        dist = r[cols["district"]] if "district" in cols else None
        ch = _chamber(r[cols["chamber"]], dist) if "chamber" in cols else None
        ch = ch or file_chamber or ("house" if dist is not None and not pd.isna(dist) else "senate")
        if "year" in cols and pd.notna(r[cols["year"]]) and int(float(r[cols["year"]])) != 2026:
            continue
        p = _prob(r[cols["p_dem"]]) if "p_dem" in cols else None
        if p is None and "p_rep" in cols:
            pr = _prob(r[cols["p_rep"]])
            p = None if pr is None else 1 - pr
        share = None
        if "dem_share" in cols:
            share = _prob(r[cols["dem_share"]])
        if ch == "house":
            try:
                d = int(re.sub(r"\D", "", str(dist)) or 1)   # at-large -> 1
            except ValueError:
                continue
            rid = race_id("house", st, d if d > 0 else 1)
        else:
            rid = race_id(ch, st)
        rows.append({"race_id": rid, "chamber": ch,
                     "set": str(r[cols["set"]]) if "set" in cols else (Path(file_name).stem or "electit"),
                     "p_dem": p, "dem_share": share,
                     "lo": _prob(r[cols["lo"]]) if "lo" in cols else None,
                     "hi": _prob(r[cols["hi"]]) if "hi" in cols else None})
    out = pd.DataFrame(rows)
    if out.empty:
        notes.append(f"{file_name}: columns recognised ({cols}) but no 2026 rows parsed")
    return out, notes


def load_electit(directory: Path = ELECTIT_DIR) -> tuple[pd.DataFrame, list[str]]:
    """All ElectIt forecast files in a directory, standardized and stacked."""
    overrides = config().get("electit", {}).get("column_map", {})
    frames, notes = [], []
    if not directory.exists():
        return pd.DataFrame(), notes
    for p in sorted(directory.iterdir()):
        if not p.is_file() or p.suffix.lower() not in TABULAR:
            continue
        try:
            df, n = standardize_forecasts(read_table(p), file_name=p.name, overrides=overrides)
        except Exception as e:  # noqa: BLE001
            notes.append(f"{p.name}: could not read ({e})")
            continue
        notes += n
        if not df.empty:
            frames.append(df)
    if not frames:
        return pd.DataFrame(), notes
    out = pd.concat(frames, ignore_index=True)
    # p_dem from vote share when only shares are published: leave None (we don't
    # know ElectIt's error distribution), the UI then shows the share instead.
    return out.drop_duplicates(["race_id", "set"], keep="last"), notes


def load_history(directory: Path = ELECTIT_DIR / "history") -> pd.DataFrame:
    """Historical race results (House 1946-, Senate 1954-) as year, chamber, state,
    district, margin (D-R two-party points). Empty if none present."""
    if not directory.exists():
        return pd.DataFrame()
    frames = []
    for p in sorted(directory.iterdir()):
        if not p.is_file() or p.suffix.lower() not in TABULAR:
            continue
        try:
            df = read_table(p)
        except Exception:  # noqa: BLE001
            continue
        cols = [str(c) for c in df.columns]
        year = _find(cols, COLUMN_PATTERNS["year"])
        state = _find(cols, COLUMN_PATTERNS["state"])
        dist = _find(cols, COLUMN_PATTERNS["district"])
        ch = _find(cols, COLUMN_PATTERNS["chamber"])
        share = _find(cols, [r"(dem|d)_?(two_?party_?)?(vote_?)?share", r"(dem|d)_?vs", r"dvote_?share", r"v"])
        dv = _find(cols, [r"(dem|d)_?votes?", r"dvotes?"])
        rv = _find(cols, [r"(rep|r)_?votes?", r"rvotes?"])
        if not (year and state and (share or (dv and rv))):
            continue
        d = pd.DataFrame({"year": pd.to_numeric(df[year], errors="coerce"),
                          "state": df[state].map(_state_abbr),
                          "district": pd.to_numeric(df[dist], errors="coerce") if dist else None,
                          "chamber": [(_chamber(v, None) if ch else None) or
                                      ("senate" if re.search(r"sen", p.name, re.I) else "house")
                                      for v in (df[ch] if ch else [None] * len(df))]})
        if share:
            s = pd.to_numeric(df[share], errors="coerce")
            s = s.where(s <= 1, s / 100)
            d["margin"] = 100 * (2 * s - 1)
        else:
            a, b = pd.to_numeric(df[dv], errors="coerce"), pd.to_numeric(df[rv], errors="coerce")
            d["margin"] = 100 * (a - b) / (a + b)
        frames.append(d.dropna(subset=["year", "state", "margin"]))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
