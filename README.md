# Electable

A transparent, poll-driven forecast of the **2026 U.S. House, Senate and governor elections** (November 3, 2026), in the spirit of Gary King's [ELECTit](https://gking.harvard.edu/electit/). It has a Python data and model pipeline and a static website with no build step.

> **No forecaster can guarantee accuracy.** Electable reports probabilities with error sizes measured from history. Over 1998–2022 the final-three-week Senate polling average missed by 7.2 points of two-party margin (RMSE), and polls sometimes miss in the same direction everywhere (2020: +6.8 toward Democrats). The model simulates misses of that size, so a 75% favorite *should* lose about one time in four.

## Quick start

```bash
pip install -r requirements.txt
python -m electable calibrate          # error sizes from historical polls vs. results
python -m electable all --backfill-days 56   # fetch → model → web/data/*.json
python -m http.server -d web 8000      # open http://localhost:8000
```

Views are addressable by URL hash, like ELECTit: panel tokens followed by a chamber, e.g.
`#seats-races-history-senate` or `#seats-tipping-house` (underscores also work).
Panels: `seats` `races` `polls` `tipping` `history` `errors` `electit`. Chambers: `senate` `house` `governor`.

## Data sources

| Source | Used for |
|---|---|
| [ElectIt](https://gking.harvard.edu/electit/) (Gary King, Danny Ebanks, Jonathan Katz; Harvard) | House & Senate nominees and incumbents; 784 race polls (all House district polls); each nominee's estimated personal vote (candidate effects); ElectIt's own forecast, read exactly from its 320,000 published simulations, for comparison; House & Senate results history for calibration |
| Cook Political Report 2026 PVI, via Wikipedia | Every House district's partisan lean on the current (post-2025 redistricting) lines |
| Wikipedia race articles (MediaWiki API) | Senate & governor nominees, incumbents, published polls (each cited to its pollster), expert ratings (Cook, Sabato, Inside Elections, DDHQ, ...) |
| Generic-ballot averages (Decision Desk HQ, FiftyPlusOne, NBC News, RealClearPolitics, Silver Bulletin), via Wikipedia and ElectIt | National environment |
| [MIT Election Data + Science Lab](https://electionlab.mit.edu/data) (Harvard Dataverse) | 2024 presidential lean by state; Senate results for calibration |
| [FiveThirtyEight pollster ratings & raw polls](https://github.com/fivethirtyeight/data/tree/master/pollster-ratings) (CC BY 4.0, bundled) | Pollster quality weights; historical polling error 1998–2022 |
| [OpenFEC](https://api.open.fec.gov/developers/) (optional, set `FEC_API_KEY`) | Senate fundraising (small prior adjustment) |

Anything a source gets wrong can be corrected in `data/manual/` (templates in `data/manual/templates/`); manual files are merged on top at build time. `data/manual/house_districts.csv`, if present, replaces the automatic House district data.

## Model

All margins are Democratic minus Republican two-party share, in points.

1. **National environment.** A weighted generic-ballot average, blended by inverse variance with a fundamentals prior: the 2024 House vote, plus the midterm penalty against the president's party, adjusted by net approval if `data/manual/approval.csv` is supplied.
2. **Race prior.** `lean_weight × lean + national + incumbency + fundraising`, where lean is the 2024 presidential margin relative to the nation.
3. **Polling average.** Each poll is weighted by recency (21-day half-life), sample size, likely/registered/adult population, 538 pollster grade, and sponsorship. Each pollster's total influence is capped (√k for k polls). Polls are shifted by national movement since their field dates.
4. **Blend.** The prior and the polls are combined by inverse variance. Error variances come from `config/calibrated.json`, which `python -m electable calibrate` writes from history.
5. **Simulation.** 20,000 elections with Student-t (df = 5) national-environment error, a shared polling miss, a Census-region term, and race-level noise. The outputs are win probabilities, seat distributions, chamber control (Senate: Democrats need 51 because the Vice President breaks ties), joint House/Senate outcomes, and tipping-point races.

**Backtest** (leave-one-cycle-out over 1,418 races, polls component only): Brier score 0.093. Races rated 0–10% were won 1.8% of the time, and races rated 90–100% were won 98.8% of the time.

Independent candidates running in place of a Democrat (as in Nebraska) are shown in the Democratic column. They count toward Democratic control only if `dem_caucus` is set to true in `data/manual/races.csv`.

## Deploying

`.github/workflows/forecast.yml` runs the tests, calibration, fetch and build twice daily, commits the updated JSON, and publishes `web/` to GitHub Pages. Add an `FEC_API_KEY` repository secret. Any static host works, since `web/` is plain HTML, CSS and JS with D3 vendored locally.

## Layout

```
config/            races_2026.json (seats up, holders), model.json, calibrated.json
electable/         sources/ (wikipedia, medsl, fec, markets), polls.py, model.py,
                   calibrate.py, pipeline.py, __main__.py
data/raw/538/      bundled historical polls + pollster ratings
data/manual/       your overrides (see templates/)
web/               index.html, app.js, style.css, vendor/d3, data/*.json
tests/             parser, model, and end-to-end tests (synthetic fixtures only)
```

## Before publishing

- Check `config/races_2026.json` (which seats are up and who holds them) against official sources.
- Read the "Data notes" section at the bottom of the site. It lists every race where a fetch or parse failed.
- Spot-check nominees and poll counts in the race table against the linked Wikipedia articles.
