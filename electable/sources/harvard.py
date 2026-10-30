"""Harvard data: Dataverse datasets and Gary King et al.'s ElectIt forecasts.

* Harvard Dataverse: every file of every dataset listed in config/harvard.json
  is mirrored to data/raw/dataverse/<doi>/ (original format where available).
* ElectIt (https://gking.harvard.edu/electit/, King, Ebanks & Katz): race-level
  2026 House and Senate forecasts and their historical inputs (House since
  1946, Senate since 1954). Files are fetched from configured URLs or dropped
  into data/raw/electit/ by hand.

ElectIt's file layout isn't hard-coded. Columns are recognised by name
(state / district / office / probability), and config/harvard.json's
`electit.column_map` overrides the guesses. Anything unrecognised is reported
rather than silently dropped.
"""
from __future__ import annotations

import json
import re
from io import BytesIO
from pathlib import Path

import pandas as pd

from ..config import CONFIG_DIR, NAME_TO_ABBR, RAW_DIR, STATES, race_id
from ..http import get

DATAVERSE = "https://dataverse.harvard.edu/api"
ELECTIT_DIR = RAW_DIR / "electit"
DATAVERSE_DIR = RAW_DIR / "dataverse"
TABULAR = (".csv", ".tsv", ".tab", ".txt", ".xlsx", ".xls", ".json", ".dta", ".rds")


def config() -> dict:
    path = CONFIG_DIR / "harvard.json"
    return json.loads(path.read_text()) if path.exists() else {"dataverse_datasets": [], "electit": {}}


# ------------------------------------------------------------- Dataverse mirror

def _doi_dir(doi: str) -> Path:
    return DATAVERSE_DIR / re.sub(r"[^A-Za-z0-9]+", "_", doi.replace("doi:", ""))


def mirror_dataset(doi: str) -> list[Path]:
    """Download every file in a Dataverse dataset. Returns the local paths."""
    meta = get(f"{DATAVERSE}/datasets/:persistentId/", {"persistentId": doi},
               as_json=True, max_age_hours=24)
    out_dir = _doi_dir(doi)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "dataset.json").write_text(json.dumps(meta, indent=1))
    paths = []
    for f in meta["data"]["latestVersion"]["files"]:
        df = f["dataFile"]
        # Tabular ingests are served as .tab unless we ask for the original upload.
        name = df.get("originalFileName") or df["filename"]
        from .medsl import _datafile
        body = _datafile(df["id"])
        p = out_dir / name
        p.write_bytes(body)
        paths.append(p)
    return paths


def mirror_all() -> tuple[list[dict], list[str]]:
    """Mirror every configured dataset and ElectIt URL. Returns (sources, warnings)."""
    cfg, sources, warnings = config(), [], []
    for ds in cfg.get("dataverse_datasets", []):
        try:
            paths = mirror_dataset(ds["doi"])
            sources.append({"name": f"Harvard Dataverse: {ds['name']}",
                            "url": "https://doi.org/" + ds["doi"].replace("doi:", ""),
                            "files": len(paths)})
            if ds.get("role") == "electit_history":
                for p in paths:
                    if p.suffix.lower() in TABULAR:
                        dest = ELECTIT_DIR / "history" / p.name
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        dest.write_bytes(p.read_bytes())
        except Exception as e:  # noqa: BLE001
            warnings.append(f"Dataverse {ds['doi']} ({ds['name']}): {e}")
    ELECTIT_DIR.mkdir(parents=True, exist_ok=True)
    for url in cfg.get("electit", {}).get("urls", []):
        try:
            body = get(url, binary=True, max_age_hours=6, timeout=120)
            name = url.rstrip("/").split("/")[-1].split("?")[0] or "electit_download"
            (ELECTIT_DIR / name).write_bytes(body)
            sources.append({"name": f"ElectIt: {name}", "url": url})
        except Exception as e:  # noqa: BLE001
            warnings.append(f"ElectIt {url}: {e}")
    return sources, warnings


# ------------------------------------------------------------- reading files

