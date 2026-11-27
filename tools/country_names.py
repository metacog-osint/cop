"""Country names in place of the bare ISO alpha-2 codes that program-written text carries in parentheses.

COP's own pages show "Elpro GmbH (Germany)" where a program wrote "Elpro GmbH (DE)", so a reader new to the codes
can read every line. The rule matches site/country-names.js: only a code the item itself lists in its countries is
named, so an acronym that looks like a code ("antidumping duty (AD)") stays as written, and a code already beside
its name ("Iran (IR)") is left alone. Names are the doctrine kit's (geo/countries.json, Natural Earth) plus a few
territories it leaves out: the same names the programs write, so "Turkey (TR)" is already named and stays.
The page reads the same list from site/country-names-data.js, which this file writes:
python tools/country_names.py --write (a test fails when the two differ).
Shown text only: ids, links and the data files are never changed. Standard library only.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COUNTRIES = ROOT / "doctrine" / "kit" / "geo" / "countries.json"
PAGE_NAMES = ROOT / "site" / "country-names-data.js"
# Territories the kit's country list leaves out but feeds use as codes (the same list as triage.py).
EXTRA_NAMES = {"GF": "French Guiana", "GP": "Guadeloupe", "MQ": "Martinique", "RE": "Réunion", "YT": "Mayotte",
               "PF": "French Polynesia", "NC": "New Caledonia", "PR": "Puerto Rico", "GU": "Guam",
               "HK": "Hong Kong", "MO": "Macao", "FO": "Faroe Islands", "GL": "Greenland"}
CODE = re.compile(r"\(([A-Z]{2})\)")
_NAMES = None


def names():
    """Every code COP can name, with its name: the kit's list, then the territories it leaves out."""
    global _NAMES
    if _NAMES is None:
        data = json.loads(COUNTRIES.read_text(encoding="utf-8"))
        kit = {c["cc"]: c["name"] for c in data["countries"] if c.get("cc") and c.get("name")}
        _NAMES = {**{cc: n for cc, n in EXTRA_NAMES.items() if cc not in kit}, **kit}
    return _NAMES


def country_name(cc):
    """The name of an ISO alpha-2 code, or None when neither the kit nor the territory list has one."""
    return names().get(cc)


def with_country_names(text, countries):
    """`text` with each "(XX)" whose code is in `countries` read as "(Name)"."""
    if not isinstance(text, str) or not countries:
        return text
    listed = {c for c in countries if isinstance(c, str) and len(c) == 2 and c.isupper()}

    def name(m):
        cc = m.group(1)
        n = country_name(cc) if cc in listed else None
        if not n or text[:m.start()].rstrip().endswith(n):
            return m.group(0)
        return f"({n})"

    return CODE.sub(name, text) if listed else text


def page_module():
    """The text of site/country-names-data.js: the same names, for the page."""
    body = json.dumps(dict(sorted(names().items())), ensure_ascii=False, indent=0)
    return ("// Written by tools/country_names.py --write from the doctrine kit's geo/countries.json (Natural Earth)\n"
            "// plus the territories it leaves out. Do not edit by hand: a test checks it matches.\n"
            f"export const NAMES = Object.freeze({body});\n")


if __name__ == "__main__":
    if sys.argv[1:] == ["--write"]:
        PAGE_NAMES.write_text(page_module(), encoding="utf-8", newline="\n")
        print(f"wrote {PAGE_NAMES.relative_to(ROOT)}: {len(names())} names")
    else:
        sys.exit("usage: python tools/country_names.py --write")
