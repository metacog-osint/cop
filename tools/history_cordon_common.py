"""Shared pieces for COP's Cordon history datasets (cop-layer-history/1): a polite cached HTTP client, the Wayback
Machine helpers, country tables and manifest writing. Standard-library Python only.

Politeness: one request at a time, at most one every MIN_GAP_S seconds per host, retries with backoff (the Internet
Archive refuses connections for a while when it is asked too fast), and every response kept in a cache folder so a
rerun never asks twice. The cache lives outside the repository, on C: (the D: drive drops when the laptop sleeps).
"""
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = Path(r"C:\Users\PSRA User 4\cop-history\cordon")
UA = "history-rebuild/1 (python urllib)"
MIN_GAP_S = float(os.environ.get("COP_HISTORY_GAP", "1.5"))   # seconds between requests
WAYBACK = "https://web.archive.org"

# Access Now's 2025 STOP countries, in the order Tom asked for: (ISO alpha-2, name, FCDO slug, embassy host or None).
# Palestine is State's "Israel, the West Bank and Gaza" (State's page and the embassy at jr.usembassy.gov), FCDO's
# "palestine" (renamed from "the-occupied-palestinian-territories" in 2025-26) and Canada's "West Bank and Gaza".
COUNTRIES = [
    ("MM", "Myanmar", "myanmar", "mm.usembassy.gov"),
    ("IN", "India", "india", "in.usembassy.gov"),
    ("PK", "Pakistan", "pakistan", "pk.usembassy.gov"),
    ("RU", "Russia", "russia", "ru.usembassy.gov"),
    ("IQ", "Iraq", "iraq", "iq.usembassy.gov"),
    ("PS", "Palestine (West Bank and Gaza)", "palestine", "il.usembassy.gov"),
    ("IR", "Iran", "iran", None),
    ("TZ", "Tanzania", "tanzania", "tz.usembassy.gov"),
    ("AF", "Afghanistan", "afghanistan", "af.usembassy.gov"),
    ("UA", "Ukraine", "ukraine", "ua.usembassy.gov"),
    ("SY", "Syria", "syria", "sy.usembassy.gov"),
    ("YE", "Yemen", "yemen", "ye.usembassy.gov"),
    ("TR", "Turkey", "turkey", "tr.usembassy.gov"),
    ("KE", "Kenya", "kenya", "ke.usembassy.gov"),
    ("SD", "Sudan", "sudan", "sd.usembassy.gov"),
    ("CM", "Cameroon", "cameroon", "cm.usembassy.gov"),
    ("VE", "Venezuela", "venezuela", "ve.usembassy.gov"),
    ("BY", "Belarus", "belarus", "by.usembassy.gov"),
    ("CD", "DR Congo", "democratic-republic-of-the-congo", "cd.usembassy.gov"),
    ("KH", "Cambodia", "cambodia", "kh.usembassy.gov"),
    ("PA", "Panama", "panama", "pa.usembassy.gov"),
    ("JO", "Jordan", "jordan", "jo.usembassy.gov"),
    ("NP", "Nepal", "nepal", "np.usembassy.gov"),
    ("GN", "Guinea", "guinea", "gn.usembassy.gov"),
]
COUNTRIES_EXTRA = [
    ("TD", "Chad", "chad", "td.usembassy.gov"),
    ("SS", "South Sudan", "south-sudan", "ss.usembassy.gov"),
    ("AL", "Albania", "albania", "al.usembassy.gov"),
    ("PG", "Papua New Guinea", "papua-new-guinea", "pg.usembassy.gov"),
    ("LT", "Lithuania", "lithuania", "lt.usembassy.gov"),
    ("LY", "Libya", "libya", "ly.usembassy.gov"),
    ("VN", "Vietnam", "vietnam", "vn.usembassy.gov"),
    ("NG", "Nigeria", "nigeria", "ng.usembassy.gov"),
    ("DZ", "Algeria", "algeria", "dz.usembassy.gov"),
    ("TG", "Togo", "togo", "tg.usembassy.gov"),
    ("AO", "Angola", "angola", "ao.usembassy.gov"),
    ("ID", "Indonesia", "indonesia", "id.usembassy.gov"),
    ("CF", "Central African Republic", "central-african-republic", "cf.usembassy.gov"),
    ("GW", "Guinea-Bissau", "guinea-bissau", None),
    ("US", "United States", "usa", None),
]


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def code_ref(path):
    """Commit sha of the file when it is committed and unchanged, else 'uncommitted sha256:...'."""
    import subprocess
    p = Path(path).resolve()
    try:
        dirty = subprocess.run(["git", "status", "--porcelain", "--", str(p)], cwd=ROOT, capture_output=True,
                               text=True, check=True).stdout.strip()
        sha = subprocess.run(["git", "log", "-1", "--format=%h", "--", str(p)], cwd=ROOT, capture_output=True,
                             text=True, check=True).stdout.strip()
        if sha and not dirty:
            return sha
    except Exception:  # noqa: BLE001
        pass
    return "uncommitted sha256:" + sha256_file(p)[:16]


