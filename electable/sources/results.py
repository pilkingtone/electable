"""2026 election results from Wikipedia race articles (which cite the Associated
Press and state election officials).

Senate and governor: each race article's infobox carries the nominees' vote
totals and percentages, and an "Elected ..." block once the race is called.
House: the 2026 House article's per-state district tables list the winner first
with a status ("Incumbent re-elected.", "Democratic gain.", ...), and its infobox
carries the seats won and the national House vote.
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from ..config import NAME_TO_ABBR, race_id
from . import wikipedia as W

PENDING = re.compile(r"too close|uncalled|not yet called|pending|undecided|tbd|runoff|recount", re.I)


def _num(text: str) -> float | None:
    m = re.search(r"\d[\d,]*(?:\.\d+)?", W._clean(text))
    return float(m.group(0).replace(",", "")) if m else None


def parse_statewide(html: str, dem_name: str | None, rep_name: str | None) -> dict | None:
    """Vote totals for the two forecast candidates, and the winner if called."""
    soup = BeautifulSoup(html, "lxml")
    box = soup.find("table", class_=re.compile(r"infobox"))
    if box is None:
        return None
    rows: dict[str, list[list[str]]] = {}
    for tr in box.find_all("tr"):
        th = tr.find("th", recursive=False)
        label = W._clean(th.get_text(" ")) if th else ""
        if label in ("Nominee", "Candidate", "Party", "Popular vote", "Percentage",
                     "First round", "Final round", "Reporting"):
            rows.setdefault(label, []).append(
                [W._clean(td.get_text(" ")) for td in tr.find_all("td", recursive=False)])
    names = rows.get("Nominee") or rows.get("Candidate") or []
    votes, pcts = rows.get("Popular vote", []), rows.get("Percentage", [])
    if not votes:
        # Ranked-choice states (Alaska, Maine) give "votes pct" per round instead,
        # e.g. "102,962 48.18%"; the final round, once tabulated, decides the race.
        rounds = rows.get("Final round") or rows.get("First round") or []
        votes = [[c.split()[0] if c else "" for c in g] for g in rounds]
        pcts = [[(c.split() + ["", ""])[1] if c else "" for c in g] for g in rounds]
    cands = []
    for g, group in enumerate(names):
        for j, n in enumerate(group):
            if not n:
                continue
            v = votes[g][j] if g < len(votes) and j < len(votes[g]) else ""
            p = pcts[g][j] if g < len(pcts) and j < len(pcts[g]) else ""
            cands.append({"name": W.display_name(n), "votes": _num(v), "pct": _num(p)})

    def find(name):
        if not name:
            return None
        sn = W.surname(name).lower()
        return next((c for c in cands if W.surname(c["name"]).lower() == sn), None)

    d, r = find(dem_name), find(rep_name)
    out = {"dem_votes": d and d["votes"], "rep_votes": r and r["votes"],
           "dem_pct": d and d["pct"], "rep_pct": r and r["pct"]}
    rep_row = (rows.get("Reporting") or [[""]])[0]
    if rep_row and rep_row[0]:
        out["reporting"] = _num(rep_row[0])                 # "74% as of November 5" -> 74
    if rows.get("Final round") or rows.get("First round"):
        out["round"] = "final" if rows.get("Final round") else "first"
    if out["dem_votes"] and out["rep_votes"]:
        out["margin"] = 100 * (out["dem_votes"] - out["rep_votes"]) / (out["dem_votes"] + out["rep_votes"])
    elif out["dem_pct"] and out["rep_pct"]:
        out["margin"] = 100 * (out["dem_pct"] - out["rep_pct"]) / (out["dem_pct"] + out["rep_pct"])

    # The infobox names the winner in an "Elected ..." block once the race is called.
    winner = winner_code = None
    for b in box.find_all("b"):
        if W._clean(b.get_text(" ")).lower().startswith("elected"):
            cell = b.find_parent("td") or b.parent
            # The block reads "Elected U.S. senator <name> <party>"; match the nominees'
            # surnames in it rather than trusting the first link (often the party).
            rest = W._clean(cell.get_text(" ")).replace(W._clean(b.get_text(" ")), "", 1).lower()
            for code, name in (("D", dem_name), ("R", rep_name)):
                if name and re.search(rf"\b{re.escape(W.surname(name).lower())}\b", rest):
                    winner, winner_code = name, code
                    break
            if winner is None:
                links = [x for x in cell.find_all("a") if x not in b.find_all("a")
                         and not re.search(r"party|independent", x.get("href", "") + x.get_text(), re.I)]
                if links:
                    winner, winner_code = W._clean(links[0].get_text(" ")), "O"
            break
    if winner:
        out["winner_name"] = winner
        out["winner"] = winner_code
        out["called"] = True
    else:
        out["called"] = False
    if not cands and not winner:
        return None
    return out


PARTY = {"democratic": "D", "republican": "R", "independent": "I"}


def _party(text: str) -> str:
    """'Democratic', 'DFL' / 'Democratic–Farmer–Labor' (MN), 'Democratic-NPL' (ND) -> D."""
    t = text.strip().lower()
    if t.startswith(("democratic", "dfl")) or "farmer" in t or "npl" in t:
        return "D"
    if t.startswith("republican"):
        return "R"
    return PARTY.get(t.split()[0], "O") if t else "O"


def parse_house(html: str) -> tuple[dict, dict]:
    """(per-district results, chamber totals) from the 2026 House article."""
    soup = BeautifulSoup(html, "lxml")
    races = {}
    for table in soup.find_all("table", class_=re.compile(r"wikitable")):
        text = table.get_text(" ")
        if "2026 PVI" not in text or "Candidates" not in text:
            continue
        try:
            df = W._read_table(table)
        except ValueError:
            continue
        loc = next((c for c in df.columns if "location" in c.lower()), None)
        status = next((c for c in df.columns if "status" in c.lower()), None)
        cands = next((c for c in df.columns if c.lower().startswith("candidates")), None)
        if not (loc and status and cands):
            continue
        for _, r in df.iterrows():
            m = re.match(r"(.+?)\s+(at-large|\d+)$", W._clean(r[loc]), re.I)
            st = NAME_TO_ABBR.get(m.group(1).strip().upper()) if m else None
            if not st:
                continue
            d = 1 if m.group(2).lower() == "at-large" else int(m.group(2))
            rid = race_id("house", st, d)
            stat = W._clean(r[status])
            # Candidates are listed winner first: "▌ Mike McGuire (Democratic) ▌James Gallagher (Republican)".
            entries = [e.strip() for e in str(r[cands]).split("▌") if e.strip()]
            first = re.match(r"(.+?)\s*\(([^)]+)\)", W._clean(entries[0])) if entries else None
            called = bool(first) and not PENDING.search(stat) and bool(
                re.search(r"elected|re-elected|gain|hold|loss", stat, re.I))
            prev = races.get(rid)
            # A district listed twice (two incumbents drawn together) agrees on the winner.
            if prev and prev["called"]:
                continue
            races[rid] = {"called": called, "status": stat,
                          "winner_name": first.group(1).strip() if first and called else None,
                          "winner": _party(first.group(2)) if first and called else None}

    totals = {}
    box = soup.find("table", class_=re.compile(r"infobox"))
    if box is not None:
        lab = {}
        for tr in box.find_all("tr"):
            th = tr.find("th", recursive=False)
            key = W._clean(th.get_text(" ")) if th else ""
            if key in ("Party", "Seats won", "Popular vote", "Percentage"):
                lab.setdefault(key, [W._clean(td.get_text(" ")) for td in tr.find_all("td", recursive=False)])
        parties = [_party(p) for p in lab.get("Party", [])]
        for key, field in (("Seats won", "seats"), ("Popular vote", "votes"), ("Percentage", "pct")):
            for p, v in zip(parties, lab.get(key, [])):
                totals.setdefault(p, {})[field] = _num(v)
    return races, totals


ORDINAL = re.compile(r"(\d+)(?:st|nd|rd|th)\s+congressional district", re.I)


def parse_house_state(html: str, state: str) -> dict:
    """Per-district general-election vote totals from a state's 2026 House article.

    Each district's results box is captioned "2026 Iowa's 1st congressional district
    election" (or "...at-large congressional district election"); primary boxes are
    skipped. Rows read Party | Candidate | Votes | %. Districts whose votes aren't
    filled in yet are omitted.
    """
    soup = BeautifulSoup(html, "lxml")
    out = {}
    for table in soup.find_all("table"):
        cap = table.find("caption")
        title = W._clean(cap.get_text(" ")) if cap else ""
        if "congressional district" not in title.lower() or "primary" in title.lower() \
                or "special" in title.lower() or "2026" not in title:
            continue
        m = ORDINAL.search(title)
        dist = int(m.group(1)) if m else (1 if "at-large" in title.lower() else None)
        if dist is None:
            continue
        cands, reporting = _box_candidates(table)
        if not cands:
            cands = _results_table_candidates(table)
        if not cands:
            continue
        # Top-two/same-party races have two Democrats or two Republicans; take the best of each party.
        best = {p: max((c for c in cands if c["party"] == p), key=lambda c: c["votes"], default=None) for p in ("D", "R")}
        d, r = best["D"], best["R"]
        rec = {"candidates": sorted(cands, key=lambda c: -c["votes"])[:4],
               "dem_votes": d and d["votes"], "rep_votes": r and r["votes"],
               "dem_pct": d and d["pct"], "rep_pct": r and r["pct"]}
        if d and r and d["votes"] + r["votes"] > 0:
            rec["margin"] = 100 * (d["votes"] - r["votes"]) / (d["votes"] + r["votes"])
        if reporting is not None:
            rec["reporting"] = reporting
        rid = race_id("house", state, dist)
        # A district can have both an infobox and a results box; keep the fuller one.
        if rid not in out or len(rec["candidates"]) > len(out[rid]["candidates"]):
            out[rid] = {**out.get(rid, {}), **rec}
    return out


def _box_candidates(table) -> tuple[list[dict], float | None]:
    """Infobox-style box: Nominee / Party / Popular vote (or First/Final round) / Percentage rows."""
    rows: dict[str, list[list[str]]] = {}
    for tr in table.find_all("tr"):
        th = tr.find("th", recursive=False)
        label = W._clean(th.get_text(" ")) if th else ""
        if label in ("Nominee", "Candidate", "Party", "Popular vote", "Percentage",
                     "First round", "Final round", "Reporting"):
            rows.setdefault(label, []).append([W._clean(td.get_text(" ")) for td in tr.find_all("td", recursive=False)])
    reporting = _num(rows["Reporting"][0][0]) if rows.get("Reporting") and rows["Reporting"][0] else None
    names, parties = rows.get("Nominee") or rows.get("Candidate") or [], rows.get("Party", [])
    votes, pcts = rows.get("Popular vote", []), rows.get("Percentage", [])
    if not votes:
        rounds = rows.get("Final round") or rows.get("First round") or []
        votes = [[c.split()[0] if c else "" for c in g] for g in rounds]
        pcts = [[(c.split() + ["", ""])[1] if c else "" for c in g] for g in rounds]
    out = []
    for g, group in enumerate(names):
        for j, n in enumerate(group):
            v = _num(votes[g][j]) if g < len(votes) and j < len(votes[g]) else None
            if not n or v is None:
                continue
            party = parties[g][j] if g < len(parties) and j < len(parties[g]) else ""
            pct = _num(pcts[g][j]) if g < len(pcts) and j < len(pcts[g]) else None
            out.append({"party": _party(party), "name": W.display_name(n), "votes": v, "pct": pct})
    return out, reporting


def _results_table_candidates(table) -> list[dict]:
    """Results box: Party | Candidate | Votes | % rows (a color-swatch cell may lead)."""
    out = []
    for tr in table.find_all("tr"):
        cells = [W._clean(c.get_text(" ")) for c in tr.find_all(["td", "th"])]
        while cells and cells[0] == "":
            cells = cells[1:]
        if len(cells) < 3 or cells[0].lower().startswith(("party", "total", "turnout")):
            continue
        votes = _num(cells[2])
        if votes is None or not re.fullmatch(r"[\d,]+", cells[2].replace(" ", "")):
            continue
        out.append({"party": _party(cells[0]), "name": re.sub(r"\s*\((?:incumbent|inc\.)\)", "", cells[1], flags=re.I),
                    "votes": votes, "pct": _num(cells[3]) if len(cells) > 3 else None})
    return out
