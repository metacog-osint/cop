"""Country names in place of bare ISO codes in program-written text: python tests/test_country_names.py"""
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import brief  # noqa: E402
from country_names import PAGE_NAMES, country_name, names, page_module, with_country_names  # noqa: E402

# (text, the item's countries, what the page and the brief show, with the kit's names)
CASES = [
    ("aurora claims Elpro GmbH (DE)", ["DE"], "aurora claims Elpro GmbH (Germany)"),
    ("US embassy (MX): Demonstration Alert", ["MX"], "US embassy (Mexico): Demonstration Alert"),
    ("payload(s): USA 123 (US), USA 124 (US).", ["US"], "payload(s): USA 123 (United States of America), USA 124 (United States of America)."),
    # A code the item does not list stays: these are acronyms, not countries.
    ("circumventing the antidumping duty (AD) and a general license (GL)", ["CN"],
     "circumventing the antidumping duty (AD) and a general license (GL)"),
    ("Cloudflare marked it verified (TP).", ["PH"], "Cloudflare marked it verified (TP)."),
    # A code already beside its name stays as written.
    ("Iran (IR): OONI anomaly/blocking rate 34.0%", ["IR"], "Iran (IR): OONI anomaly/blocking rate 34.0%"),
    ("Myanmar (MM): OONI anomaly/blocking rate 24.3%", ["MM"], "Myanmar (MM): OONI anomaly/blocking rate 24.3%"),
    # Programs write the kit's names (Blackout: f"{name} ({cc}): ..."), which the browser's own list spells
    # differently for some 28 codes; the kit's name beside its code stays, so nothing is named twice.
    ("Turkey (TR): OONI anomaly/blocking rate 40%", ["TR"], "Turkey (TR): OONI anomaly/blocking rate 40%"),
    ("United States of America (US): OONI anomaly/blocking rate 12%", ["US"],
     "United States of America (US): OONI anomaly/blocking rate 12%"),
    ("Palestine (PS): x", ["PS"], "Palestine (PS): x"),
    ("Democratic Republic of the Congo (CD): x", ["CD"], "Democratic Republic of the Congo (CD): x"),
    ("Hong Kong (HK): x", ["HK"], "Hong Kong (HK): x"),
    ("US embassy (TR): Security Alert", ["TR"], "US embassy (Turkey): Security Alert"),
    ("no countries (DE)", [], "no countries (DE)"),
    ("ids keep their codes: cordon:embassy:DE-1", ["DE"], "ids keep their codes: cordon:embassy:DE-1"),
]


class Python(unittest.TestCase):
    def test_codes_of_the_items_own_countries_read_as_names(self):
        for text, countries, _ in CASES:
            want = text
            for cc in countries:
                name = country_name(cc)
                if f"({cc})" in text and not text.startswith(name or "\0"):
                    want = want.replace(f"({cc})", f"({name})")
            with self.subTest(text):
                self.assertEqual(with_country_names(text, countries), want)
        self.assertEqual(country_name("GB"), "United Kingdom")
        self.assertEqual(country_name("HK"), "Hong Kong", "a territory the kit leaves out")
        self.assertIsNone(country_name("ZZ"))
        self.assertIsNone(with_country_names(None, ["DE"]))

    def test_the_brief_shows_names_and_keeps_the_programs_title(self):
        s = {"kind": "changed", "item_id": "vigil:x", "program": "vigil", "program_name": "Vigil",
             "title": "aurora claims Elpro GmbH (DE)", "against": None, "publishers": ["ransomware.live"],
             "link": "https://vigil.theintelligence.company/", "countries": ["DE"]}
        self.assertIn("Elpro GmbH (Germany)", brief.plain(s))
        self.assertIn("Elpro GmbH (Germany)", brief.sentence_html(s))
        self.assertEqual(s["title"], "aurora claims Elpro GmbH (DE)", "the stored title is the program's own")


def every_code_cases():
    """For every code with a name: the name beside its code, and the code alone after other words."""
    out = []
    for cc, name in sorted(names().items()):
        out.append([f"{name} ({cc}): OONI anomaly/blocking rate 40%", [cc]])
        out.append([f"US embassy ({cc}): Security Alert", [cc]])
    return out


