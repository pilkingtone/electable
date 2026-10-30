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
import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import model, polls as P
from .config import (MANUAL_DIR, PROCESSED_DIR, WEB_DATA_DIR, model_config,
                     races_config, race_id)
from .sources import fec, harvard, medsl, wikipedia

CHAMBERS = ("senate", "house", "governor")


def _log(msg: str) -> None:
    print(f"[electable] {msg}", flush=True)


def _read(path: Path) -> pd.DataFrame:
    return pd.read_csv(path) if path.exists() and path.stat().st_size else pd.DataFrame()


# ====================================================================== fetch

def _incumbent_from_holder_text(text) -> tuple[str | None, str | None]:
    """ElectIt's 'Begich (R)' -> (holder 'R', incumbent running 'R'); 'open (R)' -> ('R', None)."""
    m = re.match(r"\s*(.*?)\s*\((D|R|I)\)\s*$", str(text or ""))
    if not m:
        return None, None
    holder = m.group(2)
    return holder, (None if m.group(1).lower() in ("open", "vacant", "") else holder)


def fetch(out_dir: Path = PROCESSED_DIR) -> dict:
    """Pull every source into data/processed/. Each source fails independently and
    is reported in meta.json's warnings instead of stopping the run."""
    rc = races_config()
    warnings, sources = [], []
    out_dir.mkdir(parents=True, exist_ok=True)

    # --- Harvard: ElectIt (forecast, House seats and nominees, race polls, history)
    electit = None
    try:
        electit = harvard.electit_live()
        electit["forecasts"].to_csv(out_dir / "electit_forecasts.csv", index=False)
        electit["seats"].to_csv(out_dir / "electit_seats.csv", index=False)
        sources.append({"name": "ElectIt (King, Ebanks & Katz, Harvard): 2026 House & Senate forecast, nominees, race polls",
                        "url": harvard.ELECTIT_BASE})
        _log(f"ElectIt: {len(electit['forecasts'])} races, {len(electit['polls'])} race polls")
    except Exception as e:  # noqa: BLE001
        warnings.append(f"ElectIt forecast unavailable: {e}")
    try:
        hist = harvard.electit_results_history()
        hist.to_csv(out_dir / "electit_history.csv", index=False)
        sources.append({"name": "ElectIt results history (House 1866-2024, Senate 1918-2024)",
                        "url": harvard.ELECTIT_BASE})
    except Exception as e:  # noqa: BLE001
        warnings.append(f"ElectIt results history unavailable: {e}")

    # --- MIT Election Lab (Harvard Dataverse): 2024 presidential lean by state
    try:
        lean_df, nat_pres = medsl.state_presidential_lean(2024)
        lean = dict(zip(lean_df["state"], lean_df["lean"]))
        sources.append({"name": "MIT Election Data + Science Lab, U.S. President 1976-2024 (Harvard Dataverse)",
                        "url": "https://doi.org/10.7910/DVN/42MVDX"})
    except Exception as e:  # noqa: BLE001
        lean, nat_pres = {}, None
        warnings.append(f"State presidential lean unavailable: {e}")

    # --- Wikipedia: Senate and governor nominees, polls, expert ratings
    rows, poll_frames = [], []
    for ch in ("senate", "governor"):
        for seat in rc[ch]["seats_up"]:
            st = seat["state"]
            rid = race_id(ch, st)
            row = {"race_id": rid, "chamber": ch, "state": st, "district": None,
                   "holder": seat["holder"], "lean": lean.get(st)}
            try:
                prefer = None
                if electit is not None and ch == "senate":
                    es = electit["seats"]
                    hit = es[es["race_id"] == rid]
                    prefer = hit["dem_name"].iloc[0] if len(hit) else None
                info = wikipedia.fetch_race(ch, st, seat.get("special", False),
                                            prefer_dem=prefer if isinstance(prefer, str) else None)
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
                    poll_frames.append(info["polls"].assign(race_id=rid, source_url=info["url"]))
            except Exception as e:  # noqa: BLE001
                warnings.append(f"{rid}: Wikipedia fetch failed: {e}")
            rows.append(row)
    if poll_frames or any("ratings" in r for r in rows):
        sources.append({"name": "Wikipedia race articles (polls cited to each pollster; Cook, Sabato, Inside Elections and other ratings)",
                        "url": "https://en.wikipedia.org/wiki/2026_United_States_elections"})

    # --- House: Cook 2026 PVI (current lines) + ElectIt's nominees and incumbents
    house_html = None
    try:
        house_html = wikipedia.fetch_html(wikipedia.HOUSE_TITLE)
        pvi = wikipedia.parse_house_pvi(house_html)
        _log(f"House: Cook 2026 PVI for {len(pvi)} districts")
        sources.append({"name": "Cook Political Report 2026 PVI by district, via Wikipedia",
                        "url": wikipedia.page_url(wikipedia.HOUSE_TITLE)})
    except Exception as e:  # noqa: BLE001
        pvi = pd.DataFrame()
        warnings.append(f"House PVI unavailable: {e}")
    if electit is not None and not pvi.empty:
        lean_h = {race_id("house", s, d): l for s, d, l in zip(pvi["state"], pvi["district"], pvi["lean"])}
        seats = electit["seats"][electit["seats"]["chamber"] == "house"]
        for _, s in seats.iterrows():
            holder, inc = _incumbent_from_holder_text(s["holder_text"])
            sure = int(s["sure"])
            rows.append({"race_id": s["race_id"], "chamber": "house", "state": s["state"],
                         "district": int(s["race_id"].rsplit("-", 1)[1]), "holder": holder,
                         "lean": lean_h.get(s["race_id"]),
                         "dem_name": s["dem_name"], "rep_name": s["rep_name"], "dem_label": "D",
                         "incumbent_party": inc,
                         "uncontested": "D" if sure == 1 else "R" if sure == -1 else None,
                         "source_url": harvard.ELECTIT_BASE})
        missing = [s for s in seats["race_id"] if s not in lean_h]
        if missing:
            warnings.append(f"House: no Cook PVI for {len(missing)} districts: {', '.join(missing[:10])}")

    # --- ElectIt's race polls: all House district polls, and Senate polls for
    # races where Wikipedia had none (Wikipedia's carry sample and LV/RV details).
    if electit is not None and not electit["polls"].empty:
        ep = electit["polls"]
        have = {f["race_id"].iloc[0] for f in poll_frames}
        extra = ep[ep["race_id"].str.startswith("house-") | ~ep["race_id"].isin(have)]
        if not extra.empty:
            poll_frames.append(extra)

    # --- FEC fundraising (Senate). DEMO_KEY allows ~30 calls/hour, so only with a key.
    if os.environ.get("FEC_API_KEY"):
        for row in rows:
            if row["chamber"] != "senate" or not row.get("dem_name"):
                continue
            try:
                res = fec.totals("S", row["state"])
                row["dem_receipts"] = fec.receipts_for(res, row.get("dem_name"), "D")
                row["rep_receipts"] = fec.receipts_for(res, row.get("rep_name"), "R")
            except Exception as e:  # noqa: BLE001
                warnings.append(f"{row['race_id']}: FEC fetch failed: {e}")
                break
        else:
            sources.append({"name": "Federal Election Commission (OpenFEC candidate totals)",
                            "url": "https://api.open.fec.gov/developers/"})
    else:
        warnings.append("FEC fundraising skipped: set FEC_API_KEY to include it (small prior adjustment).")

    races = pd.DataFrame(rows)
    if electit is not None and electit.get("cand_effect"):
        races["cand_effect"] = races["race_id"].map(electit["cand_effect"])
        sources.append({"name": "ElectIt candidate effects (each nominee's estimated personal vote)",
                        "url": harvard.ELECTIT_BASE})
    races.to_csv(out_dir / "races.csv", index=False)
    polls = pd.concat(poll_frames, ignore_index=True) if poll_frames else pd.DataFrame()
    polls.to_csv(out_dir / "polls.csv", index=False) if not polls.empty else (out_dir / "polls.csv").write_text("")

    # --- National environment: the published generic-ballot averages
    gb = pd.DataFrame()
    if house_html is not None:
        gb = wikipedia.parse_generic_aggregates(house_html, source_url=wikipedia.page_url(wikipedia.HOUSE_TITLE))
    if electit is not None:
        for k, a in electit["aggregates"].items():
            name = a.get("source") or k
            if a.get("dem") and a.get("rep") and not (not gb.empty and gb["pollster"].str.contains(name.split("'")[0], regex=False).any()):
                gb = pd.concat([gb, pd.DataFrame([{"pollster": name, "partisan": None, "end_date": a.get("date"),
                                                   "sample": 5000, "population": "LV", "dem_pct": a["dem"],
                                                   "rep_pct": a["rep"], "source_url": a.get("url") or harvard.ELECTIT_BASE,
                                                   "kind": "aggregate"}])], ignore_index=True)
    if gb.empty:
        warnings.append("No generic-ballot averages found; national environment uses fundamentals only.")
        (out_dir / "generic_ballot.csv").write_text("")
    else:
        gb.to_csv(out_dir / "generic_ballot.csv", index=False)
        _log("generic ballot: " + ", ".join(f"{p} D{d - r:+.1f}" for p, d, r in zip(gb["pollster"], gb["dem_pct"], gb["rep_pct"])))
        sources.append({"name": "Generic-ballot averages: " + ", ".join(gb["pollster"].str.replace(r"\[.*?\]", "", regex=True)),
                        "url": wikipedia.page_url(wikipedia.HOUSE_TITLE)})

    h_sources, h_warn = harvard.mirror_all()
    sources += h_sources
    warnings += h_warn

    house_margin = rc.get("national_house_margin_2024")
    meta = {"fetched_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds") + "Z",
            "national_pres_margin_2024": nat_pres, "national_house_margin_2024": house_margin,
            "electit_chambers": electit["chambers"] if electit else None,
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


