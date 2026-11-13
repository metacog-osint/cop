// COP v0: the common operational picture. Reads the snapshot that tools/snapshot.py builds (data/*.json), draws each
// program's findings on a muted base map, lists today's picks and shows every feed's health.
// Rules kept here: measurements are never called warnings, and forecasts are always labelled forecasts; claims draw hollow; grey means no current data; feed text
// is always inserted as text; no inline styles (CSP), colours come from config through MapLibre paint or the CSSOM.
import * as ml from "./vendor/maplibre-gl/maplibre-gl.mjs";
import { judgmentChips } from "./vendor/tic-doctrine.js";

const T = window.TicTime;
const $ = (id) => document.getElementById(id);
const dark = matchMedia("(prefers-color-scheme: dark)");
const phone = matchMedia("(max-width: 820px)");

const STATUS = {
  assessed: "Assessed: by the program's own method",
  reported: "Reported: by an independent outlet or agency",
  claimed: "Claimed: one party or one source (drawn hollow)",
};
const PRECISION = {
  point: "Exact place, to about 1 km", approximate: "Approximate place, within about 10 to 50 km",
  area: "An area or grid cell", admin1: "Province or state only", country: "Country-level, not an exact spot",
  none: "No place",
};
const STATE_WORD = { fresh: "Fresh", late: "Late", stale: "No current data" };

// ---------------------------------------------------------------- small DOM helpers (text only, never HTML)
function h(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") e.className = v;
    else if (k === "text") e.textContent = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) e.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  return e;
}
const timeEl = (iso, kind = "datetime") => (iso ? h("time", { datetime: iso, "data-tic": kind }, iso) : h("span", { text: "unknown" }));
const renderTimes = (root) => { if (T) T.renderAll(root || document); };

// ---------------------------------------------------------------- data
async function getJSON(url) {
  const r = await fetch(url, { cache: "no-cache" });
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
  return r.json();
}
let today, picksDoc, health, countries;
try {
  [today, picksDoc, health, countries] = await Promise.all(
    ["data/today.geojson", "data/picks.json", "data/health.json", "data/countries-today.geojson"].map(getJSON));
} catch (err) {
  $("bar-times").textContent = "Today's picture could not be loaded. Try again in a few minutes.";
  throw err;
}
// Outage triage is COP's own join, built after the snapshot. The picture stands without it, so a miss is not an error.
let triage = null;
try { triage = await getJSON("data/triage.json"); } catch { triage = null; }
const CFG = health.config;
const PROGS = {};
for (const p of CFG.programs) PROGS[p.id] = { ...p };
for (const hp of health.programs) Object.assign(PROGS[hp.id], { state: hp.state, health: hp });
const BY_ID = new Map(today.features.map((f) => [f.properties.id, f]));
const PICKS = picksDoc.picks;
const PICK_OF = new Map(PICKS.map((p) => [p.id, p]));

const state = { preset: "all", routine: false, selected: null, showAllAlso: false, showAllOff: false };
readHash();

// ---------------------------------------------------------------- colours
function colours(pid, stale) {
  const c = stale ? CFG.stale_color : PROGS[pid].color;
  return { fill: c.fill, line: dark.matches ? c.outline_dark : c.outline_light };
}
function colourExpr(kind) {
  const m = ["match", ["get", "cop_program"]];
  for (const p of CFG.programs) m.push(p.id, colours(p.id, false)[kind]);
  m.push("#888888");
  return ["case", ["==", ["get", "cop_stale"], true], colours(null, true)[kind], m];
}
function dotFor(pid, props = {}) {
  const d = h("span", { class: "dot" + (props.status_class === "claimed" ? " hollow" : "") + (props.cop_stale ? " stale" : ""), "aria-hidden": "true" });
  const c = colours(pid, false);
  d.style.background = c.fill;
  d.style.borderColor = c.fill;
  return d;
}

