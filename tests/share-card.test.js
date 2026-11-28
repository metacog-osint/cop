// The share card's layout (site/share-card.js) at both sizes: every line inside the card's margins, and no two
// blocks of text overlapping or touching, with short and very long items. measureText is stood in for by a width of
// 0.6 em a character (wider than the system sans faces, so real text has more room than this test gives it).
import { test } from "node:test";
import assert from "node:assert/strict";
import { SIZES, layout, wrap } from "../site/share-card.js";

const px = (font) => Number(/(\d+)px/.exec(font)[1]);
const measure = (t, font) => t.length * 0.6 * px(font);
const long = (n, w) => Array.from({ length: n }, (_, i) => `${w}${i}`).join(" ");

const ITEMS = {
  short: { brand: "COP · The Intelligence Company", program: "Vigil", family: "Cyber", title: "LockBit claims a victim",
    status: ["Measured or reported, not a judgment"], asOf: "27 Nov 2026, 4:00 PM CST", sources: "RansomLook",
    credit: "Vigil, The Intelligence Company. Credit: RansomLook (CC BY 4.0).", url: "https://cop.theintelligence.company/#i=x" },
  long: { brand: "COP · The Intelligence Company", program: "Blackout", family: "Infrastructure",
    title: long(60, "outage"), status: [long(20, "badge"), long(20, "status"), long(20, "more")],
    asOf: "27 Nov 2026, 4:00 PM Central Standard Time", sources: long(40, "publisher"),
    credit: long(200, "licence"), url: `https://cop.theintelligence.company/#i=${"x".repeat(400)}` },
};

for (const [sk, size] of Object.entries(SIZES)) {
  for (const [ik, info] of Object.entries(ITEMS)) {
    test(`${sk} card, ${ik} item: inside the margins, nothing overlaps or touches`, () => {
      const L = layout(info, size, measure);
      const bands = L.blocks.map((b) => [b.top, b.bottom]).sort((a, b) => a[0] - b[0]);
      for (const b of L.blocks) {
        assert.ok(b.top >= L.pad && b.bottom <= L.h - L.pad, `block ${b.text.slice(0, 20)} outside the margins`);
        for (const line of b.lines) assert.ok(b.x + measure(line, b.font) <= L.w - L.pad + 0.5, `line too wide: ${line.slice(0, 30)}`);
        assert.ok(b.bottom < L.ruleY || b.top > L.ruleY, "text across the rule");
      }
      for (let i = 1; i < bands.length; i++) assert.ok(bands[i][0] > bands[i - 1][1], "two blocks touch or overlap");
      const texts = L.blocks.map((b) => b.lines.join(" "));
      assert.ok(texts.some((t) => t.startsWith(info.program)), "program shown");
      assert.ok(texts.some((t) => t.startsWith("Data as of")), "data time shown");
      assert.ok(texts.some((t) => t.startsWith("https://")), "link shown");
      assert.ok(texts.some((t) => t.startsWith("Sources:")), "sources shown");
    });
  }
}

test("wrap cuts with an ellipsis and breaks a word wider than the line", () => {
  const m = (t) => t.length * 10;
  assert.deepEqual(wrap("aaaa bbbb cccc", 90, 1, m), ["aaaa…"]);
  assert.ok(wrap("x".repeat(30), 100, 5, m).every((l) => m(l) <= 100));
});
