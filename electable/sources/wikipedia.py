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

from ..config import STATES
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


GENERIC_BALLOT_TITLE = "Opinion polling for the 2026 United States House of Representatives elections"


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


def surname(name: str) -> str:
    name = re.sub(r"\(.*?\)|\b(Jr|Sr|II|III|IV)\.?$", "", _clean(name)).strip(" ,")
    return name.split()[-1] if name.split() else ""


# ---------------------------------------------------------------- nominees

PARTY_CODES = {"democratic": "D", "republican": "R", "independent": "I",
               "libertarian": "L", "green": "G"}


def parse_nominees(html: str) -> list[dict]:
    """Read the election infobox's Nominee / Party rows.

    Returns [{"name": ..., "party": "D"|"R"|"I"|...}] in infobox order.
    """
    soup = BeautifulSoup(html, "lxml")
    box = soup.find("table", class_=re.compile(r"infobox"))
    if box is None:
        return []
    names, parties = [], []
    for tr in box.find_all("tr"):
        th = tr.find("th")
        label = _clean(th.get_text(" ")) if th else ""
        cells = [_clean(td.get_text(" ")) for td in tr.find_all("td")]
        if label in ("Nominee", "Candidate"):
            names.extend(c for c in cells if c)
        elif label == "Party":
            parties.extend(c for c in cells if c)
    out = []
    for i, n in enumerate(names):
        p = parties[i].lower() if i < len(parties) else ""
        code = next((v for k, v in PARTY_CODES.items() if k in p), "O")
        out.append({"name": n, "party": code})
    return out


def parse_incumbent(html: str) -> str | None:
    """Name in the infobox's '... before election' row (the seat's incumbent)."""
    soup = BeautifulSoup(html, "lxml")
    box = soup.find("table", class_=re.compile(r"infobox"))
    if box is None:
        return None
    for tr in box.find_all("tr"):
        th = tr.find("th")
        if th and "before election" in th.get_text(" ").lower():
            td = tr.find("td")
            if td:
                return _clean(td.get_text(" ")) or None
            # Some infoboxes put the name in the following row.
            nxt = tr.find_next_sibling("tr")
            return _clean(nxt.get_text(" ")) if nxt else None
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
            df = _flatten_columns(pd.read_html(StringIO(str(table)))[0])
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
                # Wikipedia marks sponsored/partisan polls with a capital-letter
                # footnote (e.g. "[A]") or a "(D)"/"(R)" suffix.
                "partisan": bool(re.search(r"\[[A-Z]\]|\((D|R)\)", raw_src)),
                "end_date": end.isoformat(),
                "sample": n, "population": pop,
                "dem_pct": d, "rep_pct": rp,
                "source_url": source_url,
            })
    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.drop_duplicates(["pollster", "end_date", "dem_pct", "rep_pct"])
    return out


def parse_generic_ballot(html: str, *, source_url: str = "") -> pd.DataFrame:
    """Generic congressional ballot polls: columns 'Democratic' and 'Republican'."""
    soup = BeautifulSoup(html, "lxml")
    rows = []
    for table in soup.find_all("table", class_=re.compile(r"wikitable")):
        try:
            df = _flatten_columns(pd.read_html(StringIO(str(table)))[0])
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
                         "partisan": bool(re.search(r"\[[A-Z]\]|\((D|R)\)", raw_src)),
                         "end_date": end.isoformat(), "sample": n, "population": pop,
                         "dem_pct": d, "rep_pct": rp, "source_url": source_url})
    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.drop_duplicates(["pollster", "end_date", "dem_pct", "rep_pct"])
    return out


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
            df = _flatten_columns(pd.read_html(StringIO(str(table)))[0])
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

def fetch_race(chamber: str, state: str, special: bool = False) -> dict:
    title = page_title(chamber, state, special)
    html = fetch_html(title)
    url = page_url(title)
    nominees = parse_nominees(html)
    dem = next((n for n in nominees if n["party"] == "D"), None)
    rep = next((n for n in nominees if n["party"] == "R"), None)
    # No Democrat on the ballot but a major independent (e.g. NE 2024/2026):
    # treat the independent as the anti-Republican candidate.
    if dem is None:
        dem = next((n for n in nominees if n["party"] == "I"), None)
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