// ---------------------------------------------------------------- filters
const visibleProgram = (pid) => state.preset === "all" || PROGS[pid].preset === state.preset;
// Standing notices are routine by nature: show them whenever their own preset is chosen.
const routineShown = (pid) => state.routine || (state.preset !== "all" && PROGS[pid].routine_in_preset && visibleProgram(pid));
// Shown in every view: measured anomalies, and warnings (Vis's Amber or Red forecasts, Blackout's judged shutdowns
// and cable-corridor outages).
const STANDS_OUT = ["anomaly", "warning"];
const visibleItem = (p) => visibleProgram(p.cop_program) && (STANDS_OUT.includes(p.tier) || routineShown(p.cop_program));
function baseFilter() {
  const ids = CFG.programs.filter((p) => visibleProgram(p.id)).map((p) => p.id);
  const f = ["all", ["in", ["get", "cop_program"], ["literal", ids]]];
  if (!state.routine) {
    const keep = CFG.programs.filter((p) => routineShown(p.id)).map((p) => p.id);
    f.push(["any", ["in", ["get", "tier"], ["literal", STANDS_OUT]], ["in", ["get", "cop_program"], ["literal", keep]]]);
  }
  return f;
}
const POLY = ["in", ["geometry-type"], ["literal", ["Polygon", "MultiPolygon"]]];
const POINT = ["in", ["geometry-type"], ["literal", ["Point", "MultiPoint"]]];
const haloIds = CFG.programs.filter((p) => p.country_mark === "halo").map((p) => p.id);   // ring the label point
const fillIds = CFG.programs.filter((p) => (p.country_mark || "fill") === "fill").map((p) => p.id);  // shade the country
const dashIds = CFG.programs.filter((p) => p.area_style === "dashed").map((p) => p.id);   // legal or admin areas
const IS_HALO = ["all", ["in", ["get", "cop_program"], ["literal", haloIds]], ["==", ["get", "precision_class"], "country"]];
const IS_DASHED = ["in", ["get", "cop_program"], ["literal", dashIds]];

function countryFills() {
  const shapes = new Map(countries.features.map((f) => [f.properties.cc, f.geometry]));
  const out = new Map();
  for (const f of today.features) {
    const p = f.properties;
    if (p.precision_class !== "country" || !fillIds.includes(p.cop_program) || !visibleItem(p)) continue;
    for (const cc of p.countries || []) {
      const key = `${cc}|${p.cop_program}`;
      const prev = out.get(key);
      if (!shapes.has(cc) || (prev && prev.properties.tier === "anomaly")) continue;
      out.set(key, { type: "Feature", geometry: shapes.get(cc), properties: {
        id: p.id, cop_program: p.cop_program, cop_stale: p.cop_stale, tier: p.tier, status_class: p.status_class, cc } });
    }
  }
  return { type: "FeatureCollection", features: [...out.values()] };
}

// ---------------------------------------------------------------- map
// Base map: Natural Earth land from this site at world zoom (OpenFreeMap's world tiles run to about 1.5 MB each,
// which left the map blank for seconds), OpenFreeMap's vector map once zoomed in past DETAIL_ZOOM. If OpenFreeMap
// can't be reached, the Natural Earth land carries every zoom and a note says so.
const PANEL_W = 412;
const DETAIL_ZOOM = 3.5;
// The land only shows below DETAIL_ZOOM, so the worker never needs to tile it past zoom 5.
const worldSource = () => ({ type: "geojson", data: new URL("data/world.geojson", location.href).href, maxzoom: 5, tolerance: 1 });
const worldLayers = (b, maxzoom) => [
  { id: "cop-land", type: "fill", source: "cop_world", ...(maxzoom ? { maxzoom } : {}), paint: { "fill-color": b.land } },
  { id: "cop-borders", type: "line", source: "cop_world", ...(maxzoom ? { maxzoom } : {}),
    paint: { "line-color": dark.matches ? "#3A3F46" : "#B9BEC2", "line-width": 0.6 } },
];
function fallbackStyle() {
  const b = dark.matches ? CFG.basemaps.dark : CFG.basemaps.light;
  return { version: 8, sources: { cop_world: worldSource() },
    layers: [{ id: "bg", type: "background", paint: { "background-color": b.water } }, ...worldLayers(b)] };
}
async function buildStyle() {
  const b = dark.matches ? CFG.basemaps.dark : CFG.basemaps.light;
  let s;
  try { s = await getJSON(b.style); } catch { return { style: fallbackStyle(), fellBack: true }; }
  s.layers = s.layers.filter((l) => l.source !== "ne2_shaded");  // no shaded relief: the base stays muted
  delete s.sources.ne2_shaded;
  for (const l of s.layers) {
    if (l.type === "background") {
      l.paint = { ...l.paint, "background-color": ["step", ["zoom"], b.water, DETAIL_ZOOM, (l.paint && l.paint["background-color"]) || b.land] };
    } else if (l.source) {
      l.minzoom = Math.max(l.minzoom || 0, DETAIL_ZOOM);
    }
  }
  s.sources.cop_world = worldSource();
  s.layers.splice(s.layers.findIndex((l) => l.type === "background") + 1, 0, ...worldLayers(b, DETAIL_ZOOM));
  return { style: s, fellBack: false };
}
let fellBack = false;
async function applyStyle(target) {
  const { style, fellBack: fb } = await buildStyle();
  fellBack = fb;
  document.querySelectorAll(".fallback-note").forEach((e) => e.remove());
  if (fb) document.querySelector(".app").append(h("p", { class: "fallback-note", text: "Detailed base map unavailable: showing outline land." }));
  if (target) target.setStyle(style);
  return style;
}
const map = new ml.Map({
  container: "map", style: await applyStyle(null), center: [20, 25], zoom: 1.4, dragRotate: false, pitchWithRotate: false,
  attributionControl: { compact: true, customAttribution: `Findings: ${CFG.programs.map((p) => p.name).join(", ")} (credits under Sources) · Land: Natural Earth` },
});
map.touchZoomRotate.disableRotation();
// Time to the first fully drawn map, for checking load speed (readable in the console as copReadyMs).
map.once("idle", () => { window.copReadyMs = Math.round(performance.now()); });
map.addControl(new ml.NavigationControl({ showCompass: false }), "bottom-right");
map.on("style.load", () => addLayers());
dark.addEventListener("change", () => { applyStyle(map); renderLegend(); renderPanel(); });

