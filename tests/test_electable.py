import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from electable import model, polls as P, pipeline
from electable.config import model_config
from electable.sources import wikipedia as W
from tests.fixtures import make

# ------------------------------------------------------------ parsers

INFOBOX = """
<table class="infobox"><tbody>
<tr><th>Incumbent U.S. senator before election</th><td>Jane Smith</td></tr>
<tr><th>Nominee</th><td>Jane Smith</td><td>Bob Jones Jr.</td></tr>
<tr><th>Party</th><td>Democratic</td><td>Republican</td></tr>
</tbody></table>
"""

POLLS = """
<table class="wikitable"><tbody>
<tr><th>Poll source</th><th>Date(s) administered</th><th>Sample size</th>
    <th>Margin of error</th><th>Jane Smith (D)</th><th>Bob Jones Jr. (R)</th><th>Undecided</th></tr>
<tr><td>Emerson College</td><td>October 1–5, 2026</td><td>1,000 (LV)</td><td>± 3.0%</td><td>48%</td><td>45%</td><td>7%</td></tr>
<tr><td>Some Campaign Poll<sup>[A]</sup></td><td>Sep 28 – Oct 2, 2026</td><td>600 (RV)</td><td>± 4.0%</td><td>50%</td><td>42%</td><td>8%</td></tr>
<tr><td>Bad row</td><td>TBD</td><td>–</td><td>–</td><td>–</td><td>–</td><td>–</td></tr>
</tbody></table>
<table class="wikitable"><tbody>
<tr><th>Poll source</th><th>Date(s) administered</th><th>Sample size</th><th>Jane Smith (D)</th><th>Other Guy (R)</th></tr>
<tr><td>Hypothetical</td><td>June 1, 2026</td><td>500 (RV)</td><td>40%</td><td>40%</td></tr>
</tbody></table>
"""

RATINGS = """
<table class="wikitable"><tbody>
<tr><th>Source</th><th>Ranking</th><th>As of</th></tr>
<tr><td>The Cook Political Report</td><td>Lean D</td><td>October 1, 2026</td></tr>
<tr><td>Sabato's Crystal Ball</td><td>Tossup</td><td>October 2, 2026</td></tr>
<tr><td>Inside Elections</td><td>Tilt R (flip)</td><td>October 3, 2026</td></tr>
</tbody></table>
"""


def test_parse_end_date():
    assert W.parse_end_date("October 1–5, 2026") == date(2026, 10, 5)
    assert W.parse_end_date("Sep 28 – Oct 2, 2026") == date(2026, 10, 2)
    assert W.parse_end_date("October 3, 2026") == date(2026, 10, 3)
    assert W.parse_end_date("Sept 9–12, 2026[a]") == date(2026, 9, 12)
    assert W.parse_end_date("TBD") is None


def test_parse_nominees_and_incumbent():
    noms = W.parse_nominees(INFOBOX)
    assert noms == [{"name": "Jane Smith", "party": "D"}, {"name": "Bob Jones Jr.", "party": "R"}]
    assert W.parse_incumbent(INFOBOX) == "Jane Smith"
    assert W.surname("Bob Jones Jr.") == "Jones"


def test_parse_poll_tables_matches_nominees_only():
    df = W.parse_poll_tables(POLLS, "Jane Smith", "Bob Jones Jr.")
    assert len(df) == 2
    first = df.iloc[0]
    assert (first.dem_pct, first.rep_pct, first["sample"], first.population) == (48, 45, 1000, "LV")
    assert not first.partisan and df.iloc[1].partisan


def test_parse_ratings():
    r = W.parse_ratings(RATINGS)
    assert [x["score"] for x in r] == [1, 0, -0.5]


# ------------------------------------------------------------ polls

def _polls(**kw):
    base = {"pollster": ["A", "B"], "end_date": ["2026-10-25", "2026-08-01"],
            "sample": [800, 800], "population": ["LV", "LV"], "partisan": [False, False],
            "dem_pct": [50, 40], "rep_pct": [44, 50]}
    base.update(kw)
    return pd.DataFrame(base)


def test_recent_polls_weigh_more():
    cfg = model_config()
    w = P.weights(_polls(), date(2026, 10, 28), cfg, {})
    assert w[0] > 5 * w[1]
    avg = P.average(_polls(), date(2026, 10, 28), cfg, {})
    assert avg["mean"] > 0          # dominated by the recent D+6 poll


def test_partisan_polls_downweighted_and_shifted():
    cfg = model_config()
    df = _polls(end_date=["2026-10-25"] * 2, partisan=[False, "D"], dem_pct=[48, 48], rep_pct=[46, 46])
    w = P.weights(df, date(2026, 10, 28), cfg, {})
    assert w[1] < w[0]
    a = P.average(df.iloc[[1]], date(2026, 10, 28), cfg, {})
    assert a["mean"] < P.two_party_margin(48, 46)


