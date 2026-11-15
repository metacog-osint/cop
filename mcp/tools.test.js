// Run with `node --test mcp/` from COP's root (needs Python for the fixture build). No network.
import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import * as T from "./tools.js";
import { NOW, fixtureData, readJson } from "./testkit.js";

const dir = fixtureData();
const index = readJson(dir, "agent/index.json");
const programs = readJson(dir, "agent/programs.json");
const verdict = readJson(dir, "agent/verdict.json");
const events = readJson(dir, "agent/events.json");
const picks = readJson(dir, "picks.json");
const triage = readJson(dir, "triage.json");
const shard = (n) => readJson(dir, `agent/items/${String(n).padStart(2, "0")}.json`);
const rows = T.rowsOf(index);

test("rows come out as objects with every column", () => {
  assert.equal(rows.length, index.rows.length);
  assert.deepEqual(Object.keys(rows[0]), index.fields);
  assert.equal(T.rowsOf(index), rows, "converted once per index document");
});

test("distance: to a point, to a box, and zero inside a box", () => {
  const pt = { point: [0, 0], bbox: null };
  assert.ok(Math.abs(T.distanceKm(pt, 0, 1) - 111.2) < 0.5);
  const box = { point: [1, 1], bbox: [0, 0, 2, 2] };
  assert.equal(T.distanceKm(box, 1, 1), 0);
  assert.ok(Math.abs(T.distanceKm(box, 1, 3) - 111.2) < 0.5);
  assert.equal(T.distanceKm({ point: null, bbox: null }, 0, 0), null);
});

test("search: every filter narrows, warnings and anomalies come first, newest first", () => {
  const all = T.searchItems(index, { limit: 50 }, NOW);
  assert.equal(all.matched, rows.length);
  assert.equal(all.returned, 50);
  assert.match(all.more, /more/);
  const rank = { warning: 0, anomaly: 1, routine: 2 };
  for (let i = 1; i < all.items.length; i++) {
    const a = all.items[i - 1], b = all.items[i];
    assert.ok(rank[a.tier] <= rank[b.tier], "tier order");
    if (a.tier === b.tier) assert.ok(String(a.data_as_of) >= String(b.data_as_of), "newest first");
  }
  const cordon = T.searchItems(index, { program: ["cordon"], limit: 50 }, NOW);
  assert.ok(cordon.items.every((r) => r.program === "cordon"));
  assert.ok(cordon.matched > 0 && cordon.matched < rows.length);
  const anomalies = T.searchItems(index, { tier: "anomaly", limit: 50 }, NOW);
  assert.ok(anomalies.items.every((r) => r.tier === "anomaly"));
  const withCountry = rows.find((r) => r.countries.length);
  const cc = T.searchItems(index, { country: withCountry.countries[0].toLowerCase(), limit: 50 }, NOW);
  assert.ok(cc.items.every((r) => r.countries.includes(withCountry.countries[0])));
  const word = withCountry.title.split(/\W+/).find((w) => w.length > 4);
  const byText = T.searchItems(index, { text: word }, NOW);
  assert.ok(byText.items.every((r) => (r.title + r.id).toLowerCase().includes(word.toLowerCase())));
  assert.ok(byText.items.every((r) => !("shard" in r)), "shard numbers stay internal");
});

test("search near a point returns only items within range, nearest first", () => {
  const placed = rows.find((r) => r.point);
  const [lon, lat] = placed.point;
  const res = T.searchItems(index, { near: { lat, lon, km: 300 }, limit: 50 }, NOW);
  assert.ok(res.items.some((r) => r.id === placed.id));
  assert.ok(res.items.every((r) => r.distance_km <= 300));
  for (let i = 1; i < res.items.length; i++) {
    const a = res.items[i - 1], b = res.items[i];
    if (a.tier === b.tier) assert.ok(a.distance_km <= b.distance_km);
  }
});