// The map gets only what it draws from; cards read the full item from BY_ID. Keeps the worker's work small.
const MAP_KEYS = ["id", "cop_program", "cop_stale", "tier", "status_class", "precision_class"];
const MAP_ITEMS = { type: "FeatureCollection", features: today.features.filter((f) => f.geometry).map((f) => ({
  type: "Feature", geometry: f.geometry, properties: Object.fromEntries(MAP_KEYS.map((k) => [k, f.properties[k]])) })) };
function addLayers() {
  map.addSource("items", { type: "geojson", data: MAP_ITEMS, maxzoom: 10 });
  map.addSource("countryfills", { type: "geojson", data: countryFills(), maxzoom: 6 });
  const claimed = ["==", ["get", "status_class"], "claimed"];
  const anomaly = ["==", ["get", "tier"], "anomaly"];
  map.addLayer({ id: "cf-fill", type: "fill", source: "countryfills",
    paint: { "fill-color": colourExpr("fill"), "fill-opacity": ["case", anomaly, 0.2, 0.09] } });
  map.addLayer({ id: "cf-line", type: "line", source: "countryfills",
    paint: { "line-color": colourExpr("line"), "line-width": 1.3, "line-dasharray": [3, 2] } });
  map.addLayer({ id: "cells-fill", type: "fill", source: "items", filter: ["all", baseFilter(), POLY],
    paint: { "fill-color": colourExpr("fill"), "fill-opacity": ["case", claimed, 0.08, IS_DASHED, 0.12, anomaly, 0.55, 0.22] } });
  map.addLayer({ id: "cells-line", type: "line", source: "items", filter: ["all", baseFilter(), POLY, ["!", IS_DASHED]],
    paint: { "line-color": ["case", claimed, colourExpr("fill"), colourExpr("line")], "line-width": ["case", claimed, 2, 1.3] } });
  map.addLayer({ id: "areas-line", type: "line", source: "items", filter: ["all", baseFilter(), POLY, IS_DASHED],
    paint: { "line-color": colourExpr("line"), "line-width": 1.5, "line-dasharray": [4, 2] } });
  map.addLayer({ id: "halos", type: "circle", source: "items", filter: ["all", baseFilter(), POINT, IS_HALO],
    paint: { "circle-radius": ["case", anomaly, 12, 9], "circle-opacity": 0, "circle-stroke-color": colourExpr("fill"),
      "circle-stroke-width": ["case", anomaly, 3, 2], "circle-stroke-opacity": 0.95 } });
  map.addLayer({ id: "points", type: "circle", source: "items", filter: ["all", baseFilter(), POINT, ["!", IS_HALO]],
    paint: { "circle-radius": ["case", anomaly, 6.5, 4.5], "circle-color": colourExpr("fill"),
      "circle-opacity": ["case", claimed, 0, 1], "circle-stroke-color": ["case", claimed, colourExpr("fill"), colourExpr("line")],
      "circle-stroke-width": ["case", claimed, 2.2, 1.4] } });
  if (!handlersBound) {  // layer handlers survive a style change, so bind them once
    handlersBound = true;
    for (const id of ["cells-fill", "halos", "points", "cf-fill"]) {
      map.on("click", id, (e) => { if (e.features[0]) select(e.features[0].properties.id, { fly: false }); });
      map.on("mouseenter", id, () => { map.getCanvas().style.cursor = "pointer"; });
      map.on("mouseleave", id, () => { map.getCanvas().style.cursor = ""; });
    }
  }
  drawPins();
}
let handlersBound = false;
function refreshLayers() {
  if (!map.getSource("items")) return;
  map.getSource("countryfills").setData(countryFills());
  map.setFilter("cells-fill", ["all", baseFilter(), POLY]);
  map.setFilter("cells-line", ["all", baseFilter(), POLY, ["!", IS_DASHED]]);
  map.setFilter("areas-line", ["all", baseFilter(), POLY, IS_DASHED]);
  map.setFilter("halos", ["all", baseFilter(), POINT, IS_HALO]);
  map.setFilter("points", ["all", baseFilter(), POINT, ["!", IS_HALO]]);
  drawPins();
}