def test_future_polls_ignored():
    cfg = model_config()
    assert P.average(_polls(end_date=["2026-12-01"] * 2), date(2026, 10, 28), cfg, {}) is None


# ------------------------------------------------------------ model

def _race_frame(leans, chamber="senate"):
    return pd.DataFrame({"race_id": [f"{chamber}-X{i}" for i in range(len(leans))], "chamber": chamber,
                         "state": ["OH"] * len(leans), "district": None, "holder": "R",
                         "lean": leans, "incumbent_party": None, "uncontested": "",
                         "dem_caucus": True, "dem_label": "D", "dem_receipts": np.nan, "rep_receipts": np.nan})


def test_probabilities_are_monotone_and_symmetric():
    cfg = model_config()
    nat = model.National(0.0, 2.0, None, 0, 0.0, 5.0)
    est = model.combine(_race_frame([-30, -5, 0, 5, 30]), {}, nat, cfg)
    sims = model.simulate(est, nat, cfg, n=20000, seed=1)
    p = (sims > 0).mean(0)
    assert list(p) == sorted(p)
    assert p[0] < 0.02 and p[-1] > 0.98
    assert abs(p[2] - 0.5) < 0.02
    assert abs(p[1] - (1 - p[3])) < 0.03


def test_polls_pull_forecast():
    cfg = model_config()
    nat = model.National(0.0, 2.0, None, 0, 0.0, 5.0)
    races = _race_frame([0])
    est = model.combine(races, {"senate-X0": {"mean": 10.0, "sampling_sd": 1.0, "n_polls": 8,
                                              "last_poll": "2026-10-27"}}, nat, cfg)
    assert 3 < est.loc[0, "mean"] < 10
    assert 0 < est.loc[0, "w_polls"] < 1


def test_seat_count_includes_seats_not_up():
    cfg = model_config()
    nat = model.National(0.0, 1.0, None, 0, 0.0, 5.0)
    est = model.combine(_race_frame([60, 60, -60]), {}, nat, cfg)
    sims = model.simulate(est, nat, cfg, n=2000, seed=2)
    out = model.summarize(est, sims, {"control_threshold_d": 51, "not_up": {"D": 49, "R": 48}})
    assert out["dem_seats"]["mean"] == 51 and out["p_dem_control"] == 1.0


def test_shared_error_correlates_races():
    cfg = model_config()
    nat = model.National(0.0, 4.0, None, 0, 0.0, 5.0)
    est = model.combine(_race_frame([0, 0]), {}, nat, cfg)
    sims = model.simulate(est, nat, cfg, n=20000, seed=3)
    assert np.corrcoef(sims[:, 0], sims[:, 1])[0, 1] > 0.15


# ------------------------------------------------------------ end to end

def test_build_on_synthetic_fixture(tmp_path: Path):
    make(tmp_path / "processed", tmp_path / "manual")
    f = pipeline.build(as_of=date(2026, 10, 28), backfill_days=14, processed=tmp_path / "processed",
                       manual=tmp_path / "manual", out_dir=tmp_path / "web", demo=True)
    text = (tmp_path / "web" / "forecast.json").read_text()
    json.loads(text, parse_constant=lambda c: pytest.fail(f"non-strict JSON constant {c}"))
    for ch in ("senate", "house", "governor"):
        c = f["chambers"][ch]
        assert c["status"] == "ok"
        assert 0 <= c["p_dem_control"] <= 1
        assert abs(sum(d["p"] for d in c["dem_seats"]["dist"]) - 1) < 1e-9
    assert len(f["chambers"]["senate"]["races"]) == 35
    assert len(f["chambers"]["governor"]["races"]) == 36
    assert len(f["chambers"]["house"]["races"]) == 435
    hist = json.loads((tmp_path / "web" / "history.json").read_text())
    assert [h["date"] for h in hist] == ["2026-10-14", "2026-10-21", "2026-10-28"]


def test_missing_lean_blocks_chamber(tmp_path: Path):
    make(tmp_path / "processed", tmp_path / "manual")
    r = pd.read_csv(tmp_path / "processed" / "races.csv")
    r.loc[r["race_id"] == "senate-GA", "lean"] = np.nan
    r.to_csv(tmp_path / "processed" / "races.csv", index=False)
    f = pipeline.build(as_of=date(2026, 10, 28), processed=tmp_path / "processed",
                       manual=tmp_path / "manual", out_dir=tmp_path / "web")
    assert f["chambers"]["senate"]["status"] == "incomplete"
    assert f["chambers"]["governor"]["status"] == "ok"
