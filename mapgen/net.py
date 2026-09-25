"""HTTP helpers: IPv4-only resolution, on-disk cache, parallel fetch."""
import hashlib
import os
import socket
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

# Some hosts (e.g. cyberjapandata.gsi.go.jp) hang over IPv6 from here; force IPv4.
_orig_getaddrinfo = socket.getaddrinfo


def _ipv4_only(*args, **kwargs):
    res = _orig_getaddrinfo(*args, **kwargs)
    v4 = [r for r in res if r[0] == socket.AF_INET]
    return v4 or res


socket.getaddrinfo = _ipv4_only

CACHE_DIR = Path(os.environ.get("MAPGEN_CACHE", Path(__file__).resolve().parent.parent / ".cache"))
UA = "kimimachi-mapgen/0.1 (pixel game map generator)"
_session = requests.Session()
_session.headers["User-Agent"] = UA


def fetch(url, cache=True, timeout=30, params=None):
    """GET url and return bytes, or None on 404. Cached on disk by URL."""
    key = url + ("?" + "&".join(f"{k}={v}" for k, v in sorted(params.items())) if params else "")
    path = CACHE_DIR / "http" / hashlib.sha1(key.encode()).hexdigest()[:2] / hashlib.sha1(key.encode()).hexdigest()
    if cache and path.exists():
        data = path.read_bytes()
        return data if data else None
    r = _session.get(url, params=params, timeout=timeout)
    if r.status_code == 404:
        data = b""
    else:
        r.raise_for_status()
        data = r.content
    if cache:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return data or None


def fetch_many(urls, workers=8):
    with ThreadPoolExecutor(workers) as ex:
        return list(ex.map(fetch, urls))


def fetch_json(url, params=None, cache=True):
    import json

    data = fetch(url, params=params, cache=cache)
    return json.loads(data) if data else None