test("search since keeps only newer data and refuses a bad time", () => {
  const since = "2026-10-31T00:00:00Z";
  const res = T.searchItems(index, { since, limit: 50 }, NOW);
  assert.ok(res.items.every((r) => Date.parse(r.data_as_of || r.published_at) >= Date.parse(since)));
  assert.throws(() => T.searchItems(index, { since: "last tuesday" }, NOW), T.ToolError);
});

test("search refuses an unknown program and names the real ones", () => {
  assert.throws(() => T.searchItems(index, { program: ["tempo"] }, NOW), (e) => {
    assert.ok(e instanceof T.ToolError);
    assert.match(e.message, /Unknown program "tempo".*cordon/);
    return true;
  });
});

test("every result says when its data is from and whom to credit", () => {
  const results = [
    T.searchItems(index, {}, NOW),
    T.topPicks(index, picks, NOW),
    T.outageTriage(index, triage, {}, NOW),
    T.listForecasts(verdict, {}, NOW),
    T.forecastRecord(verdict, NOW),
    T.upcomingEvents(verdict, events, {}, NOW),
  ];
  for (const r of results) {
    assert.ok(r.as_of, "as_of");
    assert.equal(typeof r.age_hours, "number");
    assert.ok(Array.isArray(r.attribution) && r.attribution.length > 0, "attribution");
  }
  const s = T.suiteStatus(programs, NOW);
  assert.equal(s.as_of, index.bus_generated_at);
});

test("get_item returns the full item with its sources, or a clear miss", () => {
  const r = rows.find((x) => x.program === "vigil") || rows[0];
  const res = T.getItem(index, shard(r.shard), r.id, NOW);
  assert.equal(res.item.properties.id, r.id);
  assert.ok(Array.isArray(res.item.properties.sources) && res.item.properties.sources.length > 0);
  assert.ok(res.item.properties.sources.every((s) => s.licence));
  assert.equal(res.program.id, r.program);
  assert.throws(() => T.getItem(index, null, "nope:1", NOW), /No item "nope:1"/);
});

test("top picks keep COP's order and reasons", () => {
  const res = T.topPicks(index, picks, NOW);
  assert.ok(picks.picks.length > 0, "the fixtures make picks at their own time");
  assert.equal(res.picks.length, picks.picks.length);
  res.picks.forEach((p, i) => {
    assert.equal(p.rank, picks.picks[i].n);
    assert.equal(p.reason, picks.picks[i].reason);
  });
  assert.match(res.note, /not warnings/);
});

test("suite status lists planned programs with no COP entry", () => {
  const res = T.suiteStatus(programs, NOW);
  const planned = res.programs.filter((p) => p.status === "planned");
  assert.ok(planned.length > 0);
  assert.ok(planned.every((p) => p.in_cop === null));
  assert.ok(res.programs.some((p) => p.in_cop && p.in_cop.state));
});

test("forecasts filter by status and keep the settling rule", () => {
  const all = T.listForecasts(verdict, {}, NOW);
  assert.equal(all.count, 8);
  assert.ok(all.forecasts.every((f) => f.rule && f.probability >= 0 && f.probability <= 1 && f.likelihood));
  assert.equal(T.listForecasts(verdict, { status: "open" }, NOW).count, 8);
  assert.equal(T.listForecasts(verdict, { status: "resolved" }, NOW).count, 0);
});

test("the record says plainly when nothing has resolved", () => {
  const res = T.forecastRecord(verdict, NOW);
  assert.equal(res.scores.resolved, 0);
  assert.match(res.note, /No forecast has resolved yet/);
});

