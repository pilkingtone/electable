"""Wikipedia: nominees, sourced polls, expert ratings, generic ballot.

Wikipedia race articles aggregate published polls with citations to each
pollster's release, and the expert-rating tables (Cook, Sabato, Inside
Elections) with citations to each outlet. We use the MediaWiki parse API
(rendered HTML) rather than scraping the site.

Table layouts vary by article and editor, so every parser here is defensive:
it returns what it can recognise and records warnings for what it cannot.
Anything missing or wrong can be fixed via data/manual/*.csv.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from io import StringIO

import pandas as pd
from bs4 import BeautifulSoup

from ..config import NAME_TO_ABBR, STATES
from ..http import get, FetchError

API = "https://en.wikipedia.org/w/api.php"

MONTHS = {m.lower(): i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], 1)}
MONTHS.update({k[:3]: v for k, v in list(MONTHS.items())})
MONTHS["sept"] = 9


def page_title(chamber: str, state: str, special: bool = False) -> str:
    name = STATES[state]
    if chamber == "senate":
        kind = "special election" if special else "election"
        return f"2026 United States Senate {kind} in {name}"
    if chamber == "governor":
        return f"2026 {name} gubernatorial election"
    if chamber == "house":
        return f"2026 United States House of Representatives elections in {name}"
    raise ValueError(chamber)


# Individual generic-ballot polls (with field dates) are listed on the cycle overview article.
GENERIC_BALLOT_TITLE = "2026 United States elections"


def fetch_html(title: str) -> str:
    data = get(API, {"action": "parse", "page": title, "prop": "text",
                     "format": "json", "formatversion": 2, "redirects": 1},
               as_json=True)
    if "error" in data:
        raise FetchError(f"Wikipedia: {title}: {data['error'].get('info')}")
    return data["parse"]["text"]


def page_url(title: str) -> str:
    return "https://en.wikipedia.org/wiki/" + title.replace(" ", "_")


# ---------------------------------------------------------------- helpers

def _clean(s) -> str:
    s = "" if s is None or (isinstance(s, float) and pd.isna(s)) else str(s)
    s = re.sub(r"\[[^\]]*\]", "", s)          # footnote markers
    return re.sub(r"\s+", " ", s).strip()


def parse_pct(s) -> float | None:
    m = re.search(r"(\d+(?:\.\d+)?)\s*%?", _clean(s))
    if not m:
        return None
    v = float(m.group(1))
    return v if 0 <= v <= 100 else None


def parse_sample(s) -> tuple[int | None, str | None]:
    raw = str(s)
    n = re.search(r"(\d[\d,]*)", raw)
    pop = re.search(r"\b(LV|RV|A)\b", raw)
    return (int(n.group(1).replace(",", "")) if n else None,
            pop.group(1) if pop else None)


def parse_end_date(s, default_year: int = 2026) -> date | None:
    """'October 1–5, 2026' / 'Sep 28 – Oct 2, 2026' / 'October 3, 2026' -> end date."""
    text = _clean(s).replace("—", "–").replace("-", "–")
    year_m = re.findall(r"(20\d\d)", text)
    year = int(year_m[-1]) if year_m else default_year
    text = re.sub(r",?\s*20\d\d", "", text)
    parts = [p.strip() for p in text.split("–") if p.strip()]
    if not parts:
        return None
    month = None
    for p in parts:
        m = re.match(r"([A-Za-z]+)\.?", p)
        if m and m.group(1).lower() in MONTHS:
            month = MONTHS[m.group(1).lower()]
    last = parts[-1]
    m = re.match(r"([A-Za-z]+)\.?\s*(\d{1,2})", last)
    if m and m.group(1).lower() in MONTHS:
        month, day = MONTHS[m.group(1).lower()], int(m.group(2))
    else:
        d = re.match(r"(\d{1,2})", last)
        if not d or month is None:
            return None
        day = int(d.group(1))
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _read_table(table) -> pd.DataFrame:
    """pandas.read_html on one <table>, after repairing malformed rowspan/colspan
    values (editors' typos such as rowspan='2"' otherwise make pandas skip the table)."""
    for cell in table.find_all(["td", "th"]):
        for attr in ("rowspan", "colspan"):
            if cell.has_attr(attr):
                digits = re.sub(r"\D", "", str(cell[attr]))
                if digits:
                    cell[attr] = digits
                else:
                    del cell[attr]
    return _flatten_columns(pd.read_html(StringIO(str(table)))[0])


def _flatten_columns(df: pd.DataFrame) -> pd.DataFrame:
    if isinstance(df.columns, pd.MultiIndex):
        cols = []
        for tup in df.columns:
            parts = []
            for p in tup:
                p = _clean(p)
                if p and not p.startswith("Unnamed") and p not in parts:
                    parts.append(p)
            cols.append(" ".join(parts))
        df.columns = cols
    else:
        df.columns = [_clean(c) for c in df.columns]
    return df


def _sponsor_side(pollster: str) -> str | None:
    """Wikipedia marks partisan/campaign polls with a "(D)" or "(R)" after the
    pollster's name; capital-letter footnotes only name a sponsor (often a news
    outlet), so they don't count."""
    m = re.search(r"\((D|R)\)", re.sub(r"\[[^\]]*\]", "", pollster))
    return m.group(1) if m else None


def display_name(name: str) -> str:
    """'Troy Jackson (replacing Graham Platner )' -> 'Troy Jackson'."""
    return re.sub(r"\s*\(.*?\)\s*", " ", _clean(name)).strip()


def surname(name: str) -> str:
    name = re.sub(r"\(.*?\)|\b(Jr|Sr|II|III|IV)\.?$", "", _clean(name)).strip(" ,")
    return name.split()[-1] if name.split() else ""


# ---------------------------------------------------------------- nominees

PARTY_CODES = {"democratic": "D", "republican": "R", "independent": "I",
               "libertarian": "L", "green": "G"}


def parse_nominees(html: str) -> list[dict]:
    """Read the election infobox's Nominee / Party rows.

    Infoboxes nest a small table per group of candidates, so only rows whose
    own (non-recursive) header cell says Nominee/Candidate or Party count, and
    only their own cells. Returns [{"name": ..., "party": "D"|"R"|"I"|...}].
    """
    soup = BeautifulSoup(html, "lxml")
    box = soup.find("table", class_=re.compile(r"infobox"))
    if box is None:
        return []
    names, parties = [], []
    for tr in box.find_all("tr"):
        th = tr.find("th", recursive=False)
        label = _clean(th.get_text(" ")) if th else ""
        cells = [_clean(td.get_text(" ")) for td in tr.find_all("td", recursive=False)]
        if label in ("Nominee", "Candidate"):
            names.append([c for c in cells if c])
        elif label == "Party":
            parties.append([c for c in cells if c])
    out = []
    for i, group in enumerate(names):
        pg = parties[i] if i < len(parties) else []
        for j, n in enumerate(group):
            p = pg[j].lower() if j < len(pg) else ""
            code = next((v for k, v in PARTY_CODES.items() if k in p), "O")
            out.append({"name": n, "party": code})
    return out


def parse_incumbent(html: str) -> str | None:
    """The infobox's 'Incumbent ...' name (the seat's current holder)."""
    soup = BeautifulSoup(html, "lxml")
    box = soup.find("table", class_=re.compile(r"infobox"))
    if box is None:
        return None
    for b in box.find_all(["b", "th"]):
        if _clean(b.get_text(" ")).lower().startswith("incumbent"):
            cell = b.find_parent(["td", "th"]) or b.parent
            a = cell.find("p").find("a") if cell.find("p") else None
            if a is None:
                links = [x for x in cell.find_all("a") if x not in b.find_all("a")]
                a = links[0] if links else None
            if a is not None:
                return _clean(a.get_text(" ")) or None
    return None


# ---------------------------------------------------------------- polls

def parse_poll_tables(html: str, dem: str, rep: str, *, source_url: str = "") -> pd.DataFrame:
    """Polls of the dem-vs-rep matchup from every 'Poll source' table on a page.

    Matches candidate columns by surname, so hypothetical matchups with other
    candidates are skipped automatically.
    """
    soup = BeautifulSoup(html, "lxml")
    ds, rs = surname(dem).lower(), surname(rep).lower()
    rows = []
    for table in soup.find_all("table", class_=re.compile(r"wikitable")):
        head = _clean(table.find("tr").get_text(" ")) if table.find("tr") else ""
        if "poll source" not in head.lower():
            continue
        try:
            df = _read_table(table)
        except ValueError:
            continue
        cols = {c.lower(): c for c in df.columns}
        dcol = next((c for lc, c in cols.items() if ds and ds in lc), None)
        rcol = next((c for lc, c in cols.items() if rs and rs in lc), None)
        src = next((c for lc, c in cols.items() if "poll source" in lc), None)
        dtc = next((c for lc, c in cols.items() if "date" in lc), None)
        smp = next((c for lc, c in cols.items() if "sample" in lc), None)
        if not (dcol and rcol and src and dtc):
            continue
        for _, r in df.iterrows():
            d, rp = parse_pct(r[dcol]), parse_pct(r[rcol])
            end = parse_end_date(r[dtc])
            if d is None or rp is None or end is None:
                continue
            raw_src = str(r[src])
            n, pop = parse_sample(r[smp]) if smp else (None, None)
            rows.append({
                "pollster": _clean(raw_src),
                "partisan": _sponsor_side(raw_src),
                "end_date": end.isoformat(),
                "sample": n, "population": pop,
                "dem_pct": d, "rep_pct": rp,
                "source_url": source_url,
            })
    return _one_version_per_poll(pd.DataFrame(rows))


def _one_version_per_poll(out: pd.DataFrame) -> pd.DataFrame:
    """A poll often reports likely- and registered-voter numbers on adjacent rows
    (the second without a pollster name in some tables); keep one, preferring LV."""
    if out.empty:
        return out
    out = out.drop_duplicates(["pollster", "end_date", "dem_pct", "rep_pct"])
    rank = out["population"].map({"LV": 0, "RV": 1, "A": 2}).fillna(1)
    out = out.assign(_r=rank).sort_values(["pollster", "end_date", "_r"])
    out = out.drop_duplicates(["pollster", "end_date"], keep="first").drop(columns="_r")
    return out.sort_values("end_date", ascending=False).reset_index(drop=True)


def parse_generic_ballot(html: str, *, source_url: str = "") -> pd.DataFrame:
    """Generic congressional ballot polls: columns 'Democratic' and 'Republican'."""
    soup = BeautifulSoup(html, "lxml")
    rows = []
    for table in soup.find_all("table", class_=re.compile(r"wikitable")):
        try:
            df = _read_table(table)
        except ValueError:
            continue
        cols = {c.lower(): c for c in df.columns}
        dcol = next((c for lc, c in cols.items() if lc.startswith("democratic")), None)
        rcol = next((c for lc, c in cols.items() if lc.startswith("republican")), None)
        src = next((c for lc, c in cols.items() if "poll" in lc and "source" in lc
                    or lc in ("pollster", "polling firm")), None)
        dtc = next((c for lc, c in cols.items() if "date" in lc), None)
        smp = next((c for lc, c in cols.items() if "sample" in lc), None)
        if not (dcol and rcol and src and dtc):
            continue
        for _, r in df.iterrows():
            d, rp, end = parse_pct(r[dcol]), parse_pct(r[rcol]), parse_end_date(r[dtc])
            if d is None or rp is None or end is None:
                continue
            raw_src = str(r[src])
            n, pop = parse_sample(r[smp]) if smp else (None, None)
            rows.append({"pollster": _clean(raw_src),
                         "partisan": _sponsor_side(raw_src),
                         "end_date": end.isoformat(), "sample": n, "population": pop,
                         "dem_pct": d, "rep_pct": rp, "source_url": source_url})
    return _one_version_per_poll(pd.DataFrame(rows))


# ---------------------------------------------------------------- ratings

RATING_SCALE = {
    "solid d": 3, "safe d": 3, "likely d": 2, "lean d": 1, "tilt d": 0.5,
    "tossup": 0, "toss-up": 0, "toss up": 0,
    "tilt r": -0.5, "lean r": -1, "likely r": -2, "solid r": -3, "safe r": -3,
}


def normalize_rating(text: str) -> float | None:
    t = _clean(text).lower().replace("(flip)", "").strip()
    t = re.sub(r"\b(democratic|dem)\b", "d", t)
    t = re.sub(r"\b(republican|rep|gop)\b", "r", t)
    t = re.sub(r"\(.*?\)", "", t).strip()
    for k, v in RATING_SCALE.items():
        if t.startswith(k):
            return v
    return None


def parse_ratings(html: str) -> list[dict]:
    """Expert ratings table: Source | Ranking | As of."""
    soup = BeautifulSoup(html, "lxml")
    out = []
    for table in soup.find_all("table", class_=re.compile(r"wikitable")):
        first = table.find("tr")
        head = _clean(first.get_text(" ")).lower() if first else ""
        if "source" not in head or not ("ranking" in head or "rating" in head):
            continue
        try:
            df = _read_table(table)
        except ValueError:
            continue
        src = next((c for c in df.columns if "source" in c.lower()), None)
        rk = next((c for c in df.columns if "ranking" in c.lower() or "rating" in c.lower()), None)
        asof = next((c for c in df.columns if "as of" in c.lower()), None)
        if not (src and rk):
            continue
        for _, r in df.iterrows():
            score = normalize_rating(r[rk])
            if score is None:
                continue
            out.append({"source": _clean(r[src]), "rating": _clean(r[rk]),
                        "score": score, "as_of": _clean(r[asof]) if asof else ""})
        if out:
            break
    return out


# ---------------------------------------------------------------- top level

def fetch_race(chamber: str, state: str, special: bool = False, prefer_dem: str | None = None) -> dict:
    """prefer_dem: the anti-Republican candidate another source identifies as the
    main challenger (e.g. ElectIt), used when the infobox lists both a Democrat
    and a stronger independent."""
    title = page_title(chamber, state, special)
    html = fetch_html(title)
    url = page_url(title)
    nominees = parse_nominees(html)
    nominees = [{**n, "name": display_name(n["name"])} for n in nominees]
    dem = next((n for n in nominees if n["party"] == "D"), None)
    rep = next((n for n in nominees if n["party"] == "R"), None)
    # No Democrat on the ballot but a major independent (e.g. NE 2024/2026):
    # treat the independent as the anti-Republican candidate.
    if dem is None:
        dem = next((n for n in nominees if n["party"] == "I"), None)
    if prefer_dem:
        want = surname(prefer_dem).lower()
        alt = next((n for n in nominees if n["party"] != "R" and surname(n["name"]).lower() == want), None)
        if alt is not None:
            dem = alt
    polls = pd.DataFrame()
    if dem and rep:
        polls = parse_poll_tables(html, dem["name"], rep["name"], source_url=url)
    return {"title": title, "url": url, "nominees": nominees, "dem": dem, "rep": rep,
            "incumbent": parse_incumbent(html),
            "polls": polls, "ratings": parse_ratings(html),
            "fetched_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds") + "Z"}


