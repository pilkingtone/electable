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
<tr><td><table><tbody>
<tr><th>Nominee</th><td><a>Jane Smith</a></td><td><a>Bob Jones Jr.</a></td><td></td></tr>
<tr><th>Party</th><td><a>Democratic</a></td><td><a>Republican</a></td></tr>
</tbody></table></td></tr>
<tr><td class="infobox-full-data"><table><tbody><tr>
<td><b>Incumbent <a>U.S. senator</a></b><br/><p><a>Jane Smith</a><br/><a>Democratic</a></p></td>
<td></td></tr></tbody></table></td></tr>
</tbody></table>
"""

POLLS = """
<table class="wikitable"><tbody>
<tr><th>Poll source</th><th>Date(s) administered</th><th>Sample size</th>
    <th>Margin of error</th><th>Jane Smith (D)</th><th>Bob Jones Jr. (R)</th><th>Undecided</th></tr>
<tr><td>Emerson College</td><td>October 1–5, 2026</td><td>1,000 (LV)</td><td>± 3.0%</td><td>48%</td><td>45%</td><td>7%</td></tr>
<tr><td>Some Campaign Poll (R)<sup>[A]</sup></td><td>Sep 28 – Oct 2, 2026</td><td>600 (RV)</td><td>± 4.0%</td><td>50%</td><td>42%</td><td>8%</td></tr>
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
    assert pd.isna(first.partisan) and df.iloc[1].partisan == "R"


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

def _base_cfg():
    """Model settings before calibration, so these tests don't move when calibration does."""
    from electable.config import CONFIG_DIR
    return json.loads((CONFIG_DIR / "model.json").read_text())


def _race_frame(leans, chamber="senate"):
    return pd.DataFrame({"race_id": [f"{chamber}-X{i}" for i in range(len(leans))], "chamber": chamber,
                         "state": ["OH"] * len(leans), "district": None, "holder": "R",
                         "lean": leans, "incumbent_party": None, "uncontested": "",
                         "dem_caucus": True, "dem_label": "D", "dem_receipts": np.nan, "rep_receipts": np.nan})


def test_probabilities_are_monotone_and_symmetric():
    cfg = _base_cfg()
    nat = model.National(0.0, 2.0, None, 0, 0.0, 5.0)
    est = model.combine(_race_frame([-30, -5, 0, 5, 30]), {}, nat, cfg)
    sims = model.simulate(est, nat, cfg, n=20000, seed=1)
    p = (sims > 0).mean(0)
    assert list(p) == sorted(p)
    assert p[0] < 0.02 and p[-1] > 0.98
    assert abs(p[2] - 0.5) < 0.02
    assert abs(p[1] - (1 - p[3])) < 0.03


def test_polls_pull_forecast():
    cfg = _base_cfg()
    nat = model.National(0.0, 2.0, None, 0, 0.0, 5.0)
    races = _race_frame([0])
    est = model.combine(races, {"senate-X0": {"mean": 10.0, "sampling_sd": 1.0, "n_polls": 8,
                                              "last_poll": "2026-10-27"}}, nat, cfg)
    assert 3 < est.loc[0, "mean"] < 10
    assert 0 < est.loc[0, "w_polls"] < 1


def test_seat_count_includes_seats_not_up():
    cfg = _base_cfg()
    nat = model.National(0.0, 1.0, None, 0, 0.0, 5.0)
    est = model.combine(_race_frame([60, 60, -60]), {}, nat, cfg)
    sims = model.simulate(est, nat, cfg, n=2000, seed=2)
    out = model.summarize(est, sims, {"control_threshold_d": 51, "not_up": {"D": 49, "R": 48}})
    assert out["dem_seats"]["mean"] == 51 and out["p_dem_control"] == 1.0


def test_shared_error_correlates_races():
    cfg = _base_cfg()
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


# ------------------------------------------------------------ Harvard / ElectIt