def _unzip(body):
    """The Archive sometimes sends gzip without saying so."""
    if body[:2] == bytes((0x1F, 0x8B)):
        import gzip
        try:
            return gzip.decompress(body)
        except OSError:
            pass
    return body


class Client:
    """A polite, caching HTTP client. get() returns (body_bytes, status) or (None, error_text); a 404 is cached."""

    def __init__(self, cache=CACHE, min_gap=MIN_GAP_S, log=None):
        self.cache = Path(cache)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.min_gap = min_gap
        self.last = 0.0
        self.log = log or (lambda *a: print(*a, file=sys.stderr))
        self.requests = 0
        self.hits = 0

    def _path(self, url):
        h = hashlib.sha256(url.encode()).hexdigest()
        return self.cache / h[:2] / h

    def get(self, url, tries=5, timeout=120, accept_status=(200, 404)):
        p = self._path(url)
        if p.with_suffix(".meta").exists():
            meta = json.loads(p.with_suffix(".meta").read_text(encoding="utf-8"))
            self.hits += 1
            return (_unzip(p.read_bytes()) if meta["status"] == 200 else b""), meta["status"]
        err = "?"
        for i in range(tries):
            wait = self.min_gap - (time.time() - self.last)
            if wait > 0:
                time.sleep(wait)
            self.last = time.time()
            self.requests += 1
            try:
                req = urllib.request.Request(url, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    body, status = _unzip(r.read()), r.status
            except urllib.error.HTTPError as e:
                body, status = b"", e.code
                if status not in accept_status:
                    err = f"HTTP {status}"
                    time.sleep(min(300, 20 * 2 ** i) if status in (429, 503, 403) else 5 * (i + 1))
                    continue
            except Exception as e:  # noqa: BLE001  (connection refused: the Archive is throttling us)
                err = f"{type(e).__name__}: {e}"
                time.sleep(min(300, 30 * 2 ** i))
                continue
            p.parent.mkdir(parents=True, exist_ok=True)
            if status == 200:
                p.write_bytes(body)
            else:
                p.write_bytes(b"")
            p.with_suffix(".meta").write_text(json.dumps({"url": url, "status": status, "at": utc_now()}),
                                              encoding="utf-8")
            return body, status
        return None, err

    def json(self, url, **kw):
        body, st = self.get(url, **kw)
        if body is None:
            return None, st
        if st != 200:
            return None, f"HTTP {st}"
        return json.loads(body.decode("utf-8")), 200


def cdx(client, url, **params):
    """Wayback CDX rows as dicts. params as the API names them (from, to, filter, collapse, matchType, limit)."""
    q = {"url": url, "output": "json", "fl": "timestamp,original,statuscode,digest,length", **params}
    body, st = client.get(f"{WAYBACK}/cdx/search/cdx?" + urllib.parse.urlencode(q, doseq=True))
    if body is None:
        raise RuntimeError(f"CDX failed for {url}: {st}")
    if not body.strip():
        return []
    rows = json.loads(body.decode("utf-8"))
    if not rows:
        return []
    head, rest = rows[0], rows[1:]
    return [dict(zip(head, r)) for r in rest]


def wayback_url(stamp, original):
    return f"{WAYBACK}/web/{stamp}/{original}"


def wayback_raw(client, stamp, original):
    """The capture's own bytes (the id_ flag: no Archive toolbar or rewritten links). (body, status) or (None, err)."""
    return client.get(f"{WAYBACK}/web/{stamp}id_/{original}")


def stamp_to_iso(stamp):
    return f"{stamp[0:4]}-{stamp[4:6]}-{stamp[6:8]}T{stamp[8:10]}:{stamp[10:12]}:{stamp[12:14]}Z"


def write_dataset(out_dir, manifest, rows, layer_dir="history/cordon"):
    """rows.jsonl (plain when under 5 MB, else gzip written aside) and manifest.json. Returns the manifest."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows).encode("utf-8")
    (out / "rows.jsonl").write_bytes(body)
    manifest["files"] = [{"path_or_url": f"{layer_dir}/{manifest['dataset_id']}/rows.jsonl",
                          "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body)}]
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return manifest


def row_stats(rows):
    return {"count": len(rows), "places": len({r["place"] for r in rows if r.get("place")}),
            "days": len({r["at"][:10] for r in rows if r.get("at")})}


def strip_tags(s):
    import html
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()