def fetch_generic_ballot() -> pd.DataFrame:
    html = fetch_html(GENERIC_BALLOT_TITLE)
    return parse_generic_ballot(html, source_url=page_url(GENERIC_BALLOT_TITLE))


HOUSE_TITLE = "2026 United States House of Representatives elections"


def parse_pvi(text: str) -> float | None:
    """Cook PVI 'R+23' / 'D+4' / 'EVEN' -> signed share points (D positive)."""
    t = _clean(text).upper().replace("−", "-")
    if t.startswith("EVEN"):
        return 0.0
    m = re.match(r"([DR])\s*\+\s*(\d+(?:\.\d+)?)", t)
    if not m:
        return None
    return float(m.group(2)) * (1 if m.group(1) == "D" else -1)


def parse_house_pvi(html: str) -> pd.DataFrame:
    """Every district's Cook 2026 PVI from the per-state district tables.

    Returns state, district, pvi (share points, D positive) and lean (the
    margin equivalent, 2 x PVI, comparable to a presidential-margin lean).
    """
    soup = BeautifulSoup(html, "lxml")
    rows = []
    for table in soup.find_all("table", class_=re.compile(r"wikitable")):
        try:
            df = _read_table(table)
        except ValueError:
            continue
        loc = next((c for c in df.columns if c.lower().startswith("district") and "location" in c.lower()), None)
        pvi = next((c for c in df.columns if "2026 pvi" in c.lower()), None)
        if not (loc and pvi):
            continue
        for _, r in df.iterrows():
            m = re.match(r"(.+?)\s+(at-large|\d+)$", _clean(r[loc]), re.I)
            if not m:
                continue
            st = NAME_TO_ABBR.get(m.group(1).strip().upper())
            v = parse_pvi(r[pvi])
            if not st or v is None:
                continue
            d = 1 if m.group(2).lower() == "at-large" else int(m.group(2))
            rows.append({"state": st, "district": d, "pvi": v, "lean": 2 * v})
    out = pd.DataFrame(rows)
    return out.drop_duplicates(["state", "district"], keep="first") if not out.empty else out