from electable import calibrate
from electable.sources import harvard
from tests.fixtures import make_electit


def test_electit_column_detection_variants():
    a = pd.DataFrame({"State": ["Georgia", "Maine"], "Prob_Dem_Win": [62.5, 40], "Model": ["x", "x"]})
    out, notes = harvard.standardize_forecasts(a, file_name="electit_senate.csv")
    assert not notes
    assert out.set_index("race_id")["p_dem"].to_dict() == {"senate-GA": 0.625, "senate-ME": 0.40}
    b = pd.DataFrame({"st": ["AK", "TX"], "cd": ["AL", "7"], "office": ["House", "House"], "p_rep": [0.9, 0.2]})
    out, _ = harvard.standardize_forecasts(b, file_name="f.csv")
    assert out.set_index("race_id")["p_dem"].round(3).to_dict() == {"house-AK-01": 0.1, "house-TX-07": 0.8}
    c = pd.DataFrame({"foo": [1], "bar": [2]})
    out, notes = harvard.standardize_forecasts(c, file_name="mystery.csv")
    assert out.empty and "column_map" in notes[0]


def test_electit_skips_past_years():
    d = pd.DataFrame({"year": [2024, 2026], "state": ["GA", "GA"], "p_dem": [0.3, 0.6]})
    out, _ = harvard.standardize_forecasts(d, file_name="senate.csv")
    assert out["p_dem"].tolist() == [0.6]


def test_blend_external_moves_toward_external():
    cfg = model_config()
    nat = model.National(0.0, 2.0, None, 0, 0.0, 5.0)
    est = model.combine(_race_frame([0, 0]), {}, nat, cfg)
    ext = pd.Series({"senate-X0": 0.9})
    out = model.blend_external(est, ext, 0.5, nat, cfg)
    assert out.loc[0, "mean"] > 2 and out.loc[1, "mean"] == est.loc[1, "mean"]
    assert model.blend_external(est, ext, 0.0, nat, cfg) is est


def test_history_errors_from_long_series():
    rng = np.random.default_rng(0)
    rows = []
    for d in range(1, 60):
        base = rng.normal(0, 20)
        for y in range(1946, 2024, 2):
            rows.append({"year": y, "chamber": "house", "state": "OH", "district": d,
                         "margin": base + rng.normal(0, 6)})
    out = calibrate.history_errors(pd.DataFrame(rows))
    assert 7 < out["house"]["race_sd"] < 10       # diff of two N(0,6) draws ~ 8.5
    assert out["house"]["years"].startswith(str(calibrate.MODERN_FROM + 2))   # modern era only


def test_build_with_electit(tmp_path: Path):
    make(tmp_path / "processed", tmp_path / "manual")
    make_electit(tmp_path / "electit", tmp_path / "processed" / "races.csv", tmp_path / "manual" / "house_districts.csv")
    f = pipeline.build(as_of=date(2026, 10, 28), processed=tmp_path / "processed", manual=tmp_path / "manual",
                       out_dir=tmp_path / "web", electit_dir=tmp_path / "electit")
    assert f["electit"]["sets"] == ["No polls", "With polls"]
    s = f["chambers"]["senate"]
    assert all("electit" in r for r in s["races"])
    assert s["electit_summary"]["No polls"]["races"] == 35
    assert f["chambers"]["house"]["electit_summary"]["With polls"]["expected_dem"] is not None
    assert "electit_summary" not in f["chambers"]["governor"]


def test_governor_prior_uses_ratings():
    cfg = model_config()
    nat = model.National(0.0, 2.0, None, 0, 0.0, 5.0)
    row = pd.Series({"chamber": "governor", "lean": 30.0, "incumbent_party": "R",
                     "dem_receipts": np.nan, "rep_receipts": np.nan,
                     "ratings": [{"score": -2}, {"score": -2}]})
    with_r = model.race_prior(row, nat, cfg)
    row["ratings"] = []
    assert with_r < model.race_prior(row, nat, cfg) - 10


