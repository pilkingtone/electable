"""HTTP fetching with an on-disk cache, retries, and a polite User-Agent."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from urllib.parse import urlparse

import requests

from .config import RAW_DIR

USER_AGENT = "Electable/0.1 (election forecast research; https://github.com/)"
CACHE_DIR = RAW_DIR / "http_cache"


class FetchError(RuntimeError):
    pass


# Hosts whose connection failed this run; later requests fail fast (or use cache).
_DEAD_HOSTS: set[str] = set()


def get(url: str, params: dict | None = None, *, max_age_hours: float = 6,
        as_json: bool = False, binary: bool = False, timeout: int = 30):
    """GET a URL, caching the body under data/raw/http_cache.

    A cached copy younger than ``max_age_hours`` is returned without a request.
    If the network fails, any cached copy (however old) is returned so the
    pipeline can still run offline; a FetchError is raised only if none exists.
    """
    key = hashlib.sha256(json.dumps([url, params], sort_keys=True).encode()).hexdigest()[:24]
    path = CACHE_DIR / key
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    fresh = path.exists() and (time.time() - path.stat().st_mtime) < max_age_hours * 3600
    host = urlparse(url).netloc
    if not fresh and host in _DEAD_HOSTS:
        if not path.exists():
            raise FetchError(f"{url}: host unreachable earlier in this run")
    elif not fresh:
        last_err = None
        for attempt in range(3):
            try:
                r = requests.get(url, params=params, timeout=timeout,
                                 headers={"User-Agent": USER_AGENT})
                if r.status_code == 404:
                    raise FetchError(f"404 Not Found: {url}")
                r.raise_for_status()
                path.write_bytes(r.content)
                break
            except FetchError:
                raise
            except requests.ConnectionError as e:
                last_err = e
                _DEAD_HOSTS.add(host)
                break
            except requests.RequestException as e:
                last_err = e
                time.sleep(2 ** attempt)
        if last_err is not None and not path.exists():
            raise FetchError(f"{url}: {last_err}")

    body = path.read_bytes()
    if binary:
        return body
    text = body.decode("utf-8", errors="replace")
    return json.loads(text) if as_json else text
