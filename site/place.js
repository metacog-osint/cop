// Place pages: draws site/data/places/<area>.json (tools/place.py). Text goes in as text, never as HTML.
import * as ml from "./vendor/maplibre-gl/maplibre-gl.mjs";
import { coverageClass } from "./vendor/tic-doctrine.js";

const T = window.TicTime;
const $ = (id) => document.getElementById(id);
const SVG = "http://www.w3.org/2000/svg";
const STATE = { fresh: "Fresh", late: "Late", stale: "No current data", missing: "No data" };
const dark = matchMedia("(prefers-color-scheme: dark)");

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null) continue;
    if (k === "text") el.textContent = v; else el.setAttribute(k, v);
  }
  for (const k of kids.flat()) if (k != null) el.append(k);
  return el;
}
function s(tag, attrs) {
  const el = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs || {})) if (v != null) el.setAttribute(k, v);
  return el;
}
const timeEl = (iso, year) => (iso ? h("time", { datetime: iso, "data-tic": "datetime", "data-tic-year": year ? "" : null }, iso)
  : h("span", { text: "unknown" }));
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
// History days are closed UTC dates: shown as dates, never moved into the viewer's zone.
const day = (d) => (d ? `${Number(d.slice(8, 10))} ${MONTHS[Number(d.slice(5, 7)) - 1]}` : "?");
const getJSON = async (u) => { const r = await fetch(u, { cache: "no-cache" }); if (!r.ok) throw new Error(r.status); return r.json(); };

// --- the outline map ------------------------------------------------------------------------------------------------
function circle([lon, lat], km, n = 128) {
  const R = 6371.0088, d = km / R, la = (lat * Math.PI) / 180, lo = (lon * Math.PI) / 180, pts = [];
  for (let i = 0; i <= n; i++) {
    const b = (2 * Math.PI * i) / n;
    const la2 = Math.asin(Math.sin(la) * Math.cos(d) + Math.cos(la) * Math.sin(d) * Math.cos(b));
    const lo2 = lo + Math.atan2(Math.sin(b) * Math.sin(d) * Math.cos(la), Math.cos(d) - Math.sin(la) * Math.sin(la2));
    pts.push([(((lo2 * 180) / Math.PI + 540) % 360) - 180, (la2 * 180) / Math.PI]);
  }
  return { type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: [pts] } };
}
function bounds(fc) {
  let w = 180, so = 90, e = -180, n = -90;
  const walk = (c) => { if (typeof c[0] === "number") { w = Math.min(w, c[0]); e = Math.max(e, c[0]); so = Math.min(so, c[1]); n = Math.max(n, c[1]); } else c.forEach(walk); };
  fc.features.forEach((f) => walk(f.geometry.coordinates));
  return [[w, so], [e, n]];
}
async function drawMap(def) {
  const box = $("place-map");
  if (!box || def.kind === "worldwide") { box && box.remove(); return; }
  let outline, what;
  if (def.kind === "circle") {
    outline = { type: "FeatureCollection", features: [circle(def.center, def.radius_km)] };
    what = `The area is everything within ${def.radius_km.toLocaleString("en")} km of ${def.center[1].toFixed(2)}° N, ${def.center[0].toFixed(2)}° E, drawn as that circle on the ground. Blackout's country outages and Cordon's embassy alerts count here by the country they name instead: ${def.countries.join(", ")}.`;
  } else {
    const world = await getJSON("data/world.geojson");
    outline = { type: "FeatureCollection", features: world.features.filter((f) => def.countries.includes(f.properties.cc)) };
    what = `The area is these countries: ${def.countries.join(", ")}. Borders from Natural Earth; an item counts here when its program names one of them.`;
  }
  $("map-note").textContent = what;
  try {
    const health = await getJSON("data/health.json");
    const b = dark.matches ? health.config.basemaps.dark : health.config.basemaps.light;
    const map = new ml.Map({ container: box, style: b.style, bounds: bounds(outline), fitBoundsOptions: { padding: 24 },
      dragRotate: false, pitchWithRotate: false, cooperativeGestures: true, attributionControl: { compact: true } });
    map.on("load", () => {
      map.addSource("place", { type: "geojson", data: outline });
      map.addLayer({ id: "place-fill", type: "fill", source: "place", paint: { "fill-color": "#e3a83b", "fill-opacity": 0.08 } });
      map.addLayer({ id: "place-line", type: "line", source: "place", paint: { "line-color": dark.matches ? "#f0c060" : "#9a6a10", "line-width": 2 } });
    });
    map.on("error", () => { box.classList.add("map-off"); });
  } catch (e) {
    box.replaceChildren(h("p", { class: "note", text: "The map could not be drawn here; the outline is described below it." }));
  }
}

