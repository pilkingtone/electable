"""The Electable forecast model.

For every race, margin = Democratic minus Republican two-party share (points).

1. National environment N: a precision-weighted blend of the generic-ballot
   polling average and a fundamentals prior (last House vote + the midterm
   penalty for the president's party, adjusted by presidential approval).

2. Race prior: lean_weight * lean + N + incumbency + fundraising, where lean is
   the state/district's 2024 presidential margin relative to the nation.

3. Race polls: weighted, trend-adjusted average (see polls.py).

4. Combination: inverse-variance weighting of prior and polls. Error terms are
   split into parts that are shared across races (national environment error,
   systematic polling error, regional error) and parts that are race-specific,
   so simulated outcomes are correlated the way real elections are.

5. Simulation: Monte Carlo draws with fat (Student-t) tails give race win
   probabilities, seat distributions, chamber control and tipping points.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import REGION


@dataclass
class National:
    mean: float
    sd: float
    generic_avg: float | None
    generic_n: int
    fundamentals: float
    fundamentals_sd: float


def national_environment(generic: dict | None, net_approval: float | None,
                         prior_house_margin: float, president_party: str,
                         cfg: dict) -> National:
    nc = cfg["national"]
    sign = 1.0 if president_party == "R" else -1.0     # + favours Democrats
    fund = prior_house_margin + sign * nc["midterm_penalty"]
    fund_sd = nc["fundamentals_sd"]
    if net_approval is not None:
        fund -= sign * nc["approval_coef"] * net_approval
    else:
        fund_sd *= 1.5
    if generic is None:
        return National(fund, fund_sd, None, 0, fund, fund_sd)
    g_var = nc["generic_ballot_error_sd"] ** 2 + generic["sampling_sd"] ** 2
    f_var = fund_sd ** 2
    mean = (fund / f_var + generic["mean"] / g_var) / (1 / f_var + 1 / g_var)
    sd = (1 / f_var + 1 / g_var) ** -0.5
    return National(mean, sd, generic["mean"], generic["n_polls"], fund, fund_sd)


def race_prior(row: pd.Series, nat: National, cfg: dict) -> float:
    pc = cfg["prior"]
    ch = row["chamber"]
    prior = pc["lean_weight"][ch] * float(row["lean"]) + nat.mean
    inc = row.get("incumbent_party")
    if inc in ("D", "R"):
        prior += pc["incumbency_bonus"][ch] * (1 if inc == "D" else -1)
    d, r = row.get("dem_receipts"), row.get("rep_receipts")
    if pd.notna(d) and pd.notna(r) and d > 0 and r > 0:
        prior += float(np.clip(pc["fundraising_coef"] * np.log(d / r),
                               -pc["fundraising_cap"], pc["fundraising_cap"]))
    return prior


def combine(races: pd.DataFrame, poll_avgs: dict, nat: National, cfg: dict) -> pd.DataFrame:
    """Per-race mean and the loadings on each error component."""
    out = races.copy()
    shared = cfg["national"]["shared_error_sd"]
    rows = []
    for _, r in out.iterrows():
        ch = r["chamber"]
        prior = race_prior(r, nat, cfg)
        v_prior_idio = cfg["prior"]["race_sd"][ch] ** 2
        pa = poll_avgs.get(r["race_id"])
        if pa is not None:
            v_poll_idio = cfg["polls"]["systematic_error_sd"][ch] ** 2 + pa["sampling_sd"] ** 2
            v_prior = v_prior_idio + nat.sd ** 2
            v_poll = v_poll_idio + shared ** 2
            w = v_prior / (v_prior + v_poll)
            mean = (1 - w) * prior + w * pa["mean"]
        else:
            v_poll_idio, w, mean = 0.0, 0.0, prior
        idio = np.sqrt((1 - w) ** 2 * v_prior_idio + w ** 2 * v_poll_idio)
        rows.append({"prior": prior, "poll_avg": pa["mean"] if pa else None,
                     "poll_n": pa["n_polls"] if pa else 0,
                     "last_poll": pa["last_poll"] if pa else None,
                     "w_polls": w, "mean": mean,
                     "load_nat": 1 - w, "load_poll": w,
                     "idio_sd": max(idio, cfg["min_race_sd"])})
    return pd.concat([out.reset_index(drop=True), pd.DataFrame(rows)], axis=1)


def _t(rng, df, size):
    """Student-t draws scaled to unit variance."""
    return rng.standard_t(df, size) * np.sqrt((df - 2) / df)


def simulate(est: pd.DataFrame, nat: National, cfg: dict, n: int | None = None,
             seed: int | None = None) -> np.ndarray:
    """Return an (n_sims, n_races) array of simulated margins."""
    n = n or cfg["simulations"]
    rng = np.random.default_rng(cfg["seed"] if seed is None else seed)
    df = cfg["t_df"]
    k = len(est)
    d_nat = _t(rng, df, (n, 1)) * nat.sd
    d_poll = _t(rng, df, (n, 1)) * cfg["national"]["shared_error_sd"]
    regions = sorted(set(REGION.values()))
    reg = rng.normal(0, cfg["regional_sd"], (n, len(regions)))
    ridx = np.array([regions.index(REGION.get(s, "South")) for s in est["state"]])
    eps = _t(rng, df, (n, k)) * est["idio_sd"].to_numpy()
    sims = (est["mean"].to_numpy()
            + d_nat * est["load_nat"].to_numpy()
            + d_poll * est["load_poll"].to_numpy()
            + reg[:, ridx] + eps)
    unc = est.get("uncontested", pd.Series([""] * k)).fillna("").to_numpy()
    sims[:, unc == "D"] = 100.0
    sims[:, unc == "R"] = -100.0
    return sims


def _str_or_none(v):
    return v if isinstance(v, str) and v else None


def summarize(est: pd.DataFrame, sims: np.ndarray, chamber_cfg: dict) -> dict:
    """Race-level and chamber-level results for one chamber."""
    dem_win = sims > 0
    counts = dem_win & est["dem_caucus"].to_numpy(bool)
    others = dem_win & ~est["dem_caucus"].to_numpy(bool)
    base_d = chamber_cfg.get("not_up", {}).get("D", 0)
    seats_d = base_d + counts.sum(1)
    seats_other = others.sum(1)
    thr = chamber_cfg.get("control_threshold_d")

    races = []
    q = np.percentile(sims, [10, 50, 90], axis=0)
    for i, (_, r) in enumerate(est.iterrows()):
        races.append({
            "id": r["race_id"], "state": r["state"],
            "district": None if pd.isna(r.get("district")) else int(r["district"]),
            "holder": r["holder"], "dem": r.get("dem_name"), "rep": r.get("rep_name"),
            "dem_label": r.get("dem_label", "D"), "incumbent_party": _str_or_none(r.get("incumbent_party")),
            "uncontested": _str_or_none(r.get("uncontested")),
            "p_dem": float(dem_win[:, i].mean()),
            "mean": round(float(r["mean"]), 2), "median": round(float(q[1, i]), 2),
            "lo80": round(float(q[0, i]), 2), "hi80": round(float(q[2, i]), 2),
            "prior": round(float(r["prior"]), 2),
            "poll_avg": None if r["poll_avg"] is None or pd.isna(r["poll_avg"]) else round(float(r["poll_avg"]), 2),
            "poll_n": int(r["poll_n"]), "last_poll": r["last_poll"],
            "w_polls": round(float(r["w_polls"]), 3),
            "lean": round(float(r["lean"]), 2),
            "ratings": r.get("ratings") or [],
            "source_url": r.get("source_url"),
        })

    out = {"races": races}
    total = seats_d
    vals, cnt = np.unique(total, return_counts=True)
    out["dem_seats"] = {
        "mean": float(total.mean()), "median": float(np.median(total)),
        "lo80": float(np.percentile(total, 10)), "hi80": float(np.percentile(total, 90)),
        "dist": [{"seats": int(v), "p": float(c / len(total))} for v, c in zip(vals, cnt)],
    }
    out["base_dem"] = base_d
    out["base_rep"] = chamber_cfg.get("not_up", {}).get("R", 0)
    out["other_seats_mean"] = float(seats_other.mean())
    if thr:
        out["threshold"] = thr
        out["p_dem_control"] = float((seats_d >= thr).mean())
        # Tipping point: the race that would supply the decisive seat when races
        # are ordered from most to least Democratic in that simulation.
        need = thr - base_d - 1
        if 0 <= need < sims.shape[1]:
            order = np.argsort(-np.where(est["dem_caucus"].to_numpy(bool), sims, -1e9), axis=1)
            tip = order[:, need]
            ids, c = np.unique(tip, return_counts=True)
            out["tipping"] = sorted(
                [{"id": est.iloc[j]["race_id"], "p": float(cc / len(tip))} for j, cc in zip(ids, c)],
                key=lambda x: -x["p"])[:15]
    return out