test("upcoming events stay inside the window and can be filtered by origin", () => {
  const res = T.upcomingEvents(verdict, events, { days: 30 }, NOW);
  assert.deepEqual(res.window, { from: "2026-10-31", to: "2026-11-30" });
  assert.ok(res.count > 0);
  assert.ok(res.events.every((e) => e.date >= "2026-10-31" && e.date <= "2026-11-30"));
  const cordon = T.upcomingEvents(verdict, events, { days: 366, origin: "cordon" }, NOW);
  assert.ok(cordon.count > 0 && cordon.events.every((e) => e.origin === "cordon"));
});

test("Orbit's scheduled launches join Verdict's calendar, in date order, credited to Orbit", () => {
  const res = T.upcomingEvents(verdict, events, { days: 30 }, NOW);
  const launches = res.events.filter((e) => e.origin === "orbit");
  assert.equal(launches.length, 14);
  assert.ok(res.events.some((e) => e.origin === "verdict"));
  const cmp = (a, b) => a.date.localeCompare(b.date) || (a.at || "").localeCompare(b.at || "") || a.id.localeCompare(b.id);
  for (let i = 1; i < res.events.length; i++) assert.ok(cmp(res.events[i - 1], res.events[i]) < 0, res.events[i].id);
  for (const e of launches) {
    assert.equal(e.verified, false, e.id);
    assert.match(e.id, /^orbit:upcoming:/);
    assert.ok(e.at === null || e.at.startsWith(e.date), e.id);
  }
  assert.ok(launches.some((e) => e.at === null && e.net_precision === "Day"), "a day-only launch has no time");
  assert.ok(res.attribution.some((a) => a.startsWith("Orbit, The Intelligence Company")));
  assert.equal(res.orbit.state, "fresh");
  assert.match(res.note, /origin orbit marks scheduled orbital launches/);
  const only = T.upcomingEvents(verdict, events, { days: 30, origin: "orbit" }, NOW);
  assert.equal(only.count, 14);
  assert.ok(!only.attribution.includes(verdict.attribution), "Verdict is not credited for Orbit's launches alone");
});

test("a launch whose stated time has passed is left out", () => {
  const first = events.events[0]; // Starship, 20:47 UTC on the fixtures' own day
  const before = T.upcomingEvents(verdict, events, { origin: "orbit" }, NOW);
  assert.ok(before.events.some((e) => e.id === first.id));
  const after = T.upcomingEvents(verdict, events, { origin: "orbit" }, Date.parse(first.at) + 60 * 1000);
  assert.ok(!after.events.some((e) => e.id === first.id));
});

test("each half of the calendar stands without the other, and both missing is refused", () => {
  const noCalendar = { ...verdict, calendar: null, missing: ["calendar"] };
  const res = T.upcomingEvents(noCalendar, events, { days: 30 }, NOW);
  assert.equal(res.count, 14);
  assert.deepEqual(res.missing, ["verdict calendar"]);
  assert.equal(res.as_of, events.bus_generated_at);
  assert.throws(() => T.upcomingEvents(noCalendar, events, { origin: "verdict" }, NOW), /could not be fetched/);
  const noEvents = T.upcomingEvents(verdict, null, { days: 30 }, NOW);
  assert.ok(noEvents.count > 0 && noEvents.events.every((e) => e.origin !== "orbit"));
  assert.deepEqual(noEvents.missing, ["program events"]);
  assert.throws(() => T.upcomingEvents(verdict, null, { origin: "orbit" }, NOW), /were not built/);
  assert.throws(() => T.upcomingEvents(null, null, {}, NOW), /could not be fetched/);
});

test("a Verdict part that could not be fetched is refused, not invented", () => {
  const broken = { ...verdict, scores: null, missing: ["scores"] };
  assert.throws(() => T.forecastRecord(broken, NOW), /could not be fetched/);
});