// --- one layer ------------------------------------------------------------------------------------------------------
function chart(w, name) {
  // Drawn at the width it is shown, so the 11 px labels stay 11 px on a phone.
  const W = Math.round(Math.min(680, Math.max(280, ($("body").clientWidth || 600) - 34))), H = 120, P = { l: 30, r: 8, t: 8, b: 20 };
  const vals = w.items, n = vals.length, max = Math.max(1, ...vals.filter((v) => v != null));
  const x = (i) => P.l + (i * (W - P.l - P.r)) / Math.max(1, n - 1);
  const y = (v) => H - P.b - (v * (H - P.t - P.b)) / max;
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, class: "pl-chart", role: "img",
    "aria-label": `${name}: items a day from ${day(w.start)} to ${day(w.end)} (UTC dates); the table below gives each day.` });
  w.notes.forEach((note, i) => { if (note) svg.append(s("rect", { class: "pl-noted", x: x(i) - (W - P.l - P.r) / (2 * Math.max(1, n - 1)), y: P.t, width: (W - P.l - P.r) / Math.max(1, n - 1), height: H - P.t - P.b })); });
  svg.append(s("line", { class: "pl-axis", x1: P.l, x2: W - P.r, y1: y(0), y2: y(0) }));
  for (const v of [0, max]) {
    const t = s("text", { class: "pl-tick", x: P.l - 6, y: y(v) + 4, "text-anchor": "end" }); t.textContent = String(v); svg.append(t);
  }
  for (const i of [0, n - 1]) {
    const t = s("text", { class: "pl-tick", x: x(i), y: H - 4, "text-anchor": i ? "end" : "start" }); t.textContent = day(i ? w.end : w.start); svg.append(t);
  }
  let d = "", pen = false;
  vals.forEach((v, i) => { if (v == null) { pen = false; return; } d += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`; pen = true; });
  svg.append(s("path", { class: "pl-line", d }));
  vals.forEach((v, i) => {
    if (v != null && (i === n - 1 || vals[i + 1] == null) && (i === 0 || vals[i - 1] == null)) svg.append(s("circle", { class: "pl-dot", cx: x(i), cy: y(v), r: 3 }));
  });
  // Hover: one hit band per day, wider than the mark, with the day's figures.
  const date0 = new Date(`${w.start}T00:00:00Z`);
  vals.forEach((v, i) => {
    const dd = new Date(date0.getTime() + i * 864e5).toISOString().slice(0, 10);
    const r = s("rect", { class: "pl-hit", x: x(i) - (W - P.l - P.r) / (2 * Math.max(1, n - 1)), y: 0, width: (W - P.l - P.r) / Math.max(1, n - 1), height: H });
    const t = s("title");
    t.textContent = v == null ? `${day(dd)}: not read` : `${day(dd)}: ${v} item${v === 1 ? "" : "s"}, ${w.anomalies[i]} unusual${w.notes[i] ? ` · ${w.notes[i]}` : ""}`;
    r.append(t); svg.append(r);
  });
  return svg;
}
function table(w) {
  const date0 = new Date(`${w.start}T00:00:00Z`);
  const rows = w.items.map((v, i) => {
    const dd = new Date(date0.getTime() + i * 864e5).toISOString().slice(0, 10);
    return h("tr", {}, h("td", { text: dd }), h("td", { text: v == null ? "not read" : String(v) }),
      h("td", { text: v == null ? "" : String(w.anomalies[i]) }), h("td", { text: w.notes[i] || "" }));
  }).reverse();
  return h("details", { class: "pl-table" }, h("summary", { text: "Each day as a table" }),
    h("table", {}, h("thead", {}, h("tr", {}, ["Day (UTC)", "Items", "Unusual", "Note"].map((c) => h("th", { text: c })))), h("tbody", {}, rows)));
}
function trendBlock(w, name, share) {
  const label = `${w.days}-day line`;
  if (!w.enough) {
    return h("div", { class: "pl-trend" }, h("h4", { text: label }),
      h("p", { class: "rr-meta", text: `Too little history: read on ${w.read_days} of the last ${w.days} days, and a line is drawn only from ${Math.round(share * 100)}% of them. COP's history starts on 31 Oct 2026.` }));
  }
  const noted = w.notes.filter(Boolean);
  return h("div", { class: "pl-trend" }, h("h4", { text: `${label}, items a day` }),
    h("p", { class: "rr-meta", text: `${day(w.start)} to ${day(w.end)} (UTC dates) · read on ${w.read_days} of ${w.days} days · median ${w.median} a day${noted.length ? ` · shaded days carry a note: ${[...new Set(noted)].join("; ")}` : ""}` }),
    chart(w, name), table(w));
}
function layerBox(prog, now, hist, share) {
  const name = now.name || prog;
  const kids = [h("h3", { text: name }),
    h("p", { class: "rr-meta" }, h("span", { class: `st st-${now.state}`, text: STATE[now.state] || now.state }), " · data as of ", timeEl(now.data_as_of)),
    h("p", { class: "pl-now", text: `Now: ${now.items} item${now.items === 1 ? "" : "s"} placed here, ${now.anomalies} unusual against ${name}'s own baseline.` })];
  if (now.top.length) {
    kids.push(h("ul", { class: "rr-items" }, now.top.map((it) => h("li", {}, h("span", { text: it.title }),
      h("span", { class: "rr-item-meta" }, `${it.tier} · data `, timeEl(it.data_as_of, true), it.stale ? " · last good copy" : null, " · ",
        h("a", { href: `./#r=1&i=${encodeURIComponent(it.id)}`, text: "On the map" }))))));
  }
  if (hist) {
    hist.windows.forEach((w) => kids.push(trendBlock(w, name, share)));
    if (hist.method_versions.length > 1) kids.push(h("p", { class: "rr-meta", text: `Method versions in this history: ${hist.method_versions.join(", ")}. A change of method can move a line.` }));
  } else {
    kids.push(h("p", { class: "rr-meta", text: `No history for ${name} here: it has placed nothing in this area since 31 Oct 2026.` }));
  }
  return h("section", { class: "rr-layer" }, kids);
}