// numbered pins for today's picks (HTML markers, so they need no map fonts)
let pins = [];
function placeOf(f) {
  const g = f && f.geometry;
  if (!g) return null;
  if (g.type === "Point") return g.coordinates;
  const ring = g.type === "Polygon" ? g.coordinates[0] : g.type === "MultiPolygon" ? g.coordinates[0][0] : null;
  if (!ring) return null;
  let [x0, y0, x1, y1] = [180, 90, -180, -90];
  for (const [x, y] of ring) { x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y); }
  return [(x0 + x1) / 2, (y0 + y1) / 2];
}
function drawPins() {
  pins.forEach((m) => m.remove());
  pins = [];
  for (const p of PICKS) {
    const f = BY_ID.get(p.id);
    const at = placeOf(f);
    if (!at || !visibleProgram(p.program)) continue;
    const el = h("button", { type: "button", class: "pin", "data-id": p.id, "aria-label": `Pick ${p.n}: ${f.properties.title}`,
      onclick: (e) => { e.stopPropagation(); select(p.id); } }, String(p.n));
    if (state.selected === p.id) el.setAttribute("aria-current", "true");
    pins.push(new ml.Marker({ element: el }).setLngLat(at).addTo(map));
  }
}
function padding() {
  return phone.matches ? { top: 40, right: 30, left: 30, bottom: Math.round(window.innerHeight * 0.5) }
    : { top: 40, right: 40, bottom: 40, left: PANEL_W + 24 };
}
function fitPicks() {
  const pts = PICKS.filter((p) => visibleProgram(p.program)).map((p) => placeOf(BY_ID.get(p.id))).filter(Boolean);
  if (!pts.length) return;
  const b = new ml.LngLatBounds(pts[0], pts[0]);
  pts.forEach((pt) => b.extend(pt));
  map.fitBounds(b, { padding: padding(), maxZoom: 4, duration: 0 });
}

