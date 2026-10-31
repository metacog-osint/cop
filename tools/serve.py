"""Serve site/ locally with the headers from site/_headers (Cloudflare Pages format), CSP included.

    python tools/serve.py [port]   (default 5071; browsers refuse 5060 and 5061, the SIP ports)
"""
import http.server
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SITE = ROOT / "site"


def rules():
    out, current = [], None
    for line in (SITE / "_headers").read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line[0].isspace():
            current = (re.compile("^" + re.escape(line.strip()).replace(r"\*", ".*") + "$"), [])
            out.append(current)
        elif current:
            k, _, v = line.strip().partition(":")
            current[1].append((k.strip(), v.strip()))
    return out


class Handler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map,
                      ".mjs": "text/javascript", ".js": "text/javascript", ".geojson": "application/geo+json"}

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(SITE), **kw)

    def end_headers(self):
        path = self.path.split("?", 1)[0]
        for rx, headers in rules():
            if rx.match(path):
                for k, v in headers:
                    if k.lower() != "strict-transport-security":  # not over plain http
                        self.send_header(k, v)
        super().end_headers()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5071
    print(f"Serving {SITE} on http://127.0.0.1:{port} with site/_headers", flush=True)
    http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