// --- coverage (kit format tic-coverage/1) ----------------------------------------------------------------------------
// How many independent source groups stand behind what COP holds for a place. The class labels and their thresholds
// are the kit's (coverageClass); the basis is COP's own, stated wherever a row is shown.
const KIT_LADDER = "https://github.com/metacog-osint/tic-doctrine/blob/main/docs/event-ladder.md";
const ASIDE = { party_claim: "party claim", state_media: "state media" };
const progNames = (ids, names) => ids.map((p) => names[p] || p).join(", ");
const groupsText = (n) => `${n} independent source group${n === 1 ? "" : "s"}`;
// A table written before kit 0.6.17 has no not_counted list: read it as empty.
const notCounted = (r) => (Array.isArray(r.not_counted) ? r.not_counted : []);
function coverageNote(method) {
  const url = (method && method.url) || KIT_LADDER;
  return h("p", { class: "rr-meta" },
    `${coverageClass(3).label}: 3 or more independent source groups. ${coverageClass(1).label}: 1 or 2. `
    + `${coverageClass(0, 1).label}: none, but COP holds items for the place from sources it sets aside or does not `
    + "count, so the place was read. "
    + `${coverageClass(0).label}: COP holds no item for the place from a source read on time. `
    + "A source group is one publisher, or outlets that share one origin (all of GDELT's readings, for example), "
    + "counted once however many programs carry it. The count is made from the sources behind the items COP holds "
    + "for the place at this snapshot, from programs and sources read on time; stale copies and items their program "
    + "says have ended are not counted. A source that watches a place but has nothing to report there is missed, so "
    + "each count is a lower bound, and it says nothing about how good the sources are or how often they report. "
    + "Party claims and state media are listed but set aside: they never raise a place's class. Sources the suite "
    + "makes itself (a program's own reading, computation or model, such as Vis's weather forecasts) are not about "
    + "the place, so they are listed as not counted. ",
    h("a", { href: `${url}#the-coverage-table`, rel: "noopener", text: "How coverage is counted" }));
}
function coverageTable(cov, names) {
  if (!cov || !Array.isArray(cov.places)) {
    return h("p", { class: "note", text: "The coverage table was not built for this picture. That says nothing about any place." });
  }
  const rows = cov.places.map((r) => h("tr", {},
    h("th", { scope: "row" }, h("a", { href: `place.html?area=${encodeURIComponent(r.place)}`, text: r.name })),
    h("td", { text: coverageClass(r).label }),
    h("td", { class: "pl-num", text: String(r.independent) }),
    h("td", { text: progNames(r.programs, names) || "none" }),
    h("td", { class: "pl-num", text: String(r.set_aside.length) }),
    h("td", { class: "pl-num", text: String(notCounted(r).length) })));
  return h("section", { class: "pl-coverage", "aria-labelledby": "coverage-h" },
    h("h2", { id: "coverage-h", text: "Coverage" }),
    h("p", { class: "rr-meta" }, "How many independent sources stand behind what COP holds for each place now, counted at the picture built ",
      timeEl(cov.generated_at), "."),
    h("div", { class: "doc-table-wrap" }, h("table", { class: "doc-table" },
      h("thead", {}, h("tr", {}, ["Place", "Coverage", "Independent groups", "Programs", "Set aside", "Not counted"].map((c) => h("th", { scope: "col", text: c })))),
      h("tbody", {}, rows))),
    coverageNote(cov));
}
function coverageBox(row, method, names) {
  const kids = [h("h3", { text: "Coverage" }),
    h("p", { class: "pl-now" }, h("strong", { text: coverageClass(row).label }),
      `: ${groupsText(row.independent)} behind what COP holds here now.`)];
  if (row.groups.length) {
    kids.push(h("ul", { class: "rr-items" }, row.groups.map((g) => h("li", {}, h("span", { text: g.label }),
      h("span", { class: "rr-item-meta", text: `Carried by ${progNames(g.programs, names)}` })))));
  }
  if (row.set_aside.length) {
    kids.push(h("p", { class: "rr-meta pl-aside", text: "Set aside, never raising the class:" }),
      h("ul", { class: "rr-items" }, row.set_aside.map((g) => h("li", {}, h("span", { text: g.label }),
        h("span", { class: "rr-item-meta", text: `${ASIDE[g.why] || g.why} · carried by ${progNames(g.programs, names)}` })))));
  }
  if (notCounted(row).length) {
    kids.push(h("p", { class: "rr-meta pl-aside", text: "Not counted, the suite's own sources:" }),
      h("ul", { class: "rr-items" }, notCounted(row).map((g) => h("li", {}, h("span", { text: g.label }),
        h("span", { class: "rr-item-meta", text: `Carried by ${progNames(g.programs, names)}` })))));
  }
  kids.push(coverageNote(method));
  return h("section", { class: "rr-layer pl-coverage" }, kids);
}

