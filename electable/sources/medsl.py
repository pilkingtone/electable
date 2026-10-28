"""MIT Election Data + Science Lab official results via Harvard Dataverse.

Used for (a) state presidential lean and (b) calibrating how far real results
land from the fundamentals prior. Datasets (CC0):
  President 1976-:  doi:10.7910/DVN/42MVDX
  Senate 1976-:     doi:10.7910/DVN/PEJ5QU
  House 1976-:      doi:10.7910/DVN/IG0UN2
"""
from __future__ import annotations

from io import BytesIO

import pandas as pd

from ..config import RAW_DIR, MANUAL_DIR
from ..http import get

DATAVERSE = "https://dataverse.harvard.edu/api"
DATASETS = {
    "president": "doi:10.7910/DVN/42MVDX",
    "senate": "doi:10.7910/DVN/PEJ5QU",
    "house": "doi:10.7910/DVN/IG0UN2",
}


def load(office: str) -> pd.DataFrame:
    """Return the MEDSL results table for an office, downloading if needed."""
    local = RAW_DIR / "medsl" / f"{office}.csv"
    if local.exists():
        return pd.read_csv(local, low_memory=False)
    meta = get(f"{DATAVERSE}/datasets/:persistentId/",
               {"persistentId": DATASETS[office]}, as_json=True, max_age_hours=24 * 7)
    files = meta["data"]["latestVersion"]["files"]
    # The results file is the largest tabular file in the dataset.
    tab = max((f for f in files if f["dataFile"].get("filename", "").endswith((".tab", ".csv"))),
              key=lambda f: f["dataFile"].get("filesize", 0))
    body = get(f"{DATAVERSE}/access/datafile/{tab['dataFile']['id']}",
               {"format": "original"}, binary=True, max_age_hours=24 * 7, timeout=120)
    sep = "\t" if body[:2000].count(b"\t") > body[:2000].count(b",") else ","
    df = pd.read_csv(BytesIO(body), sep=sep, low_memory=False)
    local.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(local, index=False)
    return df


def two_party(df: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    """Collapse candidate rows to D and R vote totals and a two-party margin."""
    d = df.copy()
    if "stage" in d.columns:
        d = d[d["stage"].astype(str).str.upper().isin(["GEN", "GENERAL"])]
    party = d["party_simplified"].astype(str).str.upper()
    d["dem"] = d["candidatevotes"].where(party == "DEMOCRAT", 0)
    d["rep"] = d["candidatevotes"].where(party == "REPUBLICAN", 0)
    g = d.groupby(keys, as_index=False)[["dem", "rep"]].sum()
    tot = g["dem"] + g["rep"]
    g = g[tot > 0].copy()
    g["margin"] = 100 * (g["dem"] - g["rep"]) / (g["dem"] + g["rep"])
    return g


def state_presidential_lean(year: int = 2024) -> tuple[pd.DataFrame, float]:
    """State two-party presidential margin relative to the nation, for ``year``.

    Falls back to data/manual/state_pres.csv (columns: state, dem_votes, rep_votes)
    when the MEDSL release does not yet include that year.
    """
    manual = MANUAL_DIR / "state_pres.csv"
    if manual.exists():
        m = pd.read_csv(manual)
        m = m.rename(columns={"dem_votes": "dem", "rep_votes": "rep"})
    else:
        p = load("president")
        p = p[p["year"] == year]
        if p.empty:
            raise ValueError(f"MEDSL president data has no {year} rows; "
                             f"add data/manual/state_pres.csv")
        m = two_party(p, ["state_po"]).rename(columns={"state_po": "state"})
    nat = 100 * (m["dem"].sum() - m["rep"].sum()) / (m["dem"].sum() + m["rep"].sum())
    m["margin"] = 100 * (m["dem"] - m["rep"]) / (m["dem"] + m["rep"])
    m["lean"] = m["margin"] - nat
    return m[["state", "margin", "lean"]], nat
