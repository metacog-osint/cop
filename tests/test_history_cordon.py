"""Cordon's rebuilt notice history (cop-layer-history/1): python tests/test_history_cordon.py

Small synthetic captures stand in for the FCDO API, Wayback page captures, embassy feeds and State's advisories RSS.
The point of the datasets is that their rows turn back into the items Cordon would have carried and that triage's
`notices()` reads them: a security alert counts, a weather alert is listed and not counted, a changed FCDO statement
counts and a routine FCDO edit does not, and a State level raise is dated and directed from the rows.
"""
import gzip
import json
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import history_cordon_common as hc  # noqa: E402
import history_cordon_embassy as emb  # noqa: E402
import history_cordon_fcdo as fcdo  # noqa: E402
import history_cordon_state as state  # noqa: E402
import triage as tri  # noqa: E402

NOW = "2026-11-16T12:00:00Z"
START, END = date(2024, 12, 1), date(2025, 12, 31)


def utc(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def feed(*items):
    body = "".join(f"<item><title>{t}</title><link>{l}</link><pubDate>{p}</pubDate>"
                   f"<description><![CDATA[<p>{d}</p>]]></description></item>" for t, l, p, d in items)
    return f"<?xml version='1.0'?><rss version='2.0'><channel><title>Alert Archives</title>{body}</channel></rss>"


SECURITY = ("Security Alert &#8211; U. S. Embassy New Delhi (May 7, 2025)", "https://in.usembassy.gov/sec-alert/",
            "Thu, 08 May 2025 03:30:30 +0000", "Indian strikes in Pakistan")
WEATHER = ("Weather Alert &#8211; U.S. Consulate General Chennai, India", "https://in.usembassy.gov/wx-alert/",
           "Thu, 08 May 2025 01:00:00 +0000", "Heavy rain")


class Embassy(unittest.TestCase):
    def rows(self):
        items = emb.parse_feed(feed(SECURITY, WEATHER), "IN")
        return emb.build_rows("IN", [("20250510000000", items, True)], START, END, NOW, "in.usembassy.gov")

    def test_parse_reads_time_title_and_type(self):
        items = emb.parse_feed(feed(SECURITY, WEATHER), "IN")
        self.assertEqual(items[0]["at"], "2025-05-08T03:30:30Z")
        self.assertEqual(items[0]["title"], "Security Alert – U. S. Embassy New Delhi (May 7, 2025)")
        self.assertEqual(items[0]["type"], "security")
        self.assertEqual(items[1]["type"], "weather")

    def test_technical_difficulties_page_is_not_a_feed(self):
        with self.assertRaises(ValueError):
            emb.parse_feed("<html><title>Technical Difficulties</title></html>", "IN")

    def test_rows_carry_the_contract_fields(self):
        r = self.rows()[0]
        for k in ("place", "at", "precision", "source_id", "source_ref", "retrieved_at", "quality"):
            self.assertIn(k, r)
        self.assertEqual(r["precision"], "minute")
        self.assertTrue(r["source_ref"].startswith("https://web.archive.org/web/20250510000000/"))
        self.assertEqual(r["quality"], [])

    def test_triage_counts_a_security_alert_and_ignores_a_weather_one(self):
        at = utc("2025-05-08T06:00:00Z")
        items = emb.to_items(self.rows(), at)
        out = tri.notices(items, "IN", at)
        self.assertEqual([n["alert_type"] for n in out["recent"]], ["security"])
        self.assertEqual([n["alert_type"] for n in out["recent_not_counted"]], ["weather"])

    def test_items_after_the_outage_are_not_carried(self):
        at = utc("2025-05-08T02:00:00Z")           # the weather alert (01:00) is out, the security one (03:30) not yet
        ids = [i["properties"]["title"] for i in emb.to_items(self.rows(), at)]
        self.assertEqual(len(ids), 1)
        self.assertIn("Weather Alert", ids[0])

    def test_item_id_matches_cordons_rule(self):
        import hashlib
        link = SECURITY[1]
        self.assertEqual(emb.item_key("IN", link), "cordon:alert:IN:" + hashlib.sha256(link.encode()).hexdigest()[:12])

    def test_jerusalem_alerts_naming_the_west_bank_are_copied_to_palestine(self):
        wb = ("Security Alert: U.S. Embassy Jerusalem (West Bank)", "https://il.usembassy.gov/a/",
              "Mon, 03 Mar 2025 10:00:00 +0000", "Roads in the West Bank")
        plain = ("Security Alert: U.S. Embassy Jerusalem (Tel Aviv)", "https://il.usembassy.gov/b/",
                 "Mon, 03 Mar 2025 11:00:00 +0000", "Rockets")
        items = emb.parse_feed(feed(wb, plain), "IL")
        rows = emb.build_rows("IL", [("20250310000000", items, True)], START, END, NOW, "il.usembassy.gov")
        self.assertEqual(sorted((r["place"], r["title"].rsplit(" ", 2)[-1] if "Tel" not in r["title"] else "(Tel Aviv)") for r in rows),
                         sorted([("IL", "Bank)"), ("IL", "(Tel Aviv)"), ("PS", "Bank)")]))

    def test_spans_report_the_stretch_no_capture_covers(self):
        def ten(day0):
            return [{"at": f"2025-{day0[:2]}-{int(day0[3:]) + i:02d}T00:00:00Z"} for i in range(10)]
        caps = [("2025-03-25T00:00:00Z", ten("03-10"), True),      # span 10 Mar..25 Mar
                ("2025-07-01T00:00:00Z", ten("06-10"), True)]      # span 10 Jun..1 Jul
        gaps = emb.spans(caps, date(2025, 3, 1), date(2025, 7, 1))
        self.assertEqual(gaps[0], (date(2025, 3, 1), date(2025, 3, 10)))
        self.assertIn((date(2025, 3, 25), date(2025, 6, 10)), gaps)

    def test_a_short_feed_shows_everything_the_category_had(self):
        caps = [("2025-07-01T00:00:00Z", [{"at": "2025-02-01T00:00:00Z"}], True)]
        self.assertEqual(emb.spans(caps, date(2025, 1, 1), date(2025, 6, 1)), [])


WARN_PARTS = ('<html><title>GOV.UK</title><h2>Warnings and insurance</h2><strong class="govuk-warning-text__text">'
              '<abbr>FCDO</abbr> advises against all travel to parts of Myanmar (Burma) and all but essential travel '
              'to parts of Myanmar (Burma).</strong> Areas where FCDO advises against travel FCDO advises against '
              'all travel to: Chin State</html>')
WARN_WHOLE = ('<html><title>GOV.UK</title><h2>Warnings and insurance</h2><strong class="govuk-warning-text__text">'
              '<abbr>FCDO</abbr> advises against all travel to Syria.</strong></html>')
WARN_NONE = '<html><title>GOV.UK</title><h2>Warnings and insurance</h2> No warnings today.</html>'


class Fcdo(unittest.TestCase):
    def test_warning_box_is_read_and_the_areas_list_is_not(self):
        st, fl = fcdo.parse_warning(WARN_PARTS, ["Myanmar", "Burma", "Myanmar (Burma)"])
        self.assertEqual(st, ["avoid_all_travel_to_parts", "avoid_all_but_essential_travel_to_parts"])
        self.assertEqual(fl, [])

    def test_whole_country_none_and_not_a_page(self):
        self.assertEqual(fcdo.parse_warning(WARN_WHOLE, ["Syria"])[0], ["avoid_all_travel_to_whole_country"])
        self.assertEqual(fcdo.parse_warning(WARN_NONE, ["Syria"])[0], [])
        with self.assertRaises(ValueError):
            fcdo.parse_warning("<html>Error</html>", ["Syria"])

    ENTRIES = [{"at": "2025-03-10T10:00:00Z", "note": "Updated embassy contact details ('Health' page)."},
               {"at": "2025-04-08T11:59:39Z", "note": "FCDO now advises against all but essential travel to a region "
                                                       "('Warnings and insurance' page)."},
               {"at": "2025-05-20T09:00:00Z", "note": "Information about visas ('Entry requirements' page)."}]
    CAPS = [("20250301000000", [], []), ("20250420000000", ["avoid_all_but_essential_travel_to_parts"], []),
            ("20250515000000", ["avoid_all_but_essential_travel_to_parts"], [])]

    def rows(self):
        return fcdo.make_rows(("MM", "Myanmar", "myanmar", None), self.ENTRIES, self.CAPS, START, END, NOW)

    def test_the_update_about_advice_is_the_change(self):
        rows = self.rows()
        ch = [r for r in rows if r["alert_status_changed"]]
        self.assertEqual([r["at"] for r in ch], ["2025-04-08T11:59:39Z"])
        self.assertEqual(ch[0]["from_alert_status"], [])
        self.assertEqual(ch[0]["quality"], [])
        self.assertEqual([r["alert_status"] for r in rows],
                         [[], ["avoid_all_but_essential_travel_to_parts"], ["avoid_all_but_essential_travel_to_parts"]])

    def test_several_candidate_updates_make_the_date_an_estimate(self):
        ents = self.ENTRIES + [{"at": "2025-04-12T09:00:00Z", "note": "Summary - updated advice for the region."}]
        rows = fcdo.make_rows(("MM", "Myanmar", "myanmar", None), ents, self.CAPS, START, END, NOW)
        ch = [r for r in rows if r["alert_status_changed"]]
        self.assertEqual(len(ch), 1)
        self.assertIn("estimated-date", ch[0]["quality"])

    def test_updates_before_the_first_capture_have_no_statements(self):
        ents = [{"at": "2025-02-01T10:00:00Z", "note": "Edit."}] + self.ENTRIES
        rows = fcdo.make_rows(("MM", "Myanmar", "myanmar", None), ents, self.CAPS, START, END, NOW)
        self.assertEqual(rows[0]["alert_status"], None)
        self.assertIn("level-unknown", rows[0]["quality"])

    def test_triage_counts_a_changed_statement_and_not_an_edit(self):
        rows = self.rows()
        at = utc("2025-04-09T06:00:00Z")
        out = tri.notices(fcdo.to_items(rows, at), "MM", at)
        self.assertEqual(len(out["recent"]), 1)
        self.assertEqual(out["recent"][0]["id"], "cordon:fcdo:myanmar")
        at = utc("2025-03-11T06:00:00Z")           # only the contact-details edit: dated, no change of statements
        out = tri.notices(fcdo.to_items(rows, at), "MM", at)
        self.assertEqual(out["recent"], [])
        self.assertEqual([n["direction"] for n in out["recent_not_raised"]], ["unchanged"])

    def test_the_change_flag_runs_thirty_days(self):
        rows = self.rows()
        it = fcdo.to_items(rows, utc("2025-05-25T00:00:00Z"))[0]["properties"]      # 47 days after the change
        self.assertEqual(it["tier"], "routine")
        it = fcdo.to_items(rows, utc("2025-05-01T00:00:00Z"))[0]["properties"]      # 23 days after
        self.assertEqual(it["tier"], "anomaly")


RSS_HEAD = "<?xml version='1.0'?><rss><channel><title>t</title>"


def adv(title, pub, link, note=None):
    d = f"<![CDATA[<p><b><i>{note}</i></b></p><p>text</p>]]>" if note else "<![CDATA[<p>text</p>]]>"
    return (f"<item><title>{title}</title><pubDate>{pub}</pubDate><link>{link}</link>"
            f"<category domain='Country-Tag'>PK</category><description>{d}</description></item>")


OLD = "http://travel.state.gov/content/travel/en/traveladvisories/traveladvisories/pakistan-travel-advisory.html"
NEW = "https://travel.state.gov/content/tsg_aem/us/en/home/international-travel/travel-advisories/destination.pak.html"


class State(unittest.TestCase):
    def caps(self):
        c1 = RSS_HEAD + adv("Pakistan - Level 3: Reconsider Travel", "Thu, 01 May 2025", OLD) + "</channel></rss>"
        c2 = RSS_HEAD + adv("Pakistan - Level 4: Do Not Travel", "Sat, 10 May 2025", OLD,
                            "Reissued with updates.") + "</channel></rss>"
        c3 = RSS_HEAD + adv("Pakistan - Level 4: Do Not Travel", "Sat, 10 May 2025", NEW) + "</channel></rss>"
        return [("20250505000000", state.parse_rss(c1)), ("20250512000000", state.parse_rss(c2)),
                ("20251201000000", state.parse_rss(c3))]

    def test_dates_without_a_time_and_both_link_spellings_parse(self):
        items = self.caps()
        self.assertEqual(items[0][1][0]["date"], "2025-05-01")
        self.assertEqual(items[0][1][0]["slug"], "pakistan")
        self.assertEqual(items[2][1][0]["slug"], "destination.pak")
        self.assertEqual(items[1][1][0]["note"], "Reissued with updates.")

    def test_one_row_per_update_whichever_link_it_came_with(self):
        rows = state.build_rows(self.caps(), START, END, NOW)
        self.assertEqual([(r["at"], r["level"]) for r in rows], [("2025-05-01", 3), ("2025-05-10", 4)])

    def test_capture_gap_is_flagged_when_captures_are_far_apart(self):
        rows = state.build_rows(self.caps(), START, END, NOW)
        self.assertIn("capture-gap", rows[0]["quality"])           # first capture: nothing before it
        self.assertEqual(rows[1]["quality"], [])                    # 7 days after the capture before

    def test_triage_reads_a_raise_from_the_rows(self):
        rows = state.build_rows(self.caps(), START, END, NOW)
        at = utc("2025-05-11T12:00:00Z")
        out = tri.notices(state.to_items(rows, at), "PK", at, levels=state.levels_from_rows(rows))
        self.assertEqual([(n["level"], n["direction"]) for n in out["recent"]], [(4, "raised")])
        it = state.to_items(rows, at)[0]["properties"]
        self.assertEqual((it["tier"], it["data_as_of_precision"]), ("anomaly", "day"))

    def test_a_reissue_at_the_same_level_is_not_raised(self):
        c = RSS_HEAD + adv("Pakistan - Level 3: Reconsider Travel", "Thu, 01 May 2025", OLD)
        d = RSS_HEAD + adv("Pakistan - Level 3: Reconsider Travel", "Mon, 12 May 2025", OLD, "Reissued.")
        caps = [("20250505000000", state.parse_rss(c + "</channel></rss>")),
                ("20250513000000", state.parse_rss(d + "</channel></rss>"))]
        rows = state.build_rows(caps, START, END, NOW)
        at = utc("2025-05-13T00:00:00Z")
        out = tri.notices(state.to_items(rows, at), "PK", at, levels=state.levels_from_rows(rows))
        self.assertEqual(out["recent"], [])
        self.assertEqual([n["direction"] for n in out["recent_not_raised"]], ["unchanged"])

    def test_the_israel_page_dates_an_update_with_no_single_level(self):
        page = ('<h1>Israel</h1><h3 class="x-alertheader-type-frame"><div class="tsg-rwd-emergency-alertheader-'
                'type-frame-typebox ">Travel Advisory<br>January 17, 2025</div></h3>'
                '<h3 class="tsg-rwd-emergency-alertheader-title notips">See Individual Summaries</h3>'
                '<div class="tsg-rwd-emergency-alert-text"><p><b>Updated to reflect the security situation.</b></p>')
        pg = state.parse_page(page)
        self.assertEqual((pg["date"], pg["level"], pg["level_text"]), ("2025-01-17", None, "See Individual Summaries"))
        rows = state.page_rows("israel-west-bank-and-gaza", [("20250120000000", pg)], START, END, NOW)
        self.assertEqual(sorted(r["place"] for r in rows), ["IL", "PS"])
        self.assertIn("level-unknown", rows[0]["quality"])
        at = utc("2025-01-18T00:00:00Z")
        out = tri.notices(state.to_items(rows, at), "PS", at)
        self.assertEqual([n["direction"] for n in out["recent"]], ["unknown"])


class Common(unittest.TestCase):
    def test_gzip_without_a_header_is_unpacked(self):
        self.assertEqual(hc._unzip(gzip.compress(b"hello")), b"hello")
        self.assertEqual(hc._unzip(b"plain"), b"plain")

    def test_cdx_rows_become_dicts_and_an_empty_answer_is_an_empty_list(self):
        class Fake:
            def __init__(self, body):
                self.body = body

            def get(self, url):
                return self.body, 200
        rows = hc.cdx(Fake(json.dumps([["timestamp", "original"], ["20250101000000", "http://x/"]]).encode()), "x")
        self.assertEqual(rows, [{"timestamp": "20250101000000", "original": "http://x/"}])
        self.assertEqual(hc.cdx(Fake(b""), "x"), [])

    def test_client_cache_answers_a_second_time_without_a_request(self):
        with tempfile.TemporaryDirectory() as d:
            c = hc.Client(cache=d, min_gap=0)
            p = c._path("http://example.invalid/a")
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"body")
            p.with_suffix(".meta").write_text(json.dumps({"url": "x", "status": 200, "at": NOW}))
            self.assertEqual(c.get("http://example.invalid/a"), (b"body", 200))
            self.assertEqual((c.requests, c.hits), (0, 1))

    def test_every_country_has_a_slug_and_iso_and_the_order_is_toms(self):
        self.assertEqual([c[0] for c in hc.COUNTRIES][:6], ["MM", "IN", "PK", "RU", "IQ", "PS"])
        self.assertEqual(len(hc.COUNTRIES), 24)
        self.assertTrue(all(c[2] for c in hc.COUNTRIES + hc.COUNTRIES_EXTRA))

    def test_manifests_carry_the_contract_fields(self):
        fields = {"contract", "dataset_id", "layer", "title", "description", "sources", "derived_from", "method",
                  "coverage", "gaps", "biases", "rows", "row_schema", "files", "redistribution", "built_at",
                  "built_by", "uses", "not_for", "supersedes"}
        rows = state.build_rows([("20250505000000", [{"slug": "pakistan", "name": "Pakistan", "level": 3,
                                                      "level_text": "x", "date": "2025-05-01", "link": OLD,
                                                      "tag": "PK", "note": None}])], START, END, NOW)
        m = state.manifest(rows, START, END, NOW, "abc", 1, [], [])
        self.assertEqual(fields - set(m), set())
        self.assertEqual(m["contract"], "cop-layer-history/1")
        self.assertEqual(m["redistribution"], "rows")
        for r in rows:
            self.assertIn(r["source_id"], {s["id"] for s in m["sources"]})

    def test_the_contract_is_copied_unchanged(self):
        self.assertTrue((ROOT / "history" / "CONTRACT.md").read_text(encoding="utf-8").startswith(
            "# COP layer history: how rebuilt historical data is labelled (`cop-layer-history/1`)"))


class Committed(unittest.TestCase):
    """Whatever datasets are committed under history/cordon keep their manifests honest."""

    def test_rows_match_their_manifest_and_sources(self):
        root = ROOT / "history" / "cordon"
        for man in sorted(root.glob("*/manifest.json")):
            m = json.loads(man.read_text(encoding="utf-8"))
            rows = [json.loads(l) for l in (man.parent / "rows.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(m["rows"]["count"], len(rows), man.parent.name)
            ids = {s["id"] for s in m["sources"]}
            for r in rows:
                self.assertIn(r["source_id"], ids)
                self.assertIn(r["precision"], ("minute", "hour", "day"))
                self.assertTrue(r["source_ref"])
                self.assertIsInstance(r["quality"], list)
            self.assertLess(len((man.parent / "rows.jsonl").read_bytes()), 5_000_000)
            import hashlib
            self.assertEqual(m["files"][0]["sha256"], hashlib.sha256((man.parent / "rows.jsonl").read_bytes()).hexdigest())
            self.assertTrue(m["gaps"] is not None and m["biases"])


if __name__ == "__main__":
    unittest.main()
