"""Country names in place of the bare ISO alpha-2 codes that program-written text carries in parentheses.

COP's own pages show "Elpro GmbH (Germany)" where a program wrote "Elpro GmbH (DE)", so a reader new to the codes
can read every line. The rule matches site/country-names.js: only a code the item itself lists in its countries is
named, so an acronym that looks like a code ("antidumping duty (AD)") stays as written, and a code already beside
its name ("Iran (IR)") is left alone. Names are the doctrine kit's (geo/countries.json, Natural Earth) plus a few
territories it leaves out. Shown text only: ids, links and the data files are never changed. Standard library only.
"""
import json
import re
from pathlib import Path

COUNTRIES = Path(__file__).resolve().parents[1] / "doctrine" / "kit" / "geo" / "countries.json"
# Territories the kit's country list leaves out but feeds use as codes (the same list as triage.py).
EXTRA_NAMES = {"GF": "French Guiana", "GP": "Guadeloupe", "MQ": "Martinique", "RE": "Réunion", "YT": "Mayotte",
               "PF": "French Polynesia", "NC": "New Caledonia", "PR": "Puerto Rico", "GU": "Guam",
               "HK": "Hong Kong", "MO": "Macao", "FO": "Faroe Islands", "GL": "Greenland"}
CODE = re.compile(r"\(([A-Z]{2})\)")
_NAMES = None


def country_name(cc):
    """The name of an ISO alpha-2 code, or None when neither the kit nor the territory list has one."""
    global _NAMES
    if _NAMES is None:
        data = json.loads(COUNTRIES.read_text(encoding="utf-8"))
        _NAMES = {c["cc"]: c.get("name") for c in data["countries"] if c.get("name")}
    return _NAMES.get(cc) or EXTRA_NAMES.get(cc)


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