def load_inputs(processed: Path = PROCESSED_DIR, manual: Path = MANUAL_DIR,
                electit_dir: Path | None = None) -> dict:
    meta = json.loads((processed / "meta.json").read_text()) if (processed / "meta.json").exists() else {}
    warnings = list(meta.get("warnings", []))
    races = _read(processed / "races.csv")
    nat_pres = meta.get("national_pres_margin_2024")

    # House districts come from fetch (Cook PVI + ElectIt nominees). A manual
    # house_districts.csv, if present, replaces them (for corrections or offline use).
    hd = _read(manual / "house_districts.csv")
    if not hd.empty and not races.empty:
        races = races[races["chamber"] != "house"]
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
    elif races.empty or not (races["chamber"] == "house").any():
        warnings.append("No House districts (fetch found no Cook PVI / ElectIt seats and "
                        "data/manual/house_districts.csv is absent): House forecast not produced.")

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
        for col in ("dem_label", "incumbent_party", "uncontested", "dem_caucus", "cand_effect",
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
    electit, notes = harvard.load_electit(electit_dir or harvard.ELECTIT_DIR)
    warnings += notes
    live = _read(processed / "electit_forecasts.csv")
    if not live.empty:
        electit = pd.concat([electit, live], ignore_index=True) if not electit.empty else live
    return {"meta": meta, "warnings": warnings, "races": races, "polls": polls,
            "generic": gb, "approval": appr, "electit": electit}


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
    ext = inp.get("electit")
    hcfg = harvard.config().get("electit", {})
    ext_sets = sorted(ext["set"].unique()) if ext is not None and not ext.empty else []
    ext_set = hcfg.get("preferred_set") if hcfg.get("preferred_set") in ext_sets else (ext_sets[0] if ext_sets else None)
    if ext_set and hcfg.get("ensemble_weight", 0) > 0:
        pe = ext[ext["set"] == ext_set].set_index("race_id")["p_dem"].dropna()
        est = model.blend_external(est, pe, float(hcfg["ensemble_weight"]), nat, cfg)
    sims = model.simulate(est, nat, cfg, n=n_sims)

    for ch in CHAMBERS:
        if ch in chambers_out:
            continue
        idx = (est["chamber"] == ch).to_numpy()
        ccfg = {"control_threshold_d": rc[ch].get("control_threshold_d"),
                "not_up": rc[ch].get("not_up", {"D": 0, "R": 0})}
        chambers_out[ch] = {"status": "ok", **model.summarize(est[idx].reset_index(drop=True),
                                                               sims[:, idx], ccfg)}
        if ext_sets:
            _attach_electit(chambers_out[ch], ext, ext_sets, ccfg)
            exact = (inp["meta"].get("electit_chambers") or {}).get(ch)
            if exact and exact.get("set") in chambers_out[ch].get("electit_summary", {}):
                # ElectIt's own simulations give its exact control odds and seat distribution.
                chambers_out[ch]["electit_summary"][exact["set"]].update(
                    {"p_dem_control": exact["p_dem_control"], "dem_seats": exact["dem_seats"]})

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
            "electit": {"sets": ext_sets, "primary_set": ext_set,
                        "ensemble_weight": float(hcfg.get("ensemble_weight", 0)) if ext_sets else 0.0},
            "national": {**nat.__dict__, "approval_net": appr_avg["mean"] if appr_avg else None,
                         "baseline_2024": base},
            "chambers": chambers_out, "joint": joint, "warnings": warnings,
            "n_sims": int(sims.shape[0])}


