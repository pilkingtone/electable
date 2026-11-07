"""Estimate model error terms from history.

Polling error (538 raw_polls.csv, 1998-2023, CC BY 4.0):
  For each Senate / governor / House race with a D-vs-R top two, average the
  polls taken in the final 21 days and compare with the result. Errors are
  split into a cycle-wide shared component (polls missing together, e.g.
  2016/2020) and a race-specific residual.

Fundamentals error (MEDSL, optional - needs network or cached files):
  Senate: result ~ year effect + b * state presidential lean.
  House:  result ~ year effect + b * same district's previous result.

Writes config/calibrated.json (consumed by config.model_config) and
web/data/calibration.json (the historical-error panel).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from .config import CONFIG_DIR, RAW_DIR, WEB_DATA_DIR

TYPES = {"Sen-G": "senate", "Gov-G": "governor", "House-G": "house"}


def _two_party(a, b):
    return 100 * (a - b) / (a + b)


def poll_errors(raw: pd.DataFrame, window: int = 21) -> pd.DataFrame:
    d = raw[(raw["time_to_election"] <= window)
            & (raw["cand1_party"] == "DEM") & (raw["cand2_party"] == "REP")].copy()
    d["poll"] = _two_party(d["cand1_pct"], d["cand2_pct"])
    d["actual"] = _two_party(d["cand1_actual"], d["cand2_actual"])
    g = d.groupby(["race", "cycle", "type_simple", "location"], as_index=False).agg(
        poll=("poll", "mean"), actual=("actual", "first"), n_polls=("poll", "size"))
    g["error"] = g["poll"] - g["actual"]          # + means polls overstated Democrats
    return g


def _fit_fe(y: pd.Series, x: pd.Series, year: pd.Series) -> tuple[float, float, np.ndarray]:
    """OLS of y on x with year fixed effects (via within-year demeaning)."""
    yd = y - y.groupby(year).transform("mean")
    xd = x - x.groupby(year).transform("mean")
    b = float((xd * yd).sum() / (xd ** 2).sum())
    resid = (yd - b * xd).to_numpy()
    dof = len(y) - year.nunique() - 1
    return b, float(np.sqrt((resid ** 2).sum() / max(dof, 1))), resid


MODERN_FROM = 2004


def _prior_sd(resid) -> float:
    """Root-mean-square residual (the fixed-effects fit already demeans by year).

    This must be the plain SD, blowouts included: the simulation draws Student-t
    errors scaled to unit variance, and inverse-variance weighting is only
    MSE-optimal with the true variance. (A robust MAD-based estimate was used
    until Nov 2026; it understated the prior's error and over-weighted
    fundamentals: Senate 1998-2022 data put the optimal poll weight at 0.83,
    against the 0.64 that estimate implied.)
    """
    r = np.asarray(resid, float)
    return float(np.sqrt(np.mean(r ** 2)))


def history_errors(hist: pd.DataFrame) -> dict:
    """Prior residual SDs from long-run race history (ElectIt: House 1946-, Senate 1954-).

    House: margin ~ year effect + b * same district's previous margin (skipping
    redistricting years). Senate: margin ~ year effect + b * the state's most
    recent earlier Senate margin. Uncontested races (|margin| > 90) are dropped.
    """
    out = {}
    h = hist[(hist["margin"].abs() < 60) & (hist["year"] >= MODERN_FROM)]
    house = h[h["chamber"] == "house"].dropna(subset=["district"])
    if len(house):
        prev = house[["year", "state", "district", "margin"]].copy()
        prev["year"] += 2
        m = house.merge(prev.rename(columns={"margin": "prev"}), on=["year", "state", "district"])
        m = m[(m["year"] % 10 != 2) & (m["prev"].abs() < 60)]   # new maps take effect in years ending in 2
        if len(m) > 100:
            b, _, resid = _fit_fe(m["margin"], m["prev"], m["year"])
            sd = _prior_sd(resid)
            out["house"] = {"lean_weight": b, "race_sd": sd, "n": len(m), "residuals": resid.tolist(),
                            "years": f"{int(m['year'].min())}-{int(m['year'].max())}"}
    sen = h[h["chamber"] == "senate"].sort_values("year")
    if len(sen):
        sen = sen.groupby(["year", "state"], as_index=False)["margin"].mean()
        sen["prev"] = sen.groupby("state")["margin"].shift(1)
        m = sen.dropna(subset=["prev"])
        if len(m) > 50:
            b, _, resid = _fit_fe(m["margin"], m["prev"], m["year"])
            sd = _prior_sd(resid)
            out["senate"] = {"lean_weight": b, "race_sd": sd, "n": len(m), "residuals": resid.tolist(),
                             "years": f"{int(m['year'].min())}-{int(m['year'].max())}"}
    return out


def fundamentals_errors() -> dict:
    """How far results land from the model's prior, 2004 onward.

    Senate: MEDSL results against the state's presidential lean (the model's
    own predictor), so both the error size and the lean weight are estimated.
    House: ElectIt's district results against the district's previous result
    (no presidential-by-district history is freely downloadable), an upper bound
    for a PVI-based prior; only the error size is used.
    """
    from .config import PROCESSED_DIR
    from .sources import harvard, medsl
    out: dict = {}
    sources = []
    try:
        pres = medsl.two_party(medsl.load("president"), ["year", "state_po"])
        nat = pres.groupby("year")[["dem", "rep"]].sum()
        pres["lean"] = pres["margin"] - pres["year"].map(_two_party(nat["dem"], nat["rep"]))
        sen = medsl.two_party(medsl.load("senate"), ["year", "state_po", "special"])
        sen = sen[(sen["dem"] > 0) & (sen["rep"] > 0) & (sen["year"] >= MODERN_FROM)].copy()
        sen["pres_year"] = sen["year"] - (sen["year"] % 4)
        sen = sen.merge(pres[["year", "state_po", "lean"]].rename(columns={"year": "pres_year"}),
                        on=["pres_year", "state_po"])
        b, _, resid = _fit_fe(sen["margin"], sen["lean"], sen["year"])
        out["senate"] = {"lean_weight": b, "race_sd": _prior_sd(resid), "n": len(sen),
                         "residuals": resid.tolist(), "lean_is_pres": True,
                         "years": f"{int(sen['year'].min())}-{int(sen['year'].max())}"}
        sources.append("MIT Election Data + Science Lab (Senate vs presidential lean)")
    except Exception as e:  # noqa: BLE001
        out["senate_error"] = str(e)
    path = PROCESSED_DIR / "electit_history.csv"
    hist = pd.read_csv(path) if path.exists() else harvard.load_history()
    if not hist.empty:
        h = history_errors(hist)
        if "house" in h:
            out["house"] = h["house"]
            sources.append("ElectIt results history (House vs previous result)")
    else:
        out["house_error"] = "no House results history available"
    out["source"] = "; ".join(sources)
    return out


def backtest(races: pd.DataFrame, cyc_mean: pd.Series, df: int = 5) -> dict:
    """Leave-one-cycle-out check of poll-based win probabilities.

    For each cycle, error sizes are estimated from the *other* cycles, then
    P(Dem win) = T_df(poll_avg / total_sd). Reports Brier score and a
    reliability table (predicted vs observed win rate).
    """
    from math import sqrt
    try:
        from scipy.stats import t as student_t
        cdf = lambda z: student_t.cdf(z * sqrt(df / (df - 2)), df)  # noqa: E731
    except ImportError:  # normal approximation
        from math import erf
        cdf = lambda z: 0.5 * (1 + erf(z / sqrt(2)))  # noqa: E731
    preds = []
    for c in sorted(races["cycle"].unique()):
        train = races[races["cycle"] != c]
        tm = cyc_mean.drop(c, errors="ignore")
        shared = float(np.sqrt((tm ** 2).mean()))
        for t in TYPES:
            tr = train[train["type_simple"] == t]
            idio = float(np.sqrt(((tr["error"] - tr["cycle"].map(tm).fillna(0)) ** 2).mean()))
            sd = np.sqrt(shared ** 2 + idio ** 2)
            for _, r in races[(races["cycle"] == c) & (races["type_simple"] == t)].iterrows():
                preds.append((TYPES[t], float(cdf(r["poll"] / sd)), float(r["actual"] > 0)))
    p = pd.DataFrame(preds, columns=["chamber", "p", "won"])
    bins = pd.cut(p["p"], [0, .1, .3, .5, .7, .9, 1.0], include_lowest=True)
    rel = p.groupby(bins, observed=True).agg(predicted=("p", "mean"), observed=("won", "mean"), n=("p", "size"))
    return {"brier": round(float(((p["p"] - p["won"]) ** 2).mean()), 4),
            "n": int(len(p)),
            "reliability": [{"bin": str(i), "predicted": round(r.predicted, 3),
                             "observed": round(r.observed, 3), "n": int(r.n)} for i, r in rel.iterrows()]}


def _hist(values, lo=-20, hi=20, width=1.0):
    v = np.clip(np.asarray(values, float), lo, hi - 1e-9)
    edges = np.arange(lo, hi + width, width)
    c, _ = np.histogram(v, edges)
    return [{"x0": float(edges[i]), "x1": float(edges[i + 1]), "n": int(c[i])} for i in range(len(c))]


def recent_cycle_errors() -> tuple[pd.DataFrame, list[dict]]:
    """Polling errors from cycles Electable forecast itself (538's data ends in 2022):
    each race's final polling average (data/final/forecast.json) against its result
    (web/data/results.json), plus the generic ballot against the House vote."""
    from .config import DATA_DIR
    fpath, rpath = DATA_DIR / "final" / "forecast.json", WEB_DATA_DIR / "results.json"
    if not (fpath.exists() and rpath.exists()):
        return pd.DataFrame(), []
    f, r = json.loads(fpath.read_text()), json.loads(rpath.read_text())
    cycle = int(f["election_date"][:4])
    rows = []
    for ch, t in (("senate", "Sen-G"), ("governor", "Gov-G")):
        for race in f["chambers"].get(ch, {}).get("races", []):
            res = r["races"].get(race["id"], {})
            if race.get("poll_n") and race.get("poll_avg") is not None and res.get("margin") is not None:
                rows.append({"race": f"{cycle}_{t}_{race['state']}", "cycle": cycle, "type_simple": t,
                             "location": race["state"], "poll": race["poll_avg"], "actual": res["margin"],
                             "n_polls": race["poll_n"], "error": race["poll_avg"] - res["margin"]})
    generic = []
    rep = r.get("chambers", {}).get("house", {}).get("reported", {})
    g = (f.get("national") or {}).get("generic_avg")
    if g is not None and rep.get("D", {}).get("pct") and rep.get("R", {}).get("pct"):
        actual = _two_party(rep["D"]["pct"], rep["R"]["pct"])
        generic.append({"cycle": cycle, "error": g - actual})
    return pd.DataFrame(rows), generic


def run(include_recent: bool = True, write: bool = True) -> dict:
    """include_recent=False calibrates on 538's 1998-2022 history only (for
    out-of-sample checks of a cycle Electable itself forecast)."""
    raw_path = RAW_DIR / "538" / "raw_polls.csv"
    raw = pd.read_csv(raw_path, low_memory=False)
    errs = poll_errors(raw)
    recent, recent_generic = recent_cycle_errors() if include_recent else (pd.DataFrame(), [])
    if not recent.empty:
        errs = pd.concat([errs, recent], ignore_index=True)

    overrides: dict = {"polls": {"systematic_error_sd": {}}, "national": {}, "prior": {
        "lean_weight": {}, "race_sd": {}}}
    panel = {"generated_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds") + "Z",
             "source": "FiveThirtyEight pollster-ratings raw_polls.csv (CC BY 4.0)",
             "polls": {}, "cycles": [], "fundamentals": {}}

    races = errs[errs["type_simple"].isin(TYPES)].copy()
    # Shared error: the average miss across all race types in a cycle.
    cyc = races[races["cycle"] % 2 == 0].groupby("cycle")["error"].agg(["mean", "size"])
    cyc = cyc[cyc["size"] >= 15]
    shared_sd = float(np.sqrt((cyc["mean"] ** 2).mean()))
    overrides["national"]["shared_error_sd"] = round(shared_sd, 2)
    panel["cycles"] = [{"cycle": int(c), "bias": round(float(m), 2), "races": int(s)}
                       for c, (m, s) in cyc.iterrows()]

    races["resid"] = races["error"] - races["cycle"].map(cyc["mean"]).fillna(0)
    for t, ch in TYPES.items():
        sub = races[(races["type_simple"] == t) & races["cycle"].isin(cyc.index)]
        sd = float(np.sqrt((sub["resid"] ** 2).mean()))
        overrides["polls"]["systematic_error_sd"][ch] = round(sd, 2)
        panel["polls"][ch] = {
            "n_races": int(len(sub)), "rmse_total": round(float(np.sqrt((sub["error"] ** 2).mean())), 2),
            "sd_race_specific": round(sd, 2),
            "mean_abs_error": round(float(sub["error"].abs().mean()), 2),
            "called_correctly": round(float(((sub["poll"] > 0) == (sub["actual"] > 0)).mean()), 3),
            "hist": _hist(sub["error"]),
        }

    panel["backtest"] = backtest(races[races["cycle"].isin(cyc.index)], cyc["mean"])

    gb = errs[errs["type_simple"] == "House-G-US"]
    if recent_generic:
        gb = pd.concat([gb, pd.DataFrame([{**g, "type_simple": "House-G-US"} for g in recent_generic])],
                       ignore_index=True)
    if len(gb) >= 4:
        bias = float(gb["error"].mean())
        gsd = float(np.sqrt(((gb["error"] - bias) ** 2).mean()))
        overrides["national"]["generic_ballot_error_sd"] = round(gsd, 2)
        # Final generic-ballot polling has overstated Democrats in most cycles; the
        # model subtracts the historical average miss (see model.national_environment).
        overrides["national"]["generic_ballot_bias"] = round(bias, 2)
        panel["generic_ballot"] = {"n_cycles": int(len(gb)), "rmse": round(float(np.sqrt((gb["error"] ** 2).mean())), 2),
                                   "bias": round(bias, 2), "sd_after_bias": round(gsd, 2),
                                   "by_cycle": [{"cycle": int(c), "error": round(float(e), 2)}
                                                for c, e in zip(gb["cycle"], gb["error"])]}

    fund = fundamentals_errors()
    for ch in ("senate", "house"):
        if ch in fund:
            f = fund[ch]
            overrides["prior"]["race_sd"][ch] = round(f["race_sd"], 2)
            # Only a regression on presidential lean estimates the model's lean
            # weight; slopes on a previous race's margin are reported, not applied.
            if f.get("lean_is_pres"):
                overrides["prior"]["lean_weight"][ch] = round(min(max(f["lean_weight"], 0.3), 1.1), 3)
            panel["fundamentals"][ch] = {"race_sd": round(f["race_sd"], 2),
                                         "lean_weight": round(f["lean_weight"], 3),
                                         "years": f.get("years"),
                                         "n": f["n"], "hist": _hist(f["residuals"], -30, 30, 2)}
        elif f"{ch}_error" in fund:
            panel["fundamentals"][ch] = {"unavailable": fund[f"{ch}_error"]}
    if any("race_sd" in v for v in panel["fundamentals"].values()):
        panel["source"] += "; " + fund.get("source", "")
    for k in ("lean_weight", "race_sd"):
        if not overrides["prior"][k]:
            del overrides["prior"][k]
    if not overrides["prior"]:
        del overrides["prior"]

    if write:
        (CONFIG_DIR / "calibrated.json").write_text(json.dumps(
            {"generated_at": panel["generated_at"], "overrides": overrides}, indent=2))
        WEB_DATA_DIR.mkdir(parents=True, exist_ok=True)
        (WEB_DATA_DIR / "calibration.json").write_text(json.dumps(panel))
    return {"overrides": overrides, "panel": panel}