# ------------------------------------------------------------ news channel

from datetime import datetime, timezone
from electable import news

RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>T</title>
<item><title>Democrats flip the House as votes are counted</title><link>https://example.com/a</link>
<pubDate>Wed, 04 Nov 2026 10:00:00 GMT</pubDate><description>&lt;p&gt;The &lt;b&gt;election&lt;/b&gt; results so far.&lt;/p&gt;</description></item>
<item><title>Recipe of the week: soup</title><link>https://example.com/b</link><pubDate>Wed, 04 Nov 2026 09:00:00 GMT</pubDate></item>
<item><title>Old Senate story</title><link>https://example.com/c</link><pubDate>Thu, 01 Oct 2026 09:00:00 GMT</pubDate></item>
<item><title>No link poll story</title><pubDate>Wed, 04 Nov 2026 09:00:00 GMT</pubDate></item>
</channel></rss>"""

ATOM_FEED = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>DEMOCRATS FLIP THE HOUSE as votes are counted</title><link href="https://example.org/x"/>
<updated>2026-11-04T08:00:00Z</updated><summary>Same story, other outlet.</summary></entry>
<entry><title>Governor race heads to recount</title><link rel="alternate" href="https://example.org/y"/>
<updated>2026-11-05T08:00:00Z</updated></entry></feed>"""


def test_news_parse_filter_dedupe():
    raw = news.parse_feed(RSS, "Outlet A") + news.parse_feed(ATOM_FEED, "Outlet B")
    assert all(i["url"].startswith("https://") for i in raw)
    assert raw[0]["summary"] == "The election results so far."
    cfg = {"keywords": ["election", "house", "senate", "governor", "recount", "poll"], "max_age_days": 14}
    out = news.compile_items(raw, cfg, datetime(2026, 11, 6, tzinfo=timezone.utc))
    assert [i["title"] for i in out] == ["Governor race heads to recount",
                                         "Democrats flip the House as votes are counted"]
    assert out[1]["source"] == "Outlet A"          # newest copy of a duplicate headline wins


def test_news_rejects_entity_expansion():
    bomb = '<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;">]><rss><channel><item><title>&b;</title></item></channel></rss>'
    with pytest.raises(Exception):
        news.parse_feed(bomb, "X")


def test_results_winner_matches_nominee_not_party_link():
    from electable.sources import results as R
    html = """<table class="infobox"><tbody>
    <tr><td><table><tbody>
    <tr><th>Nominee</th><td>Ed Markey</td><td>John Deaton</td></tr>
    <tr><th>Party</th><td>Democratic</td><td>Republican</td></tr>
    <tr><th>Popular vote</th><td>2,000,000</td><td>1,000,000</td></tr>
    <tr><th>Percentage</th><td>66.7%</td><td>33.3%</td></tr></tbody></table></td></tr>
    <tr><td><b>Elected U.S. senator</b><br/><p><a href="/wiki/Democratic_Party_(United_States)">Democratic</a> Ed Markey</p></td></tr>
    </tbody></table>"""
    out = R.parse_statewide(html, "Ed Markey", "John Deaton")
    assert out["called"] and out["winner"] == "D" and round(out["margin"], 1) == 33.3


def test_news_tolerates_undeclared_prefixes():
    xml = """<?xml version="1.0"?><rss version="2.0"><channel>
    <item><title>Fact-check: claims about the Senate vote count</title><link>https://example.com/f</link>
    <pubDate>Wed, 04 Nov 2026 10:00:00 GMT</pubDate><media:content url="https://example.com/i.jpg"/></item>
    </channel></rss>"""
    items = news.parse_feed(xml, "PolitiFact")
    assert items[0]["title"].startswith("Fact-check") and items[0]["url"] == "https://example.com/f"