def _attach_electit(chamber: dict, ext: pd.DataFrame, sets: list[str], ccfg: dict) -> None:
    """Add ElectIt's per-race numbers and a chamber summary for each forecast set."""
    by = {s: ext[ext["set"] == s].set_index("race_id") for s in sets}
    for r in chamber["races"]:
        vals = {}
        for s, d in by.items():
            if r["id"] in d.index:
                row = d.loc[r["id"]]
                vals[s] = {"p_dem": _f(row["p_dem"]), "dem_share": _f(row["dem_share"])}
        if vals:
            r["electit"] = vals
    summary = {}
    base = ccfg.get("not_up", {}).get("D", 0)
    for s in sets:
        ps = [r["electit"][s]["p_dem"] for r in chamber["races"]
              if s in r.get("electit", {}) and r["electit"][s]["p_dem"] is not None]
        ours = [r["p_dem"] for r in chamber["races"]
                if s in r.get("electit", {}) and r["electit"][s]["p_dem"] is not None]
        if not ps:
            continue
        disagree = sum((a >= 0.5) != (b >= 0.5) for a, b in zip(ps, ours))
        covered = len(ps) == len(chamber["races"])
        summary[s] = {"races": len(ps), "disagree": int(disagree),
                      # Expected seats is a plain sum of probabilities, so it needs no
                      # knowledge of how ElectIt correlates races.
                      "expected_dem": base + float(sum(ps)) if covered else None,
                      "mean_abs_diff": float(np.mean(np.abs(np.array(ps) - np.array(ours))))}
    if summary:
        chamber["electit_summary"] = summary


def _f(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if np.isfinite(v) else None


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
          out_dir: Path = WEB_DATA_DIR, demo: bool = False,
          electit_dir: Path | None = None) -> dict:
    rc = races_config()
    as_of = as_of or date.today()
    inp = load_inputs(processed, manual, electit_dir)
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