test("outage triage gives each layer's answer, the wording, and the question only as a draft", () => {
  const res = T.outageTriage(index, triage, {}, NOW);
  assert.equal(res.count, 2);
  assert.match(res.note, /not a warning, not a forecast and not a finding of cause/);
  assert.match(res.note, /question_draft .* not a registered Verdict forecast/);
  assert.equal(res.base_rates, null);
  assert.match(res.base_rates_note, /No base rates/);
  for (const r of res.records) {
    assert.deepEqual(Object.keys(r.layers), ["elements", "cordon", "watchfloor"]);
    for (const L of Object.values(r.layers)) assert.ok(L.showed.length > 10 && L.name, "each layer says what it showed");
    assert.ok(!("verdict_draft" in r), "the working draft, with its empty probability field, is not public");
    assert.equal(r.question_draft.status, "draft, not registered");
    assert.match(r.question_draft.question, /^Will OONI confirm blocking/);
    assert.ok(r.question_draft.rule_draft && r.question_draft.window_ends);
    assert.ok(r.reading in res.readings);
  }
  assert.ok(!JSON.stringify(res.records).includes("probability"));
  const credit = res.attribution.join(" ");
  for (const name of ["Blackout", "Elements", "Cordon", "Watchfloor"]) assert.match(credit, new RegExp(name));
});

test("outage triage: a country with no outage, a dead Blackout feed and a missing file are three different answers", () => {
  const none = T.outageTriage(index, triage, { country: "fr" }, NOW);
  assert.equal(none.count, 0);
  assert.match(none.none, /No outage for FR/);
  const dead = T.outageTriage(index, { ...triage, records: [], layer_states: { ...triage.layer_states, blackout: "stale" } }, {}, NOW);
  assert.match(dead.none, /no current data/);
  assert.match(dead.none, /not the same as no outages/);
  assert.throws(() => T.outageTriage(index, null, {}, NOW), /was not built for this snapshot/);
});

test("a layer with no current data reads incomplete and says so, never quiet", () => {
  const r = structuredClone(triage.records[0]);
  // What tools/triage.py writes when Elements is stale (tests/test_triage.py covers the Python side).
  r.reading = "incomplete";
  r.layers.elements = { ...r.layers.elements, state: "stale", showed: "No current data." };
  const res = T.outageTriage(index, { ...triage, records: [r] }, {}, NOW);
  assert.equal(res.records[0].layers.elements.showed, "No current data.");
  assert.match(res.readings.incomplete, /Silence is not quiet/);
});

// The free Workers plan allows about 10 ms of CPU per request. Node and workerd run the same V8, so time the work a
// request does here: parsing the index the first time an isolate sees it, then a search. COP_AGENT_DATA can point
// at a real site/data/ folder (after tools/agent_index.py) to time the real snapshot.
test("a cold request's work fits well inside the CPU budget", () => {
  const real = process.env.COP_AGENT_DATA;
  const base = real && existsSync(path.join(real, "agent", "index.json")) ? real : dir;
  const text = readFileSync(path.join(base, "agent", "index.json"), "utf8");
  const runs = 20;
  let parse = 0, search = 0;
  for (let i = 0; i < runs; i++) {
    const t0 = performance.now();
    const idx = JSON.parse(text);
    const t1 = performance.now();
    T.searchItems(idx, { tier: "anomaly", limit: 50 }, NOW);
    T.searchItems(idx, { near: { lat: 15.5, lon: 32.5, km: 1000 } }, NOW);
    search += performance.now() - t1;
    parse += t1 - t0;
  }
  const p = parse / runs, s = search / runs;
  console.log(`index ${Math.round(text.length / 1024)} KB from ${base === dir ? "fixtures" : base}: parse ${p.toFixed(2)} ms, two searches ${s.toFixed(2)} ms`);
  // Two searches on a freshly parsed index stand in for one cold request with room to spare. On the real 6 Nov
  // snapshot (956 items, 278 KB): parse 1.5 ms + two searches 0.6 ms, after the switch to filtering raw rows
  // (turning every row into an object first made the searches 5.0 ms).
  assert.ok(p + s < 5, `cold request work ${(p + s).toFixed(2)} ms`);
});
