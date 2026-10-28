"""Fetch -> processed inputs -> model -> web/data/*.json.

`fetch` writes data/processed/{races,polls,generic_ballot}.csv + meta.json.
`build` merges data/manual/* overrides on top, runs the model, and writes
web/data/forecast.json (and appends web/data/history.json).

Manual files (all optional except house_districts.csv for the House):
  data/manual/races.csv           race_id + any of: dem_name, rep_name, dem_label,
                                  dem_caucus, incumbent_party, uncontested, lean
  data/manual/polls.csv           race_id, pollster, end_date, sample, population,
                                  partisan, dem_pct, rep_pct, source_url
  data/manual/generic_ballot.csv  pollster, end_date, sample, population,
                                  partisan, dem_pct, rep_pct, source_url
  data/manual/approval.csv        pollster, end_date, sample, population, approve, disapprove
  data/manual/house_districts.csv state, district, holder, incumbent_running,
                                  dem_name, rep_name, pres_margin_2024, uncontested
  data/manual/state_pres.csv      state, dem_votes, rep_votes (2024 president)
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import model, polls as P
from .config import (MANUAL_DIR, PROCESSED_DIR, WEB_DATA_DIR, model_config,
                     races_config, race_id)
from .sources import fec, medsl, wikipedia

CHAMBERS = ("senate", "house", "governor")


def _log(msg: str) -> None:
    print(f"[electable] {msg}", flush=True)


def _read(path: Path) -> pd.DataFrame:
    return pd.read_csv(path) if path.exists() and path.stat().st_size else pd.DataFrame()


# ====================================================================== fetch

def fetch(out_dir: Path = PROCESSED_DIR) -> dict:
    rc = races_config()
    warnings, sources = [], []
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        lean_df, nat_pres = medsl.state_presidential_lean(2024)
        lean = dict(zip(lean_df["state"], lean_df["lean"]))
        sources.append({"name": "MIT Election Data + Science Lab (2024 president by state)",
                        "url": "https://doi.org/10.7910/DVN/42MVDX"})
    except Exception as e:  # noqa: BLE001
        lean, nat_pres = {}, None
        warnings.append(f"State presidential lean unavailable: {e}")

    rows, poll_frames = [], []
    for ch in ("senate", "governor"):
        for seat in rc[ch]["seats_up"]:
            st = seat["state"]
            rid = race_id(ch, st)
            row = {"race_id": rid, "chamber": ch, "state": st, "district": None,
                   "holder": seat["holder"], "lean": lean.get(st)}
            try:
                info = wikipedia.fetch_race(ch, st, seat.get("special", False))
                _log(f"{rid}: {len(info['polls'])} polls, {len(info['ratings'])} ratings")
                d, r = info["dem"], info["rep"]
                row.update({"dem_name": d and d["name"], "rep_name": r and r["name"],
                            "dem_label": d["party"] if d else None,
                            "source_url": info["url"],
                            "ratings": json.dumps(info["ratings"])})
                inc = info.get("incumbent")
                if inc:
                    sn = wikipedia.surname(inc).lower()
                    if d and sn == wikipedia.surname(d["name"]).lower():
                        row["incumbent_party"] = d["party"]
                    elif r and sn == wikipedia.surname(r["name"]).lower():
                        row["incumbent_party"] = "R"
                if d and not r:
                    row["uncontested"] = d["party"] if d["party"] == "D" else None
                elif r and not d:
                    row["uncontested"] = "R"
                if not len(info["nominees"]):
                    warnings.append(f"{rid}: no nominees found in infobox")
                if not info["polls"].empty:
                    poll_frames.append(info["polls"].assign(race_id=rid))
            except Exception as e:  # noqa: BLE001
                warnings.append(f"{rid}: Wikipedia fetch failed: {e}")
            rows.append(row)

    # FEC fundraising for Senate (federal) races.
    for row in rows:
        if row["chamber"] != "senate" or not row.get("dem_name"):
            continue
        try:
            res = fec.totals("S", row["state"])
            row["dem_receipts"] = fec.receipts_for(res, row.get("dem_name"), "D")
            row["rep_receipts"] = fec.receipts_for(res, row.get("rep_name"), "R")
        except Exception as e:  # noqa: BLE001
            warnings.append(f"{row['race_id']}: FEC fetch failed: {e}")
            break   # one failure usually means the API is unreachable/rate-limited
    else:
        sources.append({"name": "Federal Election Commission (OpenFEC candidate totals)",
                        "url": "https://api.open.fec.gov/developers/"})

    races = pd.DataFrame(rows)
    races.to_csv(out_dir / "races.csv", index=False)
    pd.concat(poll_frames).to_csv(out_dir / "polls.csv", index=False) if poll_frames else \
        (out_dir / "polls.csv").write_text("")
    if poll_frames or any("ratings" in r for r in rows):
        sources.append({"name": "Wikipedia race articles (polls cited to each pollster; Cook, Sabato, Inside Elections ratings)",
                        "url": "https://en.wikipedia.org/wiki/2026_United_States_elections"})

    try:
        gb = wikipedia.fetch_generic_ballot()
        gb.to_csv(out_dir / "generic_ballot.csv", index=False)
        _log(f"generic ballot: {len(gb)} polls")
        sources.append({"name": "Wikipedia: generic ballot polling",
                        "url": wikipedia.page_url(wikipedia.GENERIC_BALLOT_TITLE)})
    except Exception as e:  # noqa: BLE001
        warnings.append(f"Generic ballot fetch failed: {e}")
        (out_dir / "generic_ballot.csv").write_text("")

    house_margin = rc.get("national_house_margin_2024")
    if house_margin is None:
        try:
            h = medsl.load("house")
            h = medsl.two_party(h[h["year"] == 2024], ["year"])
            house_margin = float(h["margin"].iloc[0]) if len(h) else None
        except Exception as e:  # noqa: BLE001
            warnings.append(f"2024 House popular vote unavailable: {e}")

    meta = {"fetched_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds") + "Z",
            "national_pres_margin_2024": nat_pres, "national_house_margin_2024": house_margin,
            "warnings": warnings, "sources": sources}
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2))
    for w in warnings:
        _log("WARNING " + w)
    return meta


# ====================================================================== build

def _bool(v, default=False) -> bool:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "y", "t")


def load_inputs(processed: Path = PROCESSED_DIR, manual: Path = MANUAL_DIR) -> dict:
    meta = json.loads((processed / "meta.json").read_text()) if (processed / "meta.json").exists() else {}
    warnings = list(meta.get("warnings", []))
    races = _read(processed / "races.csv")
    nat_pres = meta.get("national_pres_margin_2024")

    # House districts come from the manual file (current, post-redistricting lines).
    hd = _read(manual / "house_districts.csv")
    if not hd.empty:
        if nat_pres is None:
            warnings.append("House lean needs the 2024 national presidential margin; House skipped.")
        else:
            hd = hd.assign(
                race_id=[race_id("house", s, d) for s, d in zip(hd["state"], hd["district"])],
                chamber="house",
                lean=hd["pres_margin_2024"] - nat_pres,
                dem_label="D",
                incumbent_party=[h if _bool(run) else None
                                 for h, run in zip(hd["holder"], hd.get("incumbent_running", [False] * len(hd)))])
            races = pd.concat([races, hd.drop(columns=["pres_margin_2024", "incumbent_running"],
                                              errors="ignore")], ignore_index=True)
    else:
        warnings.append("data/manual/house_districts.csv missing: House forecast not produced.")

    over = _read(manual / "races.csv")
    if not over.empty and not races.empty:
        races = races.set_index("race_id")
        for _, o in over.iterrows():
            for c, v in o.items():
                if c != "race_id" and pd.notna(v) and o["race_id"] in races.index:
                    if c not in races.columns:
                        races[c] = None
                    races.loc[o["race_id"], c] = v
        races = races.reset_index()

    if not races.empty:
        for col in ("dem_label", "incumbent_party", "uncontested", "dem_caucus",
                    "dem_receipts", "rep_receipts", "ratings", "source_url", "dem_name", "rep_name"):
            if col not in races.columns:
                races[col] = None
        races["dem_label"] = races["dem_label"].fillna("D")
        races["dem_caucus"] = [_bool(c, default=(lbl == "D"))
                               for c, lbl in zip(races["dem_caucus"], races["dem_label"])]
        races["ratings"] = [json.loads(r) if isinstance(r, str) and r else []
                            for r in races["ratings"]]
        races["uncontested"] = races["uncontested"].fillna("")
        races["incumbent_party"] = races["incumbent_party"].where(races["incumbent_party"].notna(), None)

    polls = pd.concat([_read(processed / "polls.csv"), _read(manual / "polls.csv")], ignore_index=True)
    gb = pd.concat([_read(processed / "generic_ballot.csv"), _read(manual / "generic_ballot.csv")],
                   ignore_index=True)
    appr = _read(manual / "approval.csv")
    if not appr.empty:
        appr = appr.rename(columns={"approve": "dem_pct", "disapprove": "rep_pct"})
    return {"meta": meta, "warnings": warnings, "races": races, "polls": polls,
            "generic": gb, "approval": appr}


def run_model(inp: dict, as_of: date, n_sims: int | None = None) -> dict:
    cfg = model_config()
    rc = races_config()
    grades = P.load_pollster_grades()
    warnings = list(inp["warnings"])
    gb = inp["generic"]
    trend = P.national_trend(gb, cfg, grades)
    generic = P.average(gb, as_of, cfg, grades) if not gb.empty else None
    if generic is None:
        warnings.append("No generic-ballot polls: national environment uses fundamentals only.")

    appr_avg = P.average(inp["approval"], as_of, cfg, grades) if not inp["approval"].empty else None
    meta = inp["meta"]
    base = meta.get("national_house_margin_2024")
    if base is None:
        base = meta.get("national_pres_margin_2024")
    if base is None:
        warnings.append("No 2024 national House or presidential margin (MEDSL unreachable and "
                        "national_house_margin_2024 unset in config/races_2026.json); nothing forecast.")
        return {"as_of": as_of.isoformat(), "national": None, "joint": None, "n_sims": 0,
                "chambers": {c: {"status": "no_data"} for c in CHAMBERS}, "warnings": warnings}
    nat = model.national_environment(generic, appr_avg["mean"] if appr_avg else None,
                                     base, rc["president_party"], cfg)

    races = inp["races"]
    usable, chambers_out = [], {}
    for ch in CHAMBERS:
        sub = races[races["chamber"] == ch] if not races.empty else races
        if sub.empty:
            chambers_out[ch] = {"status": "no_data"}
            continue
        missing = sub[sub["lean"].isna()]["race_id"].tolist()
        if missing:
            warnings.append(f"{ch}: no partisan lean for {', '.join(missing[:8])}"
                            f"{'...' if len(missing) > 8 else ''}; chamber not forecast.")
            chambers_out[ch] = {"status": "incomplete", "missing_lean": missing}
            continue
        if ch == "house" and len(sub) != rc["house"]["total_seats"]:
            warnings.append(f"house: {len(sub)} districts in house_districts.csv, expected 435.")
        usable.append(sub)

    if not usable:
        return {"as_of": as_of.isoformat(), "national": nat.__dict__, "chambers": chambers_out,
                "warnings": warnings}

    allr = pd.concat(usable, ignore_index=True)
    pavg = {}
    for rid, g in inp["polls"].groupby("race_id") if not inp["polls"].empty else []:
        a = P.average(g, as_of, cfg, grades, trend)
        if a:
            pavg[rid] = a
    est = model.combine(allr, pavg, nat, cfg)
    sims = model.simulate(est, nat, cfg, n=n_sims)

    for ch in CHAMBERS:
        if ch in chambers_out:
            continue
        idx = (est["chamber"] == ch).to_numpy()
        ccfg = {"control_threshold_d": rc[ch].get("control_threshold_d"),
                "not_up": rc[ch].get("not_up", {"D": 0, "R": 0})}
        chambers_out[ch] = {"status": "ok", **model.summarize(est[idx].reset_index(drop=True),
                                                               sims[:, idx], ccfg)}

    # Joint control probability (same simulations, so correlation is preserved).
    if all(chambers_out.get(c, {}).get("status") == "ok" for c in ("senate", "house")):
        def dem_ctrl(ch):
            idx = (est["chamber"] == ch).to_numpy()
            caucus = est.loc[idx, "dem_caucus"].to_numpy(bool)
            seats = rc[ch].get("not_up", {}).get("D", 0) + ((sims[:, idx] > 0) & caucus).sum(1)
            return seats >= rc[ch]["control_threshold_d"]
        s, h = dem_ctrl("senate"), dem_ctrl("house")
        joint = {"dem_both": float((s & h).mean()), "rep_both": float((~s & ~h).mean()),
                 "dem_house_rep_senate": float((h & ~s).mean()),
                 "dem_senate_rep_house": float((s & ~h).mean())}
    else:
        joint = None

    return {"as_of": as_of.isoformat(),
            "national": {**nat.__dict__, "approval_net": appr_avg["mean"] if appr_avg else None,
                         "baseline_2024": base},
            "chambers": chambers_out, "joint": joint, "warnings": warnings,
            "n_sims": int(sims.shape[0])}


def _history_point(f: dict) -> dict | None:
    if not f.get("national"):
        return None
    pt = {"date": f["as_of"], "national": round(f["national"]["mean"], 2)}
    for ch, c in f["chambers"].items():
        if c.get("status") == "ok":
            pt[ch] = {"p_dem": c.get("p_dem_control"), "mean": c["dem_seats"]["mean"],
                      "lo80": c["dem_seats"]["lo80"], "hi80": c["dem_seats"]["hi80"]}
    return pt


def build(as_of: date | None = None, backfill_days: int = 0, step: int = 7,
          processed: Path = PROCESSED_DIR, manual: Path = MANUAL_DIR,
          out_dir: Path = WEB_DATA_DIR, demo: bool = False) -> dict:
    rc = races_config()
    as_of = as_of or date.today()
    inp = load_inputs(processed, manual)
    f = run_model(inp, as_of)
    f.update({"generated_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds") + "Z",
              "election_date": rc["election_date"], "demo": demo,
              "sources": inp["meta"].get("sources", []),
              "chamber_notes": {c: rc[c].get("control_note") for c in CHAMBERS}})
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "forecast.json").write_text(_dumps(f))

    hist_path = out_dir / "history.json"
    hist = json.loads(hist_path.read_text()) if hist_path.exists() else []
    points = {h["date"]: h for h in hist}
    if (pt := _history_point(f)):
        points[f["as_of"]] = pt
        # Backfill: re-run using only polls published by each earlier date.
        for k in range(step, backfill_days + 1, step):
            d = as_of - timedelta(days=k)
            if d.isoformat() not in points and (old := _history_point(run_model(inp, d, n_sims=5000))):
                points[d.isoformat()] = old
    hist_path.write_text(_dumps(sorted(points.values(), key=lambda h: h["date"])))

    for w in f["warnings"]:
        _log("WARNING " + w)
    for ch, c in f["chambers"].items():
        if c.get("status") == "ok":
            pd_ = c.get("p_dem_control")
            _log(f"{ch}: Dem seats mean {c['dem_seats']['mean']:.1f}"
                 + (f", P(Dem control) {pd_:.1%}" if pd_ is not None else ""))
        else:
            _log(f"{ch}: {c.get('status')}")
    return f


def _clean_json(o):
    """Recursively replace NaN/inf and numpy scalars so the output is strict JSON."""
    if isinstance(o, dict):
        return {k: _clean_json(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean_json(v) for v in o]
    if isinstance(o, (float, np.floating)):
        return None if not np.isfinite(o) else float(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    return o


def _dumps(o) -> str:
    return json.dumps(_clean_json(o), default=_json_default, allow_nan=False)


def _json_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return None if np.isnan(o) else float(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    return str(o)
