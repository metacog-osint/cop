// The page's picks list (site/pick-order.js) orders picks as top_picks does.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { orderPicks, risingNote } from "../site/pick-order.js";

const picks = ["a", "b", "c"].map((id, i) => ({ id, n: i + 1, reason: `strongest ${id}` }));
const place = (direction) => ({ name: "Gulf", direction, up: ["ships", "notices"] });

test("a pick in a Rising place is listed first and says so", () => {
  const out = orderPicks(picks, { picks: [{ pick: "c", rising: true, places: [place("rising")] }] });
  assert.deepEqual(out.map((p) => [p.id, p.n, p.picked]), [["c", 1, 3], ["a", 2, 1], ["b", 3, 2]]);
  assert.equal(risingNote(out[0]), "Listed first: it falls in a place COP reads as Rising: Gulf (ships, notices up)");
  assert.equal(risingNote(out[1]), "");
  assert.ok(out[0].reason.endsWith(risingNote(out[0])));
  assert.equal(out[1].reason, picks[0].reason);
});

test("Edging up never reorders, and without direction.json the build's order stands", () => {
  const edging = { picks: [{ pick: "c", rising: false, places: [place("edging_up")] }] };
  for (const direction of [edging, null, {}]) {
    const out = orderPicks(picks, direction);
    assert.deepEqual(out.map((p) => p.id), ["a", "b", "c"]);
    assert.ok(out.every((p) => risingNote(p) === ""));
  }
});

test("the page takes its picks through orderPicks", () => {
  const js = readFileSync(new URL("../site/cop.js", import.meta.url), "utf8");
  assert.match(js, /const PICKS = orderPicks\(picksDoc\.picks, direction\);/);
});