def search_title(query: str) -> str | None:
    data = get(API, {"action": "query", "list": "search", "srsearch": query, "srlimit": 5,
                     "format": "json", "formatversion": 2}, as_json=True, max_age_hours=24)
    hits = data.get("query", {}).get("search", [])
    return hits[0]["title"] if hits else None


def parse_generic_aggregates(html: str, *, source_url: str = "") -> pd.DataFrame:
    """Generic-ballot averages from the 'Source of poll aggregation' table
    (Decision Desk HQ, RealClearPolitics, Silver Bulletin, ...), one row per
    aggregator, in the same columns as a poll. The table's own 'Average' row is
    skipped so each aggregator counts once."""
    soup = BeautifulSoup(html, "lxml")
    for table in soup.find_all("table", class_=re.compile(r"wikitable")):
        if "poll aggregation" not in table.get_text(" ").lower():
            continue
        df = _read_table(table)
        src = next((c for c in df.columns if "aggregation" in c.lower()), None)
        dcol = next((c for c in df.columns if c.lower().startswith("democrat")), None)
        rcol = next((c for c in df.columns if c.lower().startswith("republican")), None)
        upd = next((c for c in df.columns if "updated" in c.lower()), None)
        if not (src and dcol and rcol):
            continue
        rows = []
        for _, r in df.iterrows():
            name = _clean(r[src])
            if not name or name.lower().startswith("average"):
                continue
            d, rp = parse_pct(r[dcol]), parse_pct(r[rcol])
            end = parse_end_date(r[upd]) if upd else None
            if d is None or rp is None:
                continue
            rows.append({"pollster": name, "partisan": None,
                         "end_date": (end or date.today()).isoformat(),
                         # An average of many polls: a large nominal sample keeps its
                         # sampling term negligible next to the calibrated error.
                         "sample": 5000, "population": "LV",
                         "dem_pct": d, "rep_pct": rp, "source_url": source_url, "kind": "aggregate"})
        return pd.DataFrame(rows)
    return pd.DataFrame()