// ---------------------------------------------------------------- selection and the detail card
function select(id, { fly = true } = {}) {
  const f = BY_ID.get(id);
  if (!f) return;
  state.selected = id;
  renderCard(f);
  document.querySelectorAll("[data-id]").forEach((e) => e.toggleAttribute("aria-current", e.getAttribute("data-id") === id));
  document.querySelectorAll("[data-id][aria-current]").forEach((e) => e.setAttribute("aria-current", "true"));
  const at = placeOf(f);
  if (fly && at && map.loaded()) map.easeTo({ center: at, zoom: Math.max(map.getZoom(), 3.2), padding: padding(), duration: 600 });
  if (phone.matches && $("panel").classList.contains("sheet-peek")) setSheet("sheet-half");
  $("card").scrollIntoView({ block: "nearest" });
  writeHash();
}
function closeCard() {
  state.selected = null;
  $("card").hidden = true;
  document.querySelectorAll("[data-id][aria-current]").forEach((e) => e.removeAttribute("aria-current"));
  writeHash();
}
function renderCard(f) {
  const p = f.properties, prog = PROGS[p.cop_program], pick = PICK_OF.get(p.id);
  const card = $("card");
  card.replaceChildren();
  card.hidden = false;
  card.append(
    h("button", { type: "button", class: "card-close", "aria-label": "Close", onclick: closeCard }, "×"),
    h("div", { class: "card-prog" }, dotFor(p.cop_program, p), `${prog.name} · ${prog.family}`),
    h("h3", { text: p.title }),
    p.summary ? h("p", { text: p.summary }) : null,
  );
  const badges = h("div", { class: "badges" });
  if (p.kind === "forecast") {
    badges.append(h("span", { class: "badge measure", text: "Forecast: a probability, not a measurement" }));
  } else if (p.kind === "assessment") {
    badges.append(h("span", { class: "badge measure", text: "Judgment: a probability, not a measurement" }));
  } else if (p.tier === "anomaly") badges.append(h("span", { class: "badge measure", text: "Measured, not a warning" }));
  else if (p.tier === "routine") badges.append(h("span", { class: "badge", text: "Routine: inside its usual range" }));
  if (p.tier === "warning") badges.append(h("span", { class: "badge", text: `Warning: ${prog.name}'s judgment` }));
  if (p.status_class) badges.append(h("span", { class: "badge", text: STATUS[p.status_class] || p.status_class }));
  if (p.cop_stale) badges.append(h("span", { class: "badge stale", text: `No current data from ${prog.name}` }));
  for (const c of judgmentChips(p)) badges.append(h("span", { class: "badge", text: c.text }));
  card.append(badges);
  const facts = h("dl", { class: "facts" });
  const fact = (k, v) => facts.append(h("dt", { text: k }), h("dd", {}, v));
  if (pick) fact("Why it's here", `Pick ${pick.n}: ${pick.reason}.`);
  else if (p.cop_strength_text) fact("Size", p.cop_strength_text[0].toUpperCase() + p.cop_strength_text.slice(1));
  fact("Data as of", timeEl(p.data_as_of));
  if (p.published_at) fact("Published", timeEl(p.published_at));
  fact("Place", PRECISION[p.precision_class] || "Not stated");
  const pubs = [...new Set((p.sources || []).map((s) => s.publisher).filter(Boolean))];
  if (pubs.length) fact("Sources", pubs.slice(0, 3).join("; ") + (pubs.length > 3 ? ` and ${pubs.length - 3} more` : ""));
  card.append(facts);
  const href = p.link || (p.visual && p.visual.url) || prog.site;
  card.append(h("a", { class: "card-open", href, rel: "noopener" }, `Open in ${prog.name} →`));
  renderTimes(card);
}

