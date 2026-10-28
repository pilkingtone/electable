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
`#seats_races_history_senate` or `#seats_tipping_house`.
Panels: `seats` `races` `polls` `tipping` `history` `errors`. Chambers: `senate` `house` `governor`.

## Data sources

| Source | Used for | Access |
|---|---|---|
| Wikipedia race articles (MediaWiki API) | Nominees, incumbents, published polls (each cited to its pollster), Cook / Sabato / Inside Elections ratings | automatic |
| Wikipedia generic-ballot polling page | National environment | automatic |
| [MIT Election Data + Science Lab](https://electionlab.mit.edu/data) (Harvard Dataverse, CC0) | 2024 state presidential lean; historical Senate/House results for prior calibration | automatic |
| [OpenFEC API](https://api.open.fec.gov/developers/) | Senate fundraising (small prior adjustment) | set `FEC_API_KEY` |
| [FiveThirtyEight pollster ratings & raw polls](https://github.com/fivethirtyeight/data/tree/master/pollster-ratings) (CC BY 4.0) | Pollster quality weights; historical polling error 1998–2023 | bundled in `data/raw/538/` |
| Kalshi / Polymarket (optional) | Side-by-side comparison only, never a model input | `config/markets.json` |

Anything the scrapers miss or get wrong can be fixed in `data/manual/` (templates in `data/manual/templates/`). Manual files are merged on top of fetched data at build time, so you can correct inputs and re-run `build` without re-fetching.

### Required by hand: House districts

Several states redrew their maps in 2025–26, so House district lean has to reflect the **current lines**. Create `data/manual/house_districts.csv`, one row for each of the 435 districts:

```
state,district,holder,incumbent_running,dem_name,rep_name,pres_margin_2024,uncontested
GA,2,D,true,Sanford Bishop,Wayne Johnson,6.2,
```

`pres_margin_2024` is the 2024 presidential two-party margin (D − R, points) on the new lines. Sources include The Downballot's and Dave's Redistricting presidential-results-by-district tables. Until this file exists, the House panel says so instead of showing a forecast.

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