def read_table(path: Path) -> pd.DataFrame:
    suf = path.suffix.lower()
    if suf in (".xlsx", ".xls"):
        return pd.read_excel(path)
    if suf == ".json":
        data = json.loads(path.read_text())
        if isinstance(data, dict):     # {"races": [...]} or {"senate": [...], "house": [...]}
            lists = {k: v for k, v in data.items() if isinstance(v, list)}
            if len(lists) == 1:
                return pd.DataFrame(next(iter(lists.values())))
            return pd.concat([pd.DataFrame(v).assign(_chamber_key=k) for k, v in lists.items()],
                             ignore_index=True)
        return pd.DataFrame(data)
    if suf == ".dta":
        return pd.read_stata(path)
    body = path.read_bytes()
    sep = "\t" if suf in (".tsv", ".tab") or body[:4000].count(b"\t") > body[:4000].count(b",") else ","
    return pd.read_csv(BytesIO(body), sep=sep, low_memory=False)


def _find(cols: list[str], patterns: list[str]) -> str | None:
    low = {c: re.sub(r"[^a-z0-9]+", "_", c.lower()).strip("_") for c in cols}
    for pat in patterns:
        rx = re.compile(pat)
        for c, lc in low.items():
            if rx.fullmatch(lc):
                return c
    return None


COLUMN_PATTERNS = {
    "state": [r"state_?po", r"state_?abb?r?e?v?", r"st", r"state", r"state_?name"],
    "district": [r"district", r"cd", r"dist", r"district_?num(ber)?", r"seat"],
    "chamber": [r"chamber", r"office", r"race_?type", r"body", r"_chamber_key"],
    "p_dem": [r"p_?(dem|d)(_?win)?", r"prob(ability)?_?(dem|d)(_?win)?", r"(dem|d)_?win_?prob(ability)?",
              r"pr_?(dem|d)", r"(dem|d)_?prob"],
    "p_rep": [r"p_?(rep|r)(_?win)?", r"prob(ability)?_?(rep|r)(_?win)?", r"(rep|r)_?win_?prob(ability)?"],
    "dem_share": [r"(pred|forecast|expected|mean)_?(dem|d)_?(vote_?)?share", r"(dem|d)_?(vote_?)?share(_?(pred|mean))?",
                  r"(dem|d)_?two_?party"],
    "lo": [r"(dem|d)_?(share_?)?(lo|lower|q05|q10|p05|p10)(80|90|95)?", r"(lo|lower)(80|90|95)?"],
    "hi": [r"(dem|d)_?(share_?)?(hi|upper|q90|q95|p90|p95)(80|90|95)?", r"(hi|upper)(80|90|95)?"],
    "set": [r"set", r"model", r"forecast_?type", r"version", r"polls?"],
    "year": [r"year", r"cycle", r"election_?year"],
}


def detect_columns(df: pd.DataFrame, overrides: dict | None = None) -> dict:
    cols = [str(c) for c in df.columns]
    found = {k: _find(cols, pats) for k, pats in COLUMN_PATTERNS.items()}
    found.update({k: v for k, v in (overrides or {}).items() if v in cols})
    return {k: v for k, v in found.items() if v}


def _state_abbr(v) -> str | None:
    s = str(v).strip().upper()
    if s in STATES:
        return s
    return NAME_TO_ABBR.get(s)


def _chamber(v, district) -> str | None:
    s = str(v).strip().lower() if v is not None and not pd.isna(v) else ""
    if s.startswith(("sen", "s")) and not s.startswith("st"):
        return "senate"
    if s.startswith(("house", "h", "rep", "cong")):
        return "house"
    if s.startswith("gov"):
        return "governor"
    return None


def _prob(v) -> float | None:
    try:
        x = float(str(v).strip().rstrip("%"))
    except ValueError:
        return None
    if pd.isna(x):
        return None
    return x / 100 if x > 1 else x


