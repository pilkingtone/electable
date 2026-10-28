"""Poll cleaning and weighted averaging.

Each poll is converted to a two-party margin (D - R) and weighted by
  recency     0.5 ** (age / half_life)
  sample      sqrt(n / reference_sample), capped at 2
  population  LV > RV > A
  pollster    from 538's published numeric grades (0-3), unknown = middling
  sponsor     partisan/campaign polls down-weighted and shifted
A pollster's polls share a total weight budget (sqrt of count) so one firm
releasing weekly trackers cannot dominate a race.
"""
from __future__ import annotations

import re
from datetime import date

import numpy as np
import pandas as pd

from .config import RAW_DIR


def _norm(name: str) -> str:
    name = re.sub(r"\[[^\]]*\]|\(.*?\)", "", str(name)).lower()
    return re.sub(r"[^a-z0-9]+", " ", name).strip()


def load_pollster_grades() -> dict[str, float]:
    path = RAW_DIR / "538" / "pollster-ratings-combined.csv"
    if not path.exists():
        return {}
    df = pd.read_csv(path)
    return {_norm(p): g for p, g in zip(df["pollster"], df["numeric_grade"]) if pd.notna(g)}


def pollster_weight(name: str, grades: dict[str, float]) -> float:
    n = _norm(name)
    g = grades.get(n)
    if g is None:
        # Wikipedia often appends partners: "Emerson College/The Hill".
        for part in re.split(r"/| for | and ", n):
            g = grades.get(part.strip())
            if g is not None:
                break
    if g is None:
        return 0.6
    return 0.4 + 0.2 * float(g)       # grade 0 -> 0.4, grade 3 -> 1.0


def two_party_margin(dem, rep):
    dem, rep = np.asarray(dem, float), np.asarray(rep, float)
    return 100 * (dem - rep) / (dem + rep)


def _sponsor(p) -> str | None:
    """Normalise the partisan column: True (unknown side), 'D', 'R' or None."""
    if p is None or (isinstance(p, float) and np.isnan(p)) or p is False:
        return None
    s = str(p).strip().upper()
    if s in ("", "FALSE", "NAN", "NONE", "0"):
        return None
    if s.startswith("D") or s.startswith("R"):
        return s[0]
    return "?"


def weights(df: pd.DataFrame, today: date, cfg: dict, grades: dict) -> np.ndarray:
    pc = cfg["polls"]
    age = (pd.Timestamp(today) - pd.to_datetime(df["end_date"])).dt.days.clip(lower=0).to_numpy()
    w = 0.5 ** (age / pc["half_life_days"])
    n = pd.to_numeric(df.get("sample"), errors="coerce").fillna(pc["reference_sample"]).to_numpy()
    w *= np.minimum(np.sqrt(n / pc["reference_sample"]), 2.0)
    pop = df.get("population", pd.Series([None] * len(df))).fillna("RV").astype(str)
    w *= pop.map(pc["population_weight"]).fillna(0.75).to_numpy()
    w *= np.array([pollster_weight(p, grades) for p in df["pollster"]])
    sponsored = np.array([_sponsor(p) is not None for p in df.get("partisan", [None] * len(df))])
    w *= np.where(sponsored, pc["partisan_sponsor_weight"], 1.0)
    w[age > pc["max_age_days"]] = 0
    # Per-pollster budget: k polls from one firm get sqrt(k) total influence.
    key = df["pollster"].map(_norm).to_numpy()
    for k in set(key):
        idx = key == k
        tot = w[idx].sum()
        if tot > 0:
            share = w[idx] / tot
            w[idx] = share * np.sqrt(idx.sum()) * w[idx].mean()
    return w


def national_trend(gb: pd.DataFrame, cfg: dict, grades: dict, bandwidth: int = 14):
    """Kernel-smoothed generic ballot as a function of date (None if too sparse)."""
    if gb is None or len(gb) < 10:
        return None
    m = two_party_margin(gb["dem_pct"], gb["rep_pct"])
    d = pd.to_datetime(gb["end_date"]).to_numpy()
    base = np.array([pollster_weight(p, grades) for p in gb["pollster"]])

    def at(when) -> float:
        dt = (d - np.datetime64(pd.Timestamp(when))).astype("timedelta64[D]").astype(float)
        k = np.exp(-0.5 * (dt / bandwidth) ** 2) * base * (dt <= 0)
        return float((k * m).sum() / k.sum()) if k.sum() > 1e-9 else float("nan")
    return at


def average(df: pd.DataFrame, today: date, cfg: dict, grades: dict, trend=None) -> dict | None:
    """Weighted polling average for one race. Returns None if no usable polls."""
    if df is None or df.empty:
        return None
    df = df[pd.to_datetime(df["end_date"]) <= pd.Timestamp(today)].copy()
    if df.empty:
        return None
    w = weights(df, today, cfg, grades)
    if w.sum() <= 0:
        return None
    m = two_party_margin(df["dem_pct"], df["rep_pct"])
    shift = cfg["polls"]["partisan_sponsor_shift"]
    side = [_sponsor(p) for p in df.get("partisan", [None] * len(df))]
    m = m - np.array([shift if s == "D" else -shift if s == "R" else 0 for s in side])
    if trend is not None and cfg["polls"]["national_trend_adjust"]:
        now = trend(today)
        adj = np.array([now - trend(e) for e in df["end_date"]])
        m = m + np.nan_to_num(adj)
    wn = w / w.sum()
    n = pd.to_numeric(df.get("sample"), errors="coerce").fillna(cfg["polls"]["reference_sample"]).to_numpy()
    # Sampling variance of a margin is ~ 1e4 / n (in points^2) for p near 0.5.
    samp_var = float((wn ** 2 * (1e4 / n)).sum())
    spread = float(np.sqrt((wn * (m - (wn * m).sum()) ** 2).sum()))
    return {"mean": float((wn * m).sum()), "sampling_sd": float(np.sqrt(samp_var)),
            "spread": spread, "n_polls": int((w > 0).sum()),
            "n_eff": float(w.sum() ** 2 / (w ** 2).sum()),
            "last_poll": str(pd.to_datetime(df["end_date"]).max().date())}