# The codes whose browser name (Intl.DisplayNames, short form) differs from the kit's: the ones that read twice
# ("Turkey (Türkiye)") when the page took the browser's names.
DIFFER_JS = """Object.entries(NAMES).filter(([cc, n]) => {
  try {
    const b = new Intl.DisplayNames(["en"], { type: "region", fallback: "none" }).of(cc);
    return b && b.replace(/\\s*\\([^)]*\\)$/, "") !== n;
  } catch { return false; }
}).map(([cc]) => cc)"""


def run_page(cases):
    """What the page module shows for each case, and the codes the browser names differently."""
    node = shutil.which("node")
    if node is None:
        raise AssertionError("node is needed (CI installs it for npm test)")
    mod = (ROOT / "site" / "country-names.js").as_uri()
    data = (ROOT / "site" / "country-names-data.js").as_uri()
    script = (f"const m = await import({json.dumps(mod)});\n"
              f"const {{ NAMES }} = await import({json.dumps(data)});\n"
              f"const cases = {json.dumps(cases, ensure_ascii=False)};\n"
              f"console.log(JSON.stringify({{shown: cases.map(([t, c]) => m.withCountryNames(t, c)), "
              f"differ: {DIFFER_JS}}}));")
    out = subprocess.run([node, "--input-type=module", "-e", script], capture_output=True, text=True,
                         encoding="utf-8", timeout=60)
    if out.returncode != 0:
        raise AssertionError(out.stderr)
    return json.loads(out.stdout)


class OneList(unittest.TestCase):
    def test_every_code_is_named_once_by_the_brief(self):
        for cc, name in names().items():
            with self.subTest(cc):
                self.assertEqual(with_country_names(f"{name} ({cc}): x", [cc]), f"{name} ({cc}): x")
                self.assertEqual(with_country_names(f"US embassy ({cc}): x", [cc]), f"US embassy ({name}): x")

    def test_the_page_reads_the_same_names_as_the_brief(self):
        self.assertEqual(PAGE_NAMES.read_text(encoding="utf-8"), page_module(),
                         "site/country-names-data.js is out of date: run python tools/country_names.py --write")


class Page(unittest.TestCase):
    def test_the_page_names_codes_with_the_kits_names(self):
        got = run_page([[t, c] for t, c, _ in CASES])
        self.assertEqual(got["shown"], [want for _, _, want in CASES])

    def test_the_page_never_names_a_country_twice(self):
        cases = every_code_cases()
        got = run_page(cases)
        self.assertEqual(got["shown"], [with_country_names(t, c) for t, c in cases], "the page and the brief agree")
        for (text, _), shown in zip(cases, got["shown"]):
            if not text.startswith("US embassy"):
                self.assertEqual(shown, text, "a name beside its own code is left alone")
        for cc in ("TR", "US", "PS", "CD", "HK"):
            self.assertIn(cc, got["differ"], "the browser spells these differently, so the test covers them")
        self.assertGreaterEqual(len(got["differ"]), 20, got["differ"])

    def test_the_page_applies_it_to_titles_summaries_and_rows(self):
        cop = (ROOT / "site" / "cop.js").read_text(encoding="utf-8")
        self.assertIn('import { withCountryNames } from "./country-names.js";', cop)
        self.assertIn("p.title = withCountryNames(p.title, p.countries);", cop)
        self.assertIn("p.summary = withCountryNames(p.summary, p.countries);", cop)
        self.assertLess(cop.index("p.title = withCountryNames"), cop.index("const BY_ID = new Map"),
                        "named before anything reads the items")
        self.assertIn("rowTitle(row)", cop)
        self.assertIn("rowTitle(r.outage)", cop)
        place = (ROOT / "site" / "place.js").read_text(encoding="utf-8")
        self.assertIn("withCountryNames(it.title, it.countries)", place)
        js = (ROOT / "site" / "country-names.js").read_text(encoding="utf-8")
        self.assertNotIn("innerHTML", js)


if __name__ == "__main__":
    unittest.main()