// ---------------------------------------------------------------- panel
function rowFor(f, cls = "row") {
  const p = f.properties, prog = PROGS[p.cop_program];
  const pick = PICK_OF.get(p.id);
  return h("li", {}, h("button", { type: "button", class: cls, "data-id": p.id, onclick: () => select(p.id) },
    pick && cls === "pick" ? h("span", { class: "num", "aria-hidden": "true" }, String(pick.n)) : dotFor(p.cop_program, p),
    h("span", {},
      h("span", { class: cls === "pick" ? "pick-title" : "row-title", text: p.title }),
      h("span", { class: cls === "pick" ? "pick-meta" : "row-meta" },
        cls === "pick" ? dotFor(p.cop_program, p) : null, prog.name, " · ", timeEl(p.data_as_of, "date"),
        p.status_class === "claimed" ? " · claimed" : null, p.cop_stale ? " · no current data" : null))));
}
function renderPanel() {
  const presets = $("presets");
  presets.replaceChildren(...Object.entries(CFG.presets).map(([k, label]) =>
    h("button", { type: "button", class: "chip", "aria-pressed": String(state.preset === k),
      onclick: () => { state.preset = k; state.showAllAlso = state.showAllOff = false; refreshLayers(); renderPanel(); writeHash(); } }, label)));
  $("show-routine").checked = state.routine;

  const picks = PICKS.filter((p) => visibleProgram(p.program)).map((p) => BY_ID.get(p.id)).filter(Boolean);
  const standing = CFG.programs.filter((p) => routineShown(p.id) && p.routine_in_preset)
    .map((p) => `${p.name}'s ${p.routine_label || "standing notices"}`);
  const none = standing.length
    ? `Nothing outside its usual range in this view today. The map shows ${standing.join(" and ")}.`
    : "No findings with a place for this view today. See Off the map below.";
  $("picks").replaceChildren(...(picks.length ? picks.map((f) => rowFor(f, "pick")) : [h("li", { class: "empty", text: none })]));

  const placed = today.features.filter((f) => f.geometry && f.properties.tier === "anomaly" && !PICK_OF.has(f.properties.id)
    && visibleProgram(f.properties.cop_program));
  placed.sort((a, b) => CFG.programs.findIndex((x) => x.id === a.properties.cop_program) - CFG.programs.findIndex((x) => x.id === b.properties.cop_program)
    || (b.properties.cop_strength ?? -1e9) - (a.properties.cop_strength ?? -1e9));
  const alsoShown = state.showAllAlso ? placed : placed.slice(0, 10);
  $("also").replaceChildren(...(placed.length ? alsoShown.map((f) => rowFor(f)) : [h("li", { class: "empty", text: "Nothing else outside its usual range." })]),
    ...(placed.length > alsoShown.length ? [h("li", {}, h("button", { type: "button", class: "chip", onclick: () => { state.showAllAlso = true; renderPanel(); } },
      `Show ${placed.length - alsoShown.length} more`))] : []));

  const off = today.features.filter((f) => !f.geometry && visibleProgram(f.properties.cop_program));
  const offAnom = off.filter((f) => f.properties.tier === "anomaly");
  const offShown = state.showAllOff ? offAnom : offAnom.slice(0, 8);
  const routineCounts = CFG.programs.map((p) => [p, off.filter((f) => f.properties.cop_program === p.id && f.properties.tier !== "anomaly").length]).filter(([, n]) => n);
  $("offmap").replaceChildren(...offShown.map((f) => rowFor(f)),
    ...(offAnom.length > offShown.length ? [h("li", {}, h("button", { type: "button", class: "chip", onclick: () => { state.showAllOff = true; renderPanel(); } },
      `Show ${offAnom.length - offShown.length} more`))] : []),
    ...routineCounts.map(([p, n]) => h("li", { class: "more" }, `${p.name}: ${n} routine finding${n === 1 ? "" : "s"} with no place. `,
      h("a", { href: p.site, rel: "noopener" }, `Open ${p.name}`))),
    ...(!off.length ? [h("li", { class: "empty", text: "None for this view." })] : []));

  renderTriage();
  renderHealth();
  renderBar();
  renderCredits();
  if (state.selected && BY_ID.has(state.selected)) {
    document.querySelectorAll("[data-id]").forEach((e) => e.toggleAttribute("aria-current", e.getAttribute("data-id") === state.selected));
    document.querySelectorAll("[data-id][aria-current]").forEach((e) => e.setAttribute("aria-current", "true"));
  }
  renderTimes(document);
}
// Outage triage: for each Blackout outage, what Elements, Cordon and Watchfloor show under it. A reading sorts
// reports; the section says so, and shows "nothing found" and "no current data" as the different answers they are.
const TRIAGE_LISTS = { elements: "significant", cordon: "recent", watchfloor: "raised" };
function triageItem(row) {
  return BY_ID.has(row.id)
    ? h("button", { type: "button", class: "tri-item", "data-id": row.id, onclick: () => select(row.id) }, row.title)
    : h("span", { class: "tri-item", text: row.title });
}
function triageRecord(r) {
  const down = r.reading === "incomplete";
  const layers = Object.entries(r.layers).map(([key, L]) => {
    const dead = L.state === "stale" || L.state === "missing";
    const cited = dead ? [] : (L[TRIAGE_LISTS[key]] || []).slice(0, 4);
    return h("div", { class: "tri-layer" + (dead ? " dead" : "") },
      h("dt", {}, dotFor(key, { cop_stale: dead }), L.name),
      h("dd", {}, L.showed, cited.length ? h("span", { class: "tri-items" }, ...cited.map(triageItem)) : null));
  });
  return h("li", { class: "tri" },
    BY_ID.has(r.outage.id)
      ? h("button", { type: "button", class: "tri-outage", "data-id": r.outage.id, onclick: () => select(r.outage.id) }, r.outage.title)
      : h("span", { class: "tri-outage", text: r.outage.title }),
    h("div", { class: "row-meta" }, "Outage at ", timeEl(r.outage.data_as_of)),
    h("div", { class: "badges" },
      h("span", { class: "badge" + (down ? " stale" : " measure"), text: `Reading: ${r.reading}` }),
      h("span", { class: "badge", text: "Not a warning or a forecast" })),
    h("p", { class: "tri-text", text: r.reading_text }),
    h("dl", { class: "tri-layers" }, ...layers),
    r.base_rate ? h("p", { class: "tri-rate" }, h("strong", { text: "Base rate. " }), r.base_rate.text) : null,
    r.ooni_now ? h("p", { class: "tri-rate" }, h("strong", { text: "OONI now. " }), r.ooni_now.title) : null,
    ...(r.caveats || []).map((c) => h("p", { class: "tri-caveat", text: c })),
    triageQuestion(r.question_draft));
}
// The drafted Verdict question: how this reading could be scored later. A draft, never shown as a forecast.
function triageQuestion(q) {
  if (!q) return null;
  const rise = q.rise_form;
  return h("details", { class: "tri-q" },
    h("summary", { text: "Question to score it by (a draft, not registered)" }),
    h("p", { text: q.note }),
    h("p", {}, h("strong", { text: "Question. " }), q.question),
    h("p", {}, h("strong", { text: "Settled by. " }), q.rule_draft),
    h("p", {}, h("strong", { text: "Window ends. " }), timeEl(q.window_ends)),
    q.caution ? h("p", { class: "tri-caveat", text: q.caution }) : null,
    rise ? h("p", {}, h("strong", { text: "Rise form. " }), rise.question) : null,
    rise ? h("p", {}, h("strong", { text: "Settled by. " }), rise.rule_draft) : null,
    ...(rise ? rise.notes.map((n) => h("p", { class: "tri-caveat", text: n })) : []));
}
function renderTriage() {
  const sec = $("triage-sec");
  sec.hidden = !visibleProgram("blackout");
  if (sec.hidden) return;
  const list = $("triage");
  const foot = $("triage-foot");
  foot.replaceChildren();
  if (!triage) {
    $("triage-note").textContent = "Outage triage is not available for this picture.";
    list.replaceChildren();
    return;
  }
  $("triage-note").textContent = `${triage.what} ${triage.not_a_warning}`;
  const state = (triage.layer_states || {}).blackout;
  const none = state === "stale" || state === "missing"
    ? "No current data from Blackout, so there is nothing to triage. That is not the same as no outages."
    : "No outages in Blackout's current data.";
  list.replaceChildren(...(triage.records.length ? triage.records.map(triageRecord) : [h("li", { class: "empty", text: none })]));
  const b = triage.base_rates;
  if (b) {
    const pct = (x) => `${(x * 100).toFixed(1)}%`;
    foot.append(h("p", { class: "note" },
      `Base rates: over ${b.country_days.toLocaleString("en-US")} country-days in COP's archive, an outage would have read natural on ${pct(b.would_read.natural)}, ` +
      `deliberate on ${pct(b.would_read.deliberate)} and open on ${pct(b.would_read.open)}, whatever its cause. `,
      ...(b.cautions || []).map((c) => `${c} `),
      triage.windows && triage.windows.provisional ? "The time windows are provisional." : null));
  } else if (triage.records.length) {
    foot.append(h("p", { class: "note", text: "No base rates were available for this picture, so the readings stand without them." }));
  }
}
function renderHealth() {
  $("health").replaceChildren(...health.programs.map((hp) => {
    const prog = PROGS[hp.id];
    const lint = hp.lint;
    const bits = [];
    bits.push(hp.data_as_of ? ["Data through ", timeEl(hp.data_as_of)] : ["No data"]);
    bits.push(` · ${hp.items} findings, ${hp.anomalies} outside their usual range`);
    if (hp.fetch.ok) bits.push(` · fetched in ${hp.fetch.seconds} s`);
    else bits.push(` · fetch failed${hp.from_previous ? ": showing the last good copy" : ""}`);
    if (lint) bits.push(lint.errors ? ` · ${lint.errors} contract problems` : " · passes the contract checks");
    return h("div", { class: "hrow" },
      h("span", { class: "hname" }, dotFor(hp.id, { cop_stale: hp.state === "stale" }), prog.name),
      h("span", { class: `pill ${hp.state}`, text: STATE_WORD[hp.state] || hp.state }),
      h("span", { class: "hmeta" }, ...bits.flat(), " · ", h("a", { href: hp.site, rel: "noopener" }, "Open"), " · ",
        h("a", { href: hp.feed, rel: "noopener" }, "Feed")));
  }));
}
function renderBar() {
  const live = health.programs.filter((p) => p.state !== "stale" && p.data_as_of);
  const oldest = live.sort((a, b) => a.data_as_of.localeCompare(b.data_as_of))[0];
  const bar = $("bar-times");
  bar.replaceChildren("Assessed ", h("b", {}, timeEl(health.generated_at)),
    ...(oldest ? [" · Data through ", timeEl(oldest.data_as_of), ` (oldest: ${PROGS[oldest.id].name})`] : [" · No current data"]));
}
function renderCredits() {
  const box = $("credits");
  box.querySelectorAll(".prog-credit").forEach((e) => e.remove());
  for (const hp of health.programs) if (hp.attribution) box.append(h("p", { class: "prog-credit", text: hp.attribution }));
  box.querySelectorAll(".updated").forEach((e) => e.remove());
  box.append(h("p", { class: "updated" }, "Updated ", timeEl(health.generated_at), ". Not for navigation or safety decisions."));
}
function renderLegend() {
  const lg = $("legend");
  const wasOpen = lg.querySelector("details") ? lg.querySelector("details").open : !phone.matches;  // folded on phones
  const d = h("details", { class: "legend-d" }, h("summary", { text: "Map key" }),
    h("div", { class: "lg-items" },
      ...CFG.programs.map((p) => h("span", { class: "lg" }, dotFor(p.id), `${p.name} (${p.family})`)),
      h("span", { class: "lg" }, dotFor(CFG.programs[0].id, { status_class: "claimed" }), "Hollow: a claim"),
      h("span", { class: "lg" }, h("span", { class: "swatch-dash", "aria-hidden": "true" }), "Dashed: country-level or a legal area"),
      h("span", { class: "lg" }, h("span", { class: "swatch-ring", "aria-hidden": "true" }), "Ring: country-level, at its label point"),
      h("span", { class: "lg" }, dotFor(CFG.programs[0].id, { cop_stale: true }), "Grey: no current data")));
  d.open = wasOpen;
  lg.replaceChildren(d);
}