def standardize_forecasts(df: pd.DataFrame, *, file_name: str = "", overrides: dict | None = None) -> tuple[pd.DataFrame, list[str]]:
    """ElectIt-style table -> race_id, set, p_dem, dem_share, lo, hi."""
    cols = detect_columns(df, overrides)
    notes = []
    if "state" not in cols or not ({"p_dem", "p_rep", "dem_share"} & cols.keys()):
        return pd.DataFrame(), [f"{file_name}: no state + probability/share columns recognised "
                                f"(columns: {', '.join(map(str, df.columns[:20]))}); set electit.column_map"]
    file_chamber = ("senate" if re.search(r"sen", file_name, re.I) else
                    "house" if re.search(r"house|cong", file_name, re.I) else None)
    rows = []
    for _, r in df.iterrows():
        st = _state_abbr(r[cols["state"]])
        if not st:
            continue
        dist = r[cols["district"]] if "district" in cols else None
        ch = _chamber(r[cols["chamber"]], dist) if "chamber" in cols else None
        ch = ch or file_chamber or ("house" if dist is not None and not pd.isna(dist) else "senate")
        if "year" in cols and pd.notna(r[cols["year"]]) and int(float(r[cols["year"]])) != 2026:
            continue
        p = _prob(r[cols["p_dem"]]) if "p_dem" in cols else None
        if p is None and "p_rep" in cols:
            pr = _prob(r[cols["p_rep"]])
            p = None if pr is None else 1 - pr
        share = None
        if "dem_share" in cols:
            share = _prob(r[cols["dem_share"]])
        if ch == "house":
            try:
                d = int(re.sub(r"\D", "", str(dist)) or 1)   # at-large -> 1
            except ValueError:
                continue
            rid = race_id("house", st, d if d > 0 else 1)
        else:
            rid = race_id(ch, st)
        rows.append({"race_id": rid, "chamber": ch,
                     "set": str(r[cols["set"]]) if "set" in cols else (Path(file_name).stem or "electit"),
                     "p_dem": p, "dem_share": share,
                     "lo": _prob(r[cols["lo"]]) if "lo" in cols else None,
                     "hi": _prob(r[cols["hi"]]) if "hi" in cols else None})
    out = pd.DataFrame(rows)
    if out.empty:
        notes.append(f"{file_name}: columns recognised ({cols}) but no 2026 rows parsed")
    return out, notes


def load_electit(directory: Path = ELECTIT_DIR) -> tuple[pd.DataFrame, list[str]]:
    """All ElectIt forecast files in a directory, standardized and stacked."""
    overrides = config().get("electit", {}).get("column_map", {})
    frames, notes = [], []
    if not directory.exists():
        return pd.DataFrame(), notes
    for p in sorted(directory.iterdir()):
        if not p.is_file() or p.suffix.lower() not in TABULAR:
            continue
        try:
            df, n = standardize_forecasts(read_table(p), file_name=p.name, overrides=overrides)
        except Exception as e:  # noqa: BLE001
            notes.append(f"{p.name}: could not read ({e})")
            continue
        notes += n
        if not df.empty:
            frames.append(df)
    if not frames:
        return pd.DataFrame(), notes
    out = pd.concat(frames, ignore_index=True)
    # p_dem from vote share when only shares are published: leave None (we don't
    # know ElectIt's error distribution), the UI then shows the share instead.
    return out.drop_duplicates(["race_id", "set"], keep="last"), notes


def load_history(directory: Path = ELECTIT_DIR / "history") -> pd.DataFrame:
    """Historical race results (House 1946-, Senate 1954-) as year, chamber, state,
    district, margin (D-R two-party points). Empty if none present."""
    if not directory.exists():
        return pd.DataFrame()
    frames = []
    for p in sorted(directory.iterdir()):
        if not p.is_file() or p.suffix.lower() not in TABULAR:
            continue
        try:
            df = read_table(p)
        except Exception:  # noqa: BLE001
            continue
        cols = [str(c) for c in df.columns]
        year = _find(cols, COLUMN_PATTERNS["year"])
        state = _find(cols, COLUMN_PATTERNS["state"])
        dist = _find(cols, COLUMN_PATTERNS["district"])
        ch = _find(cols, COLUMN_PATTERNS["chamber"])
        share = _find(cols, [r"(dem|d)_?(two_?party_?)?(vote_?)?share", r"(dem|d)_?vs", r"dvote_?share", r"v"])
        dv = _find(cols, [r"(dem|d)_?votes?", r"dvotes?"])
        rv = _find(cols, [r"(rep|r)_?votes?", r"rvotes?"])
        if not (year and state and (share or (dv and rv))):
            continue
        d = pd.DataFrame({"year": pd.to_numeric(df[year], errors="coerce"),
                          "state": df[state].map(_state_abbr),
                          "district": pd.to_numeric(df[dist], errors="coerce") if dist else None,
                          "chamber": [(_chamber(v, None) if ch else None) or
                                      ("senate" if re.search(r"sen", p.name, re.I) else "house")
                                      for v in (df[ch] if ch else [None] * len(df))]})
        if share:
            s = pd.to_numeric(df[share], errors="coerce")
            s = s.where(s <= 1, s / 100)
            d["margin"] = 100 * (2 * s - 1)
        else:
            a, b = pd.to_numeric(df[dv], errors="coerce"), pd.to_numeric(df[rv], errors="coerce")
            d["margin"] = 100 * (a - b) / (a + b)
        frames.append(d.dropna(subset=["year", "state", "margin"]))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


