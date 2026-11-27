// COP share card: one item drawn as a PNG in the browser, in two sizes, with its status, data time (zone named),
// source publishers, the program's credit and the link that reopens the same view. Text only, no map image, so the
// card carries nothing the page did not already show. Layout is a pure function (tested in tests/share-card.test.js
// with a stand-in for measureText): the body is laid out top down and the footer bottom up, and a body block that
// would reach the footer is cut to the lines that fit, so no two pieces of text overlap or touch at either size.

export const SIZES = {
  wide: { w: 1200, h: 630, label: "1200 × 630 (link preview)", titleLines: 3 },
  square: { w: 1080, h: 1080, label: "1080 × 1080 (square)", titleLines: 7 },
};

const SANS = 'system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif';
const INK = "#13202E", BODY = "#34404F", MUT = "#5A6472", RULE = "#DCDED9", BG = "#FFFFFF";

// Break `text` into at most `max` lines no wider than `width`; a cut last line ends with an ellipsis.
export function wrap(text, width, max, measure) {
  const words = String(text || "").replace(/\s+/g, " ").trim().split(" ").filter(Boolean);
  const lines = [];
  let cur = "";
  for (let i = 0; i < words.length; i++) {
    let w = words[i];
    while (measure(w) > width && w.length > 1) {          // a single word wider than the line: break it
      let k = w.length - 1;
      while (k > 1 && measure(w.slice(0, k)) > width) k--;
      if (cur) { lines.push(cur); cur = ""; }
      lines.push(w.slice(0, k));
      w = w.slice(k);
    }
    const next = cur ? `${cur} ${w}` : w;
    if (measure(next) <= width) { cur = next; continue; }
    lines.push(cur);
    cur = w;
  }
  if (cur) lines.push(cur);
  if (lines.length <= max) return lines;
  const out = lines.slice(0, max);
  let last = out[max - 1];
  while (last && measure(`${last}…`) > width) last = last.replace(/\s*\S+$/, "") || last.slice(0, -1);
  out[max - 1] = `${last}…`;
  return out;
}

// `info`: { brand, program, family, color, title, status: [..], asOf, sources, credit, url }.
// `measure(text, font)` returns a width in px. Returns { w, h, pad, rules: [...], blocks: [{ font, color, lh, x, y,
// lines, top, bottom }], bar } in canvas pixels; every block's top..bottom is its own band, none shared.
export function layout(info, size, measure) {
  const { w, h } = size;
  const s = w / 1200;                                         // type scales with the card's width
  const pad = Math.round(64 * s), gap = Math.round(18 * s), inner = w - 2 * pad;
  const f = (px, weight = 400) => ({ font: `${weight} ${Math.round(px * s)}px ${SANS}`, lh: Math.round(px * s * 1.3) });
  const blocks = [];
  // Footer, bottom up: link, then credit.
  let bottom = h - pad;
  const foot = [
    { text: info.url, ...f(22), color: MUT, max: 1 },
    { text: info.credit, ...f(20), color: MUT, max: 2 },
  ];
  for (const b of foot) {
    if (!b.text) continue;
    const lines = wrap(b.text, inner, b.max, (t) => measure(t, b.font));
    const top = bottom - lines.length * b.lh;
    blocks.push({ ...b, x: pad, lines, top, bottom });
    bottom = top - Math.round(gap / 2);
  }
  const ruleY = bottom - Math.round(gap / 2);
  const limit = ruleY - gap;                                  // the body stops this far above the rule
  // Body, top down.
  let y = pad;
  const body = [
    { text: info.brand, ...f(22, 600), color: MUT, max: 1 },
    { text: `${info.program}${info.family ? ` · ${info.family}` : ""}`, ...f(26, 600), color: INK, max: 1, dot: true },
    { text: info.title, ...f(46, 700), color: INK, max: size.titleLines || 3, gapBefore: gap },
    ...(info.status || []).map((t, i) => ({ text: t, ...f(24, 600), color: BODY, max: 1, mark: true, need: i === 0 })),
    { text: info.asOf ? `Data as of ${info.asOf}` : "", ...f(24), color: BODY, max: 1, gapBefore: gap, need: true },
    { text: info.sources ? `Sources: ${info.sources}` : "", ...f(24), color: BODY, max: 2, need: true },
  ].filter((b) => b.text);
  // A long title or many status words give way first: the room the blocks still to come must have (a line each for
  // the first status, the data time and the sources) is held back from every block before them.
  const held = (k) => body.slice(k + 1).filter((b) => b.need)
    .reduce((n, b) => n + b.lh + Math.round(gap / 3) + (b.gapBefore || 0), 0);
  for (let k = 0; k < body.length; k++) {
    const b = body[k];
    if (b.gapBefore) y += b.gapBefore;
    const indent = b.dot || b.mark ? Math.round(30 * s) : 0;
    const room = Math.floor((limit - y - held(k)) / b.lh);
    if (room < 1) { if (b.gapBefore) y -= b.gapBefore; continue; }   // no room: leave it out, never overlap
    const lines = wrap(b.text, inner - indent, Math.min(b.max, room), (t) => measure(t, b.font));
    blocks.push({ ...b, x: pad + indent, lines, top: y, bottom: y + lines.length * b.lh });
    y += lines.length * b.lh + Math.round(gap / 3);
  }
  return { w, h, pad, s, ruleY, blocks, bar: Math.max(8, Math.round(12 * s)) };
}

export function draw(info, size, doc = document) {
  const c = doc.createElement("canvas");
  c.width = size.w;
  c.height = size.h;
  const ctx = c.getContext("2d");
  const measure = (t, font) => { ctx.font = font; return ctx.measureText(t).width; };
  const L = layout(info, size, measure);
  ctx.fillStyle = BG;
  ctx.fillRect(0, 0, L.w, L.h);
  ctx.fillStyle = info.color || INK;                           // the program's colour, a band down the left edge
  ctx.fillRect(0, 0, L.bar, L.h);
  ctx.fillStyle = RULE;
  ctx.fillRect(L.pad, L.ruleY, L.w - 2 * L.pad, Math.max(1, Math.round(2 * L.s)));
  ctx.textBaseline = "top";
  for (const b of L.blocks) {
    ctx.font = b.font;
    ctx.fillStyle = b.color;
    b.lines.forEach((line, i) => ctx.fillText(line, b.x, b.top + i * b.lh + Math.round((b.lh - b.lh / 1.3) / 2)));
    if (b.dot || b.mark) {
      const r = Math.round(9 * L.s), cy = b.top + Math.round(b.lh / 2);
      ctx.fillStyle = b.dot ? (info.color || INK) : MUT;
      if (b.dot) { ctx.beginPath(); ctx.arc(L.pad + r, cy, r, 0, 2 * Math.PI); ctx.fill(); }
      else ctx.fillRect(L.pad + Math.round(r / 2), cy - Math.round(r / 2), r, r);
    }
  }
  return c;
}

// Draw and hand the viewer a PNG named for the item and size.
export function save(info, sizeKey, name, doc = document) {
  const c = draw(info, SIZES[sizeKey], doc);
  c.toBlob((blob) => {
    if (!blob) return;
    const a = doc.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${name}-${SIZES[sizeKey].w}x${SIZES[sizeKey].h}.png`;
    doc.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 10000);
  }, "image/png");
}
