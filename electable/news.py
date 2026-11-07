"""The News channel: election and politics headlines compiled from major outlets' RSS feeds.

Only what publishers put in their feeds for syndication is kept (headline, link,
time, and a trimmed summary), and every item links to the original article.
Feeds are parsed with defusedxml because their content is untrusted.
"""
from __future__ import annotations

import html
import json
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

from defusedxml import ElementTree as ET

from .config import CONFIG_DIR, WEB_DATA_DIR
from .http import get

ATOM = "{http://www.w3.org/2005/Atom}"
TAG = re.compile(r"<[^>]+>")


def _text(el, *names) -> str:
    for n in names:
        found = el.find(n)
        if found is not None and (found.text or "").strip():
            return found.text.strip()
    return ""


def _clean(s: str, limit: int | None = None) -> str:
    s = re.sub(r"\s+", " ", html.unescape(TAG.sub(" ", s or ""))).strip()
    if limit and len(s) > limit:
        s = s[:limit].rsplit(" ", 1)[0].rstrip(",;:—-") + "…"
    return s


def _when(s: str) -> datetime | None:
    if not s:
        return None
    try:
        d = parsedate_to_datetime(s)                                  # RSS: RFC 822
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))       # Atom: ISO 8601
        except ValueError:
            return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def parse_feed(xml: str, source: str) -> list[dict]:
    """Items from an RSS 2.0 or Atom feed."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as e:
        if "unbound prefix" not in str(e):
            raise
        # Some feeds use namespace prefixes they never declare (e.g. <media:content>
        # without xmlns:media). Rename undeclared prefixed tags/attributes so the
        # strict parser accepts the document; the fields we read are unprefixed.
        xml = re.sub(r"<(/?)([A-Za-z_][\w.-]*):", r"<\1\2_", xml)
        xml = re.sub(r"\s(?!xmlns)([A-Za-z_][\w.-]*):([A-Za-z_][\w.-]*)=", r" \1_\2=", xml)
        root = ET.fromstring(xml)
    items = []
    for it in root.iter("item"):
        items.append({"title": _text(it, "title"), "url": _text(it, "link", "guid"),
                      "published": _when(_text(it, "pubDate", "{http://purl.org/dc/elements/1.1/}date")),
                      "summary": _text(it, "description")})
    for it in root.iter(f"{ATOM}entry"):
        link = it.find(f"{ATOM}link[@rel='alternate']")
        if link is None:      # (an Element with no children is falsy, so no `or` here)
            link = it.find(f"{ATOM}link")
        items.append({"title": _text(it, f"{ATOM}title"), "url": link.get("href", "") if link is not None else "",
                      "published": _when(_text(it, f"{ATOM}updated", f"{ATOM}published")),
                      "summary": _text(it, f"{ATOM}summary", f"{ATOM}content")})
    out = []
    for i in items:
        title, url = _clean(i["title"]), i["url"].strip()
        if not title or not url.startswith(("https://", "http://")):
            continue
        out.append({"title": title, "url": url, "source": source, "published": i["published"],
                    "summary": _clean(i["summary"], 200)})
    return out


def _relevant(item: dict, keywords: list[str]) -> bool:
    text = f"{item['title']} {item['summary']}".lower()
    return any(re.search(rf"\b{re.escape(k)}", text) for k in keywords)


def _key(title: str) -> str:
    """Normalised headline, to merge the same story syndicated by several outlets."""
    return re.sub(r"[^a-z0-9 ]", "", title.lower())[:80]


def compile_items(raw: list[dict], cfg: dict, now: datetime) -> list[dict]:
    cutoff = now - timedelta(days=cfg.get("max_age_days", 14))
    seen, per_source, out = set(), {}, []
    keep = [i for i in raw if i["published"] and i["published"] >= cutoff and _relevant(i, cfg["keywords"])]
    for i in sorted(keep, key=lambda x: x["published"], reverse=True):
        k = _key(i["title"])
        if k in seen or per_source.get(i["source"], 0) >= cfg.get("max_per_source", 15):
            continue
        seen.add(k)
        per_source[i["source"]] = per_source.get(i["source"], 0) + 1
        out.append({**i, "published": i["published"].astimezone(timezone.utc).isoformat(timespec="seconds")})
        if len(out) >= cfg.get("max_items", 120):
            break
    return out


def run(out_dir: Path = WEB_DATA_DIR) -> dict:
    cfg = json.loads((CONFIG_DIR / "news.json").read_text())
    raw, warnings, ok = [], [], []
    for f in cfg["feeds"]:
        # A feed may list fallback addresses ("urls"); the first that parses is used.
        errors = []
        for url in f.get("urls") or [f["url"]]:
            try:
                raw += parse_feed(get(url, max_age_hours=0.5, timeout=20), f["name"])
                ok.append(f["name"])
                break
            except Exception as e:  # noqa: BLE001 - one bad feed must not sink the channel
                errors.append(str(e)[:120])
        else:
            warnings.append(f"{f['name']}: {' | '.join(errors)}")
    now = datetime.now(timezone.utc)
    items = compile_items(raw, cfg, now)
    out = {"generated_at": now.replace(tzinfo=None).isoformat(timespec="seconds") + "Z",
           "sources": [{"name": f["name"], "lean": f.get("lean")} for f in cfg["feeds"] if f["name"] in ok],
           "items": items, "warnings": warnings}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "news.json").write_text(json.dumps(out))
    print(f"[electable] news: {len(items)} items from {len(ok)}/{len(cfg['feeds'])} feeds", flush=True)
    for w in warnings:
        print(f"[electable] WARNING news feed {w}", flush=True)
    return out
