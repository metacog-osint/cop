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
from country_names import country_name, with_country_names  # noqa: E402

# (text, the item's countries, what the page should show with the browser's names)
CASES = [
    ("aurora claims Elpro GmbH (DE)", ["DE"], "aurora claims Elpro GmbH (Germany)"),
    ("US embassy (MX): Demonstration Alert", ["MX"], "US embassy (Mexico): Demonstration Alert"),
    ("payload(s): USA 123 (US), USA 124 (US).", ["US"], "payload(s): USA 123 (United States), USA 124 (United States)."),
    # A code the item does not list stays: these are acronyms, not countries.
    ("circumventing the antidumping duty (AD) and a general license (GL)", ["CN"],
     "circumventing the antidumping duty (AD) and a general license (GL)"),
    ("Cloudflare marked it verified (TP).", ["PH"], "Cloudflare marked it verified (TP)."),
    # A code already beside its name stays as written.
    ("Iran (IR): OONI anomaly/blocking rate 34.0%", ["IR"], "Iran (IR): OONI anomaly/blocking rate 34.0%"),
    ("Myanmar (MM): OONI anomaly/blocking rate 24.3%", ["MM"], "Myanmar (MM): OONI anomaly/blocking rate 24.3%"),
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


class Page(unittest.TestCase):
    def test_the_page_names_codes_with_the_browsers_names(self):
        node = shutil.which("node")
        self.assertIsNotNone(node, "node is needed (CI installs it for npm test)")
        mod = (ROOT / "site" / "country-names.js").as_uri()
        cases = [[t, c] for t, c, _ in CASES]
        script = (f"import({json.dumps(mod)}).then((m) => console.log(JSON.stringify("
                  f"{json.dumps(cases)}.map(([t, c]) => m.withCountryNames(t, c)))))")
        out = subprocess.run([node, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout), [want for _, _, want in CASES])

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