// --- the page -------------------------------------------------------------------------------------------------------
async function index() {
  const ix = await getJSON("data/places/index.json");
  // The coverage table and the programs' names are read beside the list; the list stands without them.
  const [cov, health] = await Promise.all([getJSON("data/places/coverage.json").catch(() => null), getJSON("data/health.json").catch(() => null)]);
  const names = Object.fromEntries(((health && health.config && health.config.programs) || []).map((p) => [p.id, p.name]));
  $("body").replaceChildren(h("ul", { class: "pl-index" }, ix.areas.map((a) => h("li", {}, h("a", { href: `place.html?area=${encodeURIComponent(a.area)}`, text: a.name })))),
    coverageTable(cov, names));
  $("bar-times").replaceChildren("Picture built ", timeEl(ix.snapshot_generated_at));
}
async function place(id) {
  const doc = await getJSON(`data/places/${encodeURIComponent(id)}.json`);
  document.title = `${doc.name} · Places · COP`;
  $("title").textContent = doc.name;
  const hist = doc.history || {};
  const layers = Object.entries(doc.layers).filter(([p, l]) => l.items || (hist.layers && hist.layers[p]));
  const none = Object.entries(doc.layers).filter(([p, l]) => !l.items && !(hist.layers && hist.layers[p])).map(([, l]) => l.name);
  $("body").replaceChildren(
    doc.definition.kind === "worldwide" ? (doc.definition.counts ? h("p", { class: "rr-meta", text: doc.definition.counts }) : null) : h("figure", { class: "pl-figure" }, h("div", { id: "place-map", class: "pl-map" }), h("figcaption", { id: "map-note", class: "rr-meta" })),
    hist.available ? h("p", { class: "rr-meta", text: `History: closed UTC days ${day(hist.first_day)} to ${day(hist.last_day)}, from COP's daily snapshots, its ledger and the programs' own logs. A day a program was not read has no value, never a zero.` })
      : h("p", { class: "note", text: "COP's history set was not available in this build, so no lines are drawn. That says nothing about the place." }),
    doc.coverage ? coverageBox(doc.coverage, doc.coverage_method, Object.fromEntries(Object.entries(doc.layers).map(([p, l]) => [p, l.name || p]))) : null,
    ...layers.map(([p, l]) => layerBox(p, l, hist.layers && hist.layers[p], hist.trend_share || 0.8)),
    none.length ? h("p", { class: "rr-meta", text: `Nothing placed here now or since 31 Oct 2026: ${none.join(", ")}.` }) : null);
  $("bar-times").replaceChildren("Picture built ", timeEl(doc.snapshot_generated_at));
  $("credits").replaceChildren(h("p", { text: "Each layer is its own program's count, The Intelligence Company; sources are named on each item on the map. Base map © OpenFreeMap, © OpenStreetMap contributors." }));
  if (T) T.renderAll(document);
  drawMap(doc.definition);
}

if (T) { T.mountSettings($("time-settings")); T.onChange(() => T.renderAll(document)); }
const id = new URLSearchParams(location.search).get("area");
(id ? place(id) : index()).catch(() => {
  $("body").replaceChildren(h("p", { text: id ? `No place called "${id}" is watched. ` : "The list of places could not be read. " },
    h("a", { href: "place.html", text: "All places" })));
}).finally(() => { if (T) T.renderAll(document); });
