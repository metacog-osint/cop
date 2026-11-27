// The history tool: weeks read, the range, churn left out by default, programs with no rows named. No network.
import { test } from "node:test";
import assert from "node:assert/strict";
import * as T from "./tools.js";

const NOW = Date.parse("2026-11-26T12:00:00Z");
const index = { programs: { cordon: { attribution: "Cordon" }, vigil: { attribution: "Vigil" } } };
const manifest = {
  built_at: "2026-11-26T00:30:00Z",
  first_day: "2026-10-31",
  last_day: "2026-11-25",
  read_days: { cordon: ["2026-11-17", "2026-11-18", "2026-11-19"], vigil: ["2026-11-18"], orbit: ["2026-11-18"] },
  programs: { cordon: {}, vigil: {}, orbit: {} },
  files: [{ name: "readings-2026-11.parquet", rows: 3, bytes: 100 }],
};
const RUNS = ["program", "subject_id", "item_id", "from", "to", "days", "kind", "tier", "value", "value_text", "title",
  "method_version", "note", "link"];
const EVS = ["at", "program", "subject_id", "item_id", "change", "tier", "title", "method_version", "churn", "note", "source"];
const week = {
  place: "IR",
  week: "2026-11-16",
  runs: {
    cols: RUNS,
    rows: [
      ["cordon", "cordon:alert:ir", "cordon:alert:ir-1", "2026-11-17", "2026-11-19", 3, "observation", "routine", null,
        null, "Alert", "cordon-1.2", null, null],
      ["cordon", "cordon:old", "cordon:old-1", "2026-11-01", "2026-11-02", 2, "observation", "anomaly", null, null,
        "Old", "cordon-1.0", null, null],
    ],
  },
  events: {
    cols: EVS,
    rows: [
      ["2026-11-18T02:20:00Z", "vigil", "vigil:x", "vigil:2", "new", "routine", "x", "vigil-1.0", true, null, "cop-ledger"],
      ["2026-11-18T04:20:00Z", "cordon", "cordon:alert:ir", "cordon:alert:ir-1", "new", "routine", "Alert", "cordon-1.2",
        false, null, "cop-ledger"],
    ],
  },
};

test("weeks are the Mondays the range touches", () => {
  assert.deepEqual(T.historyWeeks("2026-11-18", "2026-11-24"), ["2026-11-16", "2026-11-23"]);
  assert.deepEqual(T.historyWeeks("2026-11-16", "2026-11-16"), ["2026-11-16"]);
});

test("a week for a country: readings in range, churn left out, days read counted", () => {
  const res = T.history(index, manifest, [week], { place: "ir", start: "2026-11-16", days: 7 }, NOW);
  assert.equal(res.place, "IR");
  assert.equal(res.to, "2026-11-22");
  const cordon = res.programs.find((p) => p.program === "cordon");
  assert.equal(cordon.days_read, 3);
  assert.deepEqual(cordon.readings.map((r) => r.subject_id), ["cordon:alert:ir"]); // the 1-2 Nov run is outside
  assert.equal(cordon.events.length, 1);
  const vigil = res.programs.find((p) => p.program === "vigil");
  assert.equal(vigil.churn_left_out, 1);
  assert.equal(vigil.events.length, 0);
  assert.deepEqual(res.no_rows_here, [{ program: "orbit", days_read: 1 }]);
  assert.ok(res.files[0].url.endsWith("/data/history/readings-2026-11.parquet"));
  const all = T.history(index, manifest, [week], { place: "IR", start: "2026-11-16", include_churn: true }, NOW);
  assert.equal(all.programs.find((p) => p.program === "vigil").events.length, 1);
});

test("before the history starts, and bad input", () => {
  const res = T.history(index, manifest, [], { place: "gulf", start: "2026-10-01" }, NOW);
  assert.match(res.before_history, /2026-10-31/);
  assert.throws(() => T.history(index, null, [], { place: "IR" }, NOW), T.ToolError);
  assert.throws(() => T.history(index, manifest, [], { place: "I" }, NOW), T.ToolError);
  assert.throws(() => T.history(index, manifest, [], { place: "IR", start: "18 Nov" }, NOW), T.ToolError);
});