// ---------------------------------------------------------------- URL state: place, preset, routine, selection
function readHash() {
  const q = new URLSearchParams(location.hash.slice(1));
  if (q.get("p") && CFG.presets[q.get("p")]) state.preset = q.get("p");
  state.routine = q.get("r") === "1";
  if (q.get("i")) state.selected = q.get("i");
  const m = /^(-?[\d.]+)\/(-?[\d.]+)\/(-?[\d.]+)$/.exec(q.get("map") || "");
  state.view = m ? { zoom: +m[1], center: [+m[3], +m[2]] } : null;
}
let hashTimer;
function writeHash() {
  clearTimeout(hashTimer);
  hashTimer = setTimeout(() => {
    const c = map.getCenter();
    const q = new URLSearchParams();
    q.set("map", `${map.getZoom().toFixed(2)}/${c.lat.toFixed(2)}/${c.lng.toFixed(2)}`);
    if (state.preset !== "all") q.set("p", state.preset);
    if (state.routine) q.set("r", "1");
    if (state.selected) q.set("i", state.selected);
    history.replaceState(null, "", "#" + q.toString());
  }, 250);
}
map.on("moveend", writeHash);

// ---------------------------------------------------------------- phone sheet, keys, time
const SHEETS = ["sheet-peek", "sheet-half", "sheet-full"];
function setSheet(s) {
  const panel = $("panel");
  SHEETS.forEach((x) => panel.classList.toggle(x, x === s));
  $("sheet-handle").setAttribute("aria-label", s === "sheet-full" ? "Show less of the panel" : "Show more of the panel");
}
$("sheet-handle").addEventListener("click", () => {
  const cur = SHEETS.find((s) => $("panel").classList.contains(s)) || "sheet-half";
  setSheet(SHEETS[(SHEETS.indexOf(cur) + 1) % SHEETS.length]);
});
$("show-routine").addEventListener("change", (e) => { state.routine = e.target.checked; refreshLayers(); renderPanel(); writeHash(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && state.selected) closeCard(); });
if (T) { T.mountSettings($("time-settings")); T.onChange(() => renderTimes(document)); }

// ---------------------------------------------------------------- start
renderLegend();
renderPanel();
if (state.view) map.jumpTo(state.view);
else map.once("load", fitPicks);
if (state.selected && BY_ID.has(state.selected)) map.once("load", () => select(state.selected, { fly: !state.view }));
