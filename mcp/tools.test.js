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
const threeSignal = readJson(dir, "three-signal.json");
const shard = (n) => readJson(dir, `agent/items/${String(n).padStart(2, "0")}.json`);
const rows = T.rowsOf(index);

test("rows come out as objects with every column", () => {
  assert.equal(rows.length, index.rows.length);
  assert.deepEqual(Object.keys(rows[0]), index.fields);
  assert.equal(T.rowsOf(index), rows, "converted once per index document");
});

test("coded columns come out as words, and an index without codes searches the same", () => {
  assert.ok(index.codes && Array.isArray(index.codes.tier), "the built index codes its labels");
  const i = index.fields.indexOf("tier");
  assert.ok(index.rows.every((r) => Number.isInteger(r[i])));
  assert.ok(rows.every((r) => ["warning", "anomaly", "routine"].includes(r.tier)));
  assert.ok(rows.every((r) => typeof r.stale === "boolean"));
  const plain = { ...index, codes: undefined, rows: rows.map((o) => index.fields.map((k) => o[k])) };
  for (const args of [{ limit: 50 }, { program: ["cordon"], limit: 50 }, { tier: "anomaly" }, { kind: "observation", limit: 20 }]) {
    assert.deepEqual(T.searchItems(plain, args, NOW), T.searchItems(index, args, NOW), JSON.stringify(args));
  }
  const picked = rows.find((r) => r.tier === "anomaly") || rows[0];
  assert.deepEqual(T.findRow(index, picked.id), picked);
  assert.equal(T.searchItems(index, { kind: "no-such-kind" }, NOW).matched, 0);
  assert.equal(T.searchItems(index, { tier: "no-such-tier" }, NOW).matched, 0);
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
  const expired = rows.filter((r) => T.isExpired(r.valid_until, NOW)).length;
  assert.equal(all.matched + (all.expired_left_out || 0), rows.length);
  assert.equal(all.expired_left_out || 0, expired);
  assert.equal(all.returned, 50);
  assert.match(all.more, /pass offset 50/);
  assert.equal(all.next_offset, 50);
  const next = T.searchItems(index, { limit: 50, offset: 50 }, NOW);
  assert.equal(next.offset, 50);
  assert.equal(next.matched, all.matched);
  const seen = new Set(all.items.map((x) => x.id));
  for (const x of next.items) assert.ok(!seen.has(x.id), "pages don't overlap");
  if (rows.length <= 100) assert.equal(next.next_offset, undefined);
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

test("search leaves out items past their valid_until unless asked, and every row says expired (ideas#164)", () => {
  const vi = index.fields.indexOf("valid_until");
  assert.ok(vi >= 0, "the index carries valid_until");
  const all = T.searchItems(index, { limit: 50, include_expired: true }, NOW);
  assert.ok(all.items.every((r) => typeof r.expired === "boolean"));
  // Expire one row by copying the index with its valid_until an hour before NOW.
  const target = index.rows[0];
  const id = target[index.fields.indexOf("id")];
  const past = new Date(Number(NOW) - 3600 * 1000).toISOString();
  const copy = { ...index, rows: index.rows.map((r) => (r === target ? Object.assign([...r], { [vi]: past }) : r)) };
  const shown = T.searchItems(copy, { text: id, limit: 50 }, NOW);
  assert.ok(!shown.items.some((r) => r.id === id), "left out by default");
  assert.ok(shown.expired_left_out >= 1 && /include_expired/.test(shown.expired_note));
  const asked = T.searchItems(copy, { text: id, limit: 50, include_expired: true }, NOW);
  const row = asked.items.find((r) => r.id === id);
  assert.ok(row && row.expired === true, "listed and marked when asked");
  assert.equal(T.isExpired(null, NOW), false);
  assert.equal(T.isExpired(new Date(Number(NOW) + 1000).toISOString(), NOW), false);
});

test("a pick that passed its valid_until after the build says expired", () => {
  const first = picks.picks[0];
  const vi = index.fields.indexOf("valid_until");
  const ii = index.fields.indexOf("id");
  const past = new Date(Number(NOW) - 60 * 1000).toISOString();
  const copy = { ...index, rows: index.rows.map((r) => (r[ii] === first.id ? Object.assign([...r], { [vi]: past }) : r)) };
  const out = T.topPicks(copy, picks, NOW);
  assert.equal(out.picks[0].expired, true);
  assert.ok(out.picks.slice(1).every((p) => p.missing || p.expired === false || p.expired === true));
  assert.match(out.note, /valid_until/);
});

test("search by state matches only items placed in that state", () => {
  // kit 0.5.7 subdivisions (Tom, 15 Nov 2026): a hazard in Hawaii is not one for the whole US.
  const fields = index.fields;
  const mk = (id, countries, subs) => fields.map((k) => ({ id, program: "elements", kind: "observation",
    tier: "routine", title: id, countries, subdivisions: subs, point: null, bbox: null, shard: 0 })[k] ?? null);
  const small = { ...index, rows: [mk("elements:a", ["US"], ["US-HI"]), mk("elements:b", ["US"], ["US-ND", "US-MN"]),
    mk("elements:c", ["US"], [])] };
  assert.deepEqual(T.searchItems(small, { state: "us-hi" }, NOW).items.map((r) => r.id), ["elements:a"]);
  assert.deepEqual(T.searchItems(small, { state: "US-MN" }, NOW).items.map((r) => r.id), ["elements:b"]);
  assert.equal(T.searchItems(small, { country: "US" }, NOW).matched, 3);
  assert.ok(fields.includes("subdivisions"));
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

// Watchfloor and Context both read GDELT: one voice, joined on zone number (suite review, 15 Nov 2026).
test("top picks give GDELT one slot a round and never two picks for one zone", () => {
  const res = T.topPicks(index, picks, NOW);
  const gdelt = res.picks.filter((p) => p.program === "watchfloor" || p.program === "context");
  assert.equal(gdelt.length, 1, "the fixtures had a Watchfloor and a Context pick: one voice, one slot");
  const voices = res.picks.flatMap((p) => p.voice || []);
  assert.equal(voices.length, new Set(voices).size);
  assert.match(res.note, /one voice/);
});

test("a GDELT pick carries both programs' readings, each with its own id, program and licence", () => {
  const ctx = rows.find((r) => r.id === "context:2026-10-31_1200Z:14_DRC_Rwanda");
  const wf = rows.find((r) => r.id === "watchfloor:2026-10-30:zone:14_DRC_Rwanda");
  assert.ok(ctx && wf, "fixture rows");
  const doc = {
    picks: [{
      n: 1, id: ctx.id, program: "context", reason: "Context's strongest anomaly with a place", voice: ["gdelt:Z14"],
      readings: [
        { id: ctx.id, program: "context", tier: "anomaly", warning_problem_id: "CX-WP-Z14", strength: "237 articles" },
        { id: wf.id, program: "watchfloor", tier: "routine", warning_problem_id: "WF-WP-Z14", strength: null },
      ],
    }],
  };
  const res = T.topPicks(index, doc, NOW);
  assert.equal(res.picks.length, 1, "one press row, not two picks");
  const [p] = res.picks;
  assert.deepEqual(p.voice, ["gdelt:Z14"]);
  assert.deepEqual(p.readings.map((r) => [r.id, r.program]), [[ctx.id, "context"], [wf.id, "watchfloor"]]);
  for (const r of p.readings) {
    assert.ok(r.title && r.link, "each reading keeps its own title and link");
    assert.equal(r.licence, index.programs[r.program].licence);
    assert.ok(!("shard" in r));
  }
  assert.equal(p.readings[1].tier, "routine");
  const credits = res.attribution.join("\n");
  for (const prog of ["context", "watchfloor"]) assert.ok(credits.includes(index.programs[prog].attribution), prog);
});

test("suite status lists planned programs with no COP entry", () => {
  const res = T.suiteStatus(programs, NOW);
  const planned = res.programs.filter((p) => p.status === "planned");
  assert.ok(planned.length > 0);
  assert.ok(planned.every((p) => p.in_cop === null));
  assert.ok(res.programs.some((p) => p.in_cop && p.in_cop.state));
});

test("suite status carries the hub's page check, null where there is none", () => {
  const plain = T.suiteStatus(programs, NOW);
  assert.equal(plain.page_check, null);
  assert.ok(plain.programs.every((p) => p.page_check === null));
  assert.match(plain.note, /page_check/);
  const checked = structuredClone(programs);
  checked.page_check = { finished_at: "2026-11-18T05:11:27Z", pages: 19, passed: 18, failed: ["cordon"] };
  const cordon = checked.programs.find((p) => p.id === "cordon");
  cordon.page_check = { ok: false, checked_at: "2026-11-18T05:11:27Z", problems: ["map did not draw"] };
  const res = T.suiteStatus(checked, NOW);
  assert.deepEqual(res.page_check.failed, ["cordon"]);
  assert.equal(res.programs.find((p) => p.id === "cordon").page_check.ok, false);
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
    assert.match(r.question_draft.question, /^Will OONI measure blocking/);
    assert.ok(r.question_draft.rule_draft.includes("aggregation API (api.ooni.io, probe_cc="));
    assert.ok(r.question_draft.rule_draft && r.question_draft.window_ends);
    assert.ok(r.reading in res.readings);
  }
  assert.ok(!JSON.stringify(res.records).includes("probability"));
  // The STOP replay: how often documented shutdowns read each way, beside the same countries' ordinary days.
  assert.match(res.note, /replay_rate .* never say triage detects, finds or predicts shutdowns/);
  assert.equal(res.replay.attribution, "Access Now, #KeepItOn STOP");
  assert.equal(res.replay.three_layer.matched_shutdowns, 157);
  assert.ok(res.replay.caveats.some((c) => /not a test of whether triage finds shutdowns/.test(c)));
  // Caveats per run (18 Nov 2026): the shared list never says Cordon is not replayed while the three-layer run says
  // how it was; each run's own caveats sit with its pool.
  assert.ok(!res.replay.caveats.some((c) => /not replayed yet/.test(c)), "the shared caveats do not contradict the three-layer run");
  assert.ok(res.replay.two_layer.caveats.some((c) => /not replayed yet/.test(c)));
  assert.ok(res.replay.three_layer.caveats.some((c) => /Cordon's history/.test(c)));
  assert.match(res.note, /replay_rate\.caveats/);
  assert.match(res.replay.caution, /does not show that a reading identifies a shutdown or its cause/);
  for (const r of res.records) {
    const x = r.replay_rate;
    if (r.reading === "unconfirmed") {
      assert.equal(x, null, "a single-source dip has no replay figure: STOP's shutdowns are all confirmed ones");
      continue;
    }
    assert.ok(x, `${r.country} has a replay figure for its reading`);
    assert.equal(x.reading, r.reading);
    assert.ok(x.shutdown_share >= 0 && x.shutdown_share <= 1 && x.ordinary_day_share >= 0 && x.ordinary_day_share <= 1);
    assert.match(x.text, /of shutdowns and on [\d.]+% of those countries' ordinary days/);
    assert.doesNotMatch(x.text, /detect|predict|likely/i);
    if (x.pool === "three_layer") assert.deepEqual(x.caveats, res.replay.three_layer.caveats, "its own run's caveats");
    else assert.ok(!x.caveats.some((c) => /Cordon's history/.test(c)), "no three-layer caveat on a two-layer figure");
  }
  assert.ok(res.records.some((r) => r.replay_rate), "at least one record carries a replay figure");
  assert.ok(res.attribution.includes("Access Now, #KeepItOn STOP"));
  const noReplay = T.outageTriage(index, { ...triage, replay: null }, {}, NOW);
  assert.match(noReplay.replay_note, /No STOP replay figures/);
  assert.ok(!noReplay.attribution.includes("Access Now, #KeepItOn STOP"));
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

// Until 17 Nov 2026 the free Workers plan allowed about 10 ms of CPU per request; Workers Paid allows 30 s, so this
// now guards answer time rather than a hard cap. Node and workerd run the same V8, so time the work a request does here: parsing the index the first time an isolate sees it, then a search. COP_AGENT_DATA can point
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
  // 25 ms: well under any visible delay, and loose enough for a busy laptop (the 5 ms bound of the free-plan days
  // failed there even on untouched main).
  assert.ok(p + s < 25, `cold request work ${(p + s).toFixed(2)} ms`);
});

test("three-signal gives the reading, the layers' items and the wording, and a missing file is a tool error", () => {
  const res = T.threeSignal(index, threeSignal, {}, NOW);
  assert.equal(res.join, "three-signal");
  assert.equal(res.count, 1);
  const r = res.records[0];
  assert.equal(r.area, "hormuz");
  assert.ok(["converged", "partial", "none", "incomplete"].includes(r.reading));
  assert.deepEqual(Object.keys(r.signals), ["interference", "notice", "connectivity", "press"]);
  assert.ok(Array.isArray(r.signals_at_usual_rate), "join 0.3: signals present only at their usual rate");
  assert.match(res.note, /signals_at_usual_rate/);
  assert.ok(r.weather && typeof r.weather.text === "string");
  assert.match(res.note, /Do not state what the pattern means/);
  // Join 0.4: UKMTO's reports are context, credited to UKMTO via Wake, counted only on a surge.
  assert.ok(r.context.ukmto && Array.isArray(r.context.ukmto.reports), "context.ukmto");
  assert.equal(typeof r.context.ukmto.counts, "boolean");
  assert.match(r.context.ukmto.credit, /Royal Navy/);
  assert.match(res.note, /context\.ukmto/);
  assert.match(res.note, /not a finding/);
  assert.equal(r.verdict_draft, undefined, "the working draft with its empty probability is not published");
  assert.deepEqual(res.attribution, T.threeSignal(index, threeSignal, { area: "hormuz" }, NOW).attribution);
  assert.ok(res.attribution.length > 1, "COP's credit plus the programs it reads");
  assert.equal(T.threeSignal(index, threeSignal, { area: "hormuz" }, NOW).count, 1);
  assert.throws(() => T.threeSignal(index, threeSignal, { area: "baltic" }, NOW), T.ToolError);
  assert.throws(() => T.threeSignal(index, null, {}, NOW), /not built for this snapshot/);
});