# ------------------------------------------------------------- ElectIt site files
#
# ElectIt (gking.harvard.edu/electit/) publishes its forecast as JSON/CSV files
# beside the page. Large files are packed (their pack.py): {"split": [parts]}
# whose texts are joined in order; {"gz": "<base64 or 'b85:' RFC 1924 base85>"}
# holding gzipped text. These helpers mirror the page's own getData()/inflate().

ELECTIT_BASE = "https://gking.harvard.edu/electit/"
_B85 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz!#$%&()*+-;<=>?@^_`{|}~"
_B85_IDX = {c: i for i, c in enumerate(_B85)}


def b85decode(s: str) -> bytes:
    """ElectIt's base85: 'b85:' prefix, RFC 1924 alphabet, a short final group padded with '~'."""
    body = s[4:] if s.startswith("b85:") else s
    n = len(body)
    out_len = (n // 5) * 4 + max(0, n % 5 - 1)
    out = bytearray(out_len)
    o = 0
    for i in range(0, n, 5):
        v = 0
        for j in range(i, i + 5):
            v = v * 85 + (_B85_IDX[body[j]] if j < n else 84)
        for k in range(4):
            if o + k < out_len:
                out[o + k] = (v >> (24 - 8 * k)) & 255
        o += 4
    return bytes(out)


def inflate(packed: str) -> bytes:
    import base64
    import gzip
    raw = b85decode(packed) if packed.startswith("b85:") else base64.b64decode(packed)
    return gzip.decompress(raw)


def electit_get(path: str, as_text: bool = False, max_age_hours: float = 3):
    """Fetch an ElectIt data file by its path under /electit/, unpacked."""
    url = ELECTIT_BASE + path

    def fetch_text(u: str) -> str:
        return get(u, max_age_hours=max_age_hours, timeout=120)

    text = fetch_text(url)
    try:
        j = json.loads(text)
    except json.JSONDecodeError:
        return text
    if isinstance(j, dict) and "split" in j and len(j) == 1:
        text = "".join(fetch_text(ELECTIT_BASE + p) for p in j["split"])
        try:
            j = json.loads(text)
        except json.JSONDecodeError:
            return text
    if isinstance(j, dict) and isinstance(j.get("gz"), str):
        text = inflate(j["gz"]).decode("utf-8")
        if as_text:
            return text
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text
    return text if as_text else j


def _electit_page_data() -> dict:
    """The DATA object embedded in the ElectIt page (seats, nominees, polls, aggregates)."""
    html = get(ELECTIT_BASE, max_age_hours=3, timeout=120)
    m = re.search(r"(?:const|let|var)\s+DATA\s*=\s*", html)
    if not m:
        raise ValueError("ElectIt page layout changed: no DATA object found")
    data, _ = json.JSONDecoder().raw_decode(html[m.end():])
    return data


def _seat_race_id(chamber: str, label: str) -> str:
    st, rest = label.split("-", 1)
    if chamber == "senate":
        return race_id("senate", st)
    return race_id("house", st, 1 if rest == "AL" else int(rest))


def electit_live() -> dict:
    """ElectIt's current 2026 forecast, read from its published files.

    Returns
      forecasts : race_id, chamber, set, p_dem, dem_share, lo, hi
                  (p_dem = share of ElectIt's 320,000 simulations the Democrat wins;
                  dem_share/lo/hi = median and 80% range of the two-party vote)
      chambers  : {house|senate: {p_dem_control, dem_seats mean/lo80/hi80, dist}}
      seats     : race_id, label, holder text, dem/rep nominees, contest type, sure (+1 D / -1 R)
      polls     : race polls as ElectIt lists them (margin D-R, sample, pollster, sponsor)
      aggregates: generic-ballot averages ElectIt reads (Silver Bulletin, RealClearPolitics)
    """
    import numpy as np
    D = _electit_page_data()
    sims = electit_get("sims-2026.json")
    n = int(D["n"])
    words = n // 32
    set_name = "ElectIt model"
    rows, seats_rows, poll_rows, chambers = [], [], [], {}
    for ch in ("house", "senate"):
        seats = D[ch]["seats"]
        v = sims["versions"]["model"][ch]
        bits = np.frombuffer(inflate(v["bits"]), dtype=np.uint8)
        dem_seats = np.full(n, int(D[ch]["held"]), dtype=np.int32)
        for i, s in enumerate(seats):
            rid = _seat_race_id(ch, s["l"])
            k = v["k"][i]
            if k >= 0:
                won = np.unpackbits(bits[k * words * 4:(k + 1) * words * 4], bitorder="little")[:n]
                p = float(won.mean())
                dem_seats += won
            else:
                p = 1.0 if k == -2 else 0.0
                dem_seats += int(k == -2)
            if s.get("dc"):
                dem_seats += 1 - (1 if k == -2 else 0)
            rows.append({"race_id": rid, "chamber": ch, "set": set_name, "p_dem": p,
                         "dem_share": v["m"][i], "lo": v["lo"][i], "hi": v["hi"][i]})
            seats_rows.append({"race_id": rid, "chamber": ch, "label": s["l"], "state": s["s"],
                               "holder_text": s.get("w"), "dem_name": s.get("d") or None,
                               "rep_name": s.get("r") or None, "contest": s.get("c"),
                               "sure": int(s.get("u") or 0)})
        vals, cnt = np.unique(dem_seats, return_counts=True)
        need = int(D[ch]["need"])
        chambers[ch] = {"set": set_name, "need": need,
                        "p_dem_control": float((dem_seats >= need).mean()),
                        "dem_seats": {"mean": float(dem_seats.mean()),
                                      "lo80": float(np.percentile(dem_seats, 10)),
                                      "hi80": float(np.percentile(dem_seats, 90)),
                                      "dist": [{"seats": int(a), "p": float(b / n)} for a, b in zip(vals, cnt)]}}
        for p in D.get("race", {}).get("polls", {}).get(ch, []):
            i, start, end, margin, size, pollster, sponsor = (list(p) + [None] * 7)[:7]
            if i is None or i >= len(seats) or margin is None:
                continue
            poll_rows.append({"race_id": _seat_race_id(ch, seats[i]["l"]), "pollster": pollster,
                              "partisan": sponsor or None, "start_date": start, "end_date": end,
                              "sample": size, "population": None,
                              # ElectIt lists two-party margins; express them as shares.
                              "dem_pct": 50 + margin / 2, "rep_pct": 50 - margin / 2,
                              "source_url": ELECTIT_BASE})
    # Candidate effects (ElectIt's estimate of each nominee's personal vote, in
    # vote-share points): net margin effect = 2 x (Democrat's - Republican's).
    effects = {}
    for ch in ("house", "senate"):
        for c in D.get("cands", {}).get(ch, []):
            label = c["r"] if ch == "house" else f"{c['r']}-Sen"
            try:
                rid = _seat_race_id(ch, label)
            except (ValueError, IndexError):
                continue
            e = effects.setdefault(rid, {"D": 0.0, "R": 0.0})
            if c.get("p") in ("D", "R"):
                e[c["p"]] += float(c["e"])
    cand_effect = {rid: 2 * (e["D"] - e["R"]) for rid, e in effects.items()}
    agg = {k: {kk: D[k].get(kk) for kk in ("margin", "dem", "rep", "date", "source", "url")}
           for k in ("feed", "rcp") if isinstance(D.get(k), dict)}
    return {"forecasts": pd.DataFrame(rows), "chambers": chambers, "seats": pd.DataFrame(seats_rows),
            "polls": pd.DataFrame(poll_rows), "aggregates": agg, "n_sims": n, "cand_effect": cand_effect,
            "election": D.get("election")}


def electit_results_history() -> pd.DataFrame:
    """ElectIt's results file (House since 1866, Senate) as year, chamber, state,
    district, margin (D-R two-party points); uncontested races dropped."""
    res = electit_get("results.json", max_age_hours=24)
    rows = []
    for ch in ("house", "senate"):
        for year, block in res.get(ch, {}).items():
            for r in block.get("rows", []):
                label, _holder, _d, _r, unc, share = r[:6]
                if unc or share is None:
                    continue
                st, rest = (label.split("-", 1) + [""])[:2]
                dist = None if ch == "senate" else (1 if rest == "AL" else int(re.sub(r"\D", "", rest) or 1))
                rows.append({"year": int(year), "chamber": ch, "state": st, "district": dist,
                             "margin": 100 * (2 * float(share) - 1)})
    return pd.DataFrame(rows)
