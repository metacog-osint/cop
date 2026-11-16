// COP v0: the common operational picture. Reads the snapshot that tools/snapshot.py builds (data/*.json), draws each
// program's findings on a muted base map, lists today's picks and shows every feed's health.
// Rules kept here: measurements are never called warnings, and forecasts are always labelled forecasts; claims draw hollow; grey means no current data; feed text
// is always inserted as text; no inline styles (CSP), colours come from config through MapLibre paint or the CSSOM.
// Since 15 Nov 2026: warnings are listed first, as their program's own judgment, and are never picks; an item past
// its own valid_until no longer stands out; one click handler opens the top mark under the cursor and lists the
// rest; and the sentences that count or describe findings are written from the snapshot, not fixed in the page.
import * as ml from "./vendor/maplibre-gl/maplibre-gl.mjs";
import { judgmentChips, asOfDateLabel } from "./vendor/tic-doctrine.js";

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
// An item's data time. A source that gives no time (data_as_of_precision "day" or "month", kit 0.5.6) is shown as
// its date or month, read in UTC and without data-tic, so the viewer's zone never moves it to the evening before.
const asOfEl = (p, kind = "datetime") => {
  const label = asOfDateLabel(p);
  return label ? h("time", { datetime: p.data_as_of, title: "The source gives no time of day" }, label) : timeEl(p.data_as_of, kind);
};
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
// The three-signal join (published since 15 Nov 2026), built after triage. The picture stands without it too.
let threeSignal = null;
try { threeSignal = await getJSON("data/three-signal.json"); } catch { threeSignal = null; }
const CFG = health.config;
const PROGS = {};
for (const p of CFG.programs) PROGS[p.id] = { ...p };
for (const hp of health.programs) Object.assign(PROGS[hp.id], { state: hp.state, health: hp });
const BY_ID = new Map(today.features.map((f) => [f.properties.id, f]));
const PICKS = picksDoc.picks;
const PICK_OF = new Map(PICKS.map((p) => [p.id, p]));
// Watchfloor and Context both read GDELT, so COP counts them as one voice per zone (15 Nov 2026): a pick of that voice
// carries every reading of it in `readings` (its own first), each still its own program's item. READING_OF maps the
// other readings to their pick, so the panel lists them on the pick's row, not again under Also watching.
const READING_OF = new Map(PICKS.flatMap((p) => (p.readings || []).filter((r) => r.id !== p.id).map((r) => [r.id, p])));
const TIER_WORD = { anomaly: "unusual", routine: "routine", warning: "warning" };
const otherReadings = (pick) => (pick && pick.readings ? pick.readings.filter((r) => r.id !== pick.id && BY_ID.has(r.id)) : []);
const readingText = (r) => `${PROGS[r.program] ? PROGS[r.program].name : r.program}, ${TIER_WORD[r.tier] || r.tier}`
  + (r.stale ? ", no current data" : "");
// An item past its own valid_until has expired. Read once, as the page loads: the snapshot is up to two hours old by
// then, and an item can run out between builds (a forecast for a day that has ended, a warning that was cancelled).
const LOADED = Date.now();
for (const f of today.features) {
  const until = typeof f.properties.valid_until === "string" ? Date.parse(f.properties.valid_until) : NaN;
  f.properties.cop_expired = until < LOADED;  // no valid_until, or one that cannot be read, is not expired
}

const state = { preset: "all", routine: false, selected: null, showAllAlso: false, showAllOff: false, showAllWarn: false,
  here: null, hereAll: false };
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
// and cable-corridor outages), until they expire. An expired item shows only where routine items show, drawn as one.
const STANDS_OUT = ["anomaly", "warning"];
const standsOut = (p) => STANDS_OUT.includes(p.tier) && !p.cop_expired;
const NOT_EXPIRED = ["!=", ["get", "cop_expired"], true];  // the same test for the map, which filters its own copy
const OUT = ["all", ["in", ["get", "tier"], ["literal", STANDS_OUT]], NOT_EXPIRED];
// A program's items tagged for another preset (config also_in, cop_also ",spectrum,") show there too, routine or not.
const alsoHere = (p) => state.preset !== "all" && typeof p.cop_also === "string" && p.cop_also.includes(`,${state.preset},`);
const visibleItem = (p) => (visibleProgram(p.cop_program) && (standsOut(p) || routineShown(p.cop_program)))
  || alsoHere(p);
function baseFilter() {
  const ids = CFG.programs.filter((p) => visibleProgram(p.id)).map((p) => p.id);
  const f = ["all", ["in", ["get", "cop_program"], ["literal", ids]]];
  if (!state.routine) {
    const keep = CFG.programs.filter((p) => routineShown(p.id)).map((p) => p.id);
    f.push(["any", OUT, ["in", ["get", "cop_program"], ["literal", keep]]]);
  }
  if (state.preset === "all") return f;
  return ["any", f, ["in", `,${state.preset},`, ["coalesce", ["get", "cop_also"], ""]]];
}
const POLY = ["in", ["geometry-type"], ["literal", ["Polygon", "MultiPolygon"]]];
const POINT = ["in", ["geometry-type"], ["literal", ["Point", "MultiPoint"]]];
// Lines: tracklines in NGA warnings (cable routes, ice limits, cleared tracks), drawn solid whatever area_style says.
const LINE = ["in", ["geometry-type"], ["literal", ["LineString", "MultiLineString"]]];
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
      if (!shapes.has(cc) || (prev && standsOut(prev.properties))) continue;  // a standing-out item keeps the country
      out.set(key, { type: "Feature", geometry: shapes.get(cc), properties: {
        id: p.id, cop_program: p.cop_program, cop_stale: p.cop_stale, tier: p.tier, status_class: p.status_class,
        cop_expired: p.cop_expired, cc } });
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
// A GeometryCollection (an NGA warning with both an area and a trackline) becomes one feature per part, each with
// the item's id, so every part is filtered, drawn and clicked as its own geometry type.
const MAP_KEYS = ["id", "cop_program", "cop_stale", "tier", "status_class", "precision_class", "cop_also", "cop_expired"];
const parts = (g) => (g && g.type === "GeometryCollection" ? (g.geometries || []).flatMap(parts) : g ? [g] : []);
const MAP_ITEMS = { type: "FeatureCollection", features: today.features.flatMap((f) => parts(f.geometry).map((g) => ({
  type: "Feature", geometry: g, properties: Object.fromEntries(MAP_KEYS.map((k) => [k, f.properties[k]])) }))) };
function addLayers() {
  map.addSource("items", { type: "geojson", data: MAP_ITEMS, maxzoom: 10 });
  map.addSource("countryfills", { type: "geojson", data: countryFills(), maxzoom: 6 });
  const claimed = ["==", ["get", "status_class"], "claimed"];
  // Every item that stands out (an anomaly or a warning, until it expires) draws at the larger size; warnings drew at
  // routine size until 15 Nov 2026. An expired one draws as routine.
  const strong = OUT;
  map.addLayer({ id: "cf-fill", type: "fill", source: "countryfills",
    paint: { "fill-color": colourExpr("fill"), "fill-opacity": ["case", strong, 0.2, 0.09] } });
  map.addLayer({ id: "cf-line", type: "line", source: "countryfills",
    paint: { "line-color": colourExpr("line"), "line-width": 1.3, "line-dasharray": [3, 2] } });
  map.addLayer({ id: "cells-fill", type: "fill", source: "items", filter: ["all", baseFilter(), POLY],
    paint: { "fill-color": colourExpr("fill"), "fill-opacity": ["case", claimed, 0.08, IS_DASHED, 0.12, strong, 0.55, 0.22] } });
  map.addLayer({ id: "cells-line", type: "line", source: "items", filter: ["all", baseFilter(), POLY, ["!", IS_DASHED]],
    paint: { "line-color": ["case", claimed, colourExpr("fill"), colourExpr("line")], "line-width": ["case", claimed, 2, 1.3] } });
  map.addLayer({ id: "areas-line", type: "line", source: "items", filter: ["all", baseFilter(), POLY, IS_DASHED],
    paint: { "line-color": colourExpr("line"), "line-width": 1.5, "line-dasharray": [4, 2] } });
  // Lines: a casing in the outline colour (contrast with the base map) under the program's fill colour; a claim
  // keeps only the fill colour, as cells-line does. lines-hit is invisible and wide, so a thin line is easy to click.
  map.addLayer({ id: "lines-casing", type: "line", source: "items", filter: ["all", baseFilter(), LINE],
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": ["case", claimed, colourExpr("fill"), colourExpr("line")], "line-width": ["case", strong, 5, 3.6] } });
  map.addLayer({ id: "lines", type: "line", source: "items", filter: ["all", baseFilter(), LINE],
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": colourExpr("fill"), "line-width": ["case", strong, 2.8, 1.8] } });
  map.addLayer({ id: "lines-hit", type: "line", source: "items", filter: ["all", baseFilter(), LINE],
    paint: { "line-color": "#000000", "line-opacity": 0, "line-width": 14 } });
  map.addLayer({ id: "halos", type: "circle", source: "items", filter: ["all", baseFilter(), POINT, IS_HALO],
    paint: { "circle-radius": ["case", strong, 12, 9], "circle-opacity": 0, "circle-stroke-color": colourExpr("fill"),
      "circle-stroke-width": ["case", strong, 3, 2], "circle-stroke-opacity": 0.95 } });
  map.addLayer({ id: "points", type: "circle", source: "items", filter: ["all", baseFilter(), POINT, ["!", IS_HALO]],
    paint: { "circle-radius": ["case", strong, 6.5, 4.5], "circle-color": colourExpr("fill"),
      "circle-opacity": ["case", claimed, 0, 1], "circle-stroke-color": ["case", claimed, colourExpr("fill"), colourExpr("line")],
      "circle-stroke-width": ["case", claimed, 2.2, 1.4] } });
  drawPins();
}
// One click handler for every layer that carries items (15 Nov 2026). Each layer used to have its own, every one of
// them fired on a click, and the country fill's ran last: a click on a mark inside a shaded country opened the
// country's card. Now the top mark under the cursor opens, and its card lists whatever else is drawn at that spot.
const HIT_LAYERS = ["cf-fill", "cells-fill", "lines-hit", "halos", "points"];  // as drawn, bottom first
function idsAt(point) {
  if (!HIT_LAYERS.every((id) => map.getLayer(id))) return [];  // between two styles, on a theme change
  const ids = [];
  for (const f of map.queryRenderedFeatures(point, { layers: HIT_LAYERS })) {  // MapLibre returns the top mark first
    const id = f.properties.id;
    if (BY_ID.has(id) && !ids.includes(id)) ids.push(id);
  }
  return ids;
}
map.on("click", (e) => { const ids = idsAt(e.point); if (ids.length) select(ids[0], { fly: false, here: ids }); });
map.on("mousemove", (e) => { map.getCanvas().style.cursor = idsAt(e.point).length ? "pointer" : ""; });
function refreshLayers() {
  if (!map.getSource("items")) return;
  map.getSource("countryfills").setData(countryFills());
  map.setFilter("cells-fill", ["all", baseFilter(), POLY]);
  map.setFilter("cells-line", ["all", baseFilter(), POLY, ["!", IS_DASHED]]);
  map.setFilter("areas-line", ["all", baseFilter(), POLY, IS_DASHED]);
  for (const id of ["lines-casing", "lines", "lines-hit"]) map.setFilter(id, ["all", baseFilter(), LINE]);
  map.setFilter("halos", ["all", baseFilter(), POINT, IS_HALO]);
  map.setFilter("points", ["all", baseFilter(), POINT, ["!", IS_HALO]]);
  drawPins();
}

// numbered pins for today's picks (HTML markers, so they need no map fonts)
let pins = [];
// A point is its own place; anything else sits at the middle of the box around its positions (a polygon's outer
// ring, the first polygon of a multipolygon, every position of a line or multipoint, the first part of a
// collection). tools/card.py place_of keeps the same rule. Shapes across 180 degrees carry longitudes past +-180
// (Cordon keeps them continuous), so the middle can be past 180 too; MapLibre draws it on the next world copy.
function positionsOf(g) {
  if (!g) return [];
  switch (g.type) {
    case "Point": return [g.coordinates];
    case "MultiPoint": case "LineString": return g.coordinates;
    case "Polygon": return g.coordinates[0] || [];
    case "MultiPolygon": return (g.coordinates[0] || [])[0] || [];
    case "MultiLineString": return g.coordinates.flat();
    case "GeometryCollection": return positionsOf((g.geometries || [])[0]);
    default: return [];
  }
}
function placeOf(f) {
  const g = f && f.geometry;
  if (g && g.type === "Point") return g.coordinates;
  const pts = positionsOf(g);
  if (!pts.length) return null;
  let [x0, y0, x1, y1] = [Infinity, Infinity, -Infinity, -Infinity];
  for (const [x, y] of pts) { x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y); }
  return [(x0 + x1) / 2, (y0 + y1) / 2];
}
function drawPins() {
  pins.forEach((m) => m.remove());
  pins = [];
  for (const p of PICKS) {
    const f = BY_ID.get(p.id);
    const at = placeOf(f);
    if (!at || !visibleProgram(p.program) || !standsOut(f.properties)) continue;  // a pick that has expired loses its pin
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
  const pts = PICKS.filter((p) => visibleProgram(p.program) && BY_ID.has(p.id) && standsOut(BY_ID.get(p.id).properties))
    .map((p) => placeOf(BY_ID.get(p.id))).filter(Boolean);
  if (!pts.length) return;
  const b = new ml.LngLatBounds(pts[0], pts[0]);
  pts.forEach((pt) => b.extend(pt));
  map.fitBounds(b, { padding: padding(), maxZoom: 4, duration: 0 });
}

// ---------------------------------------------------------------- selection and the detail card
// `here`: every item under a map click, top first. The card lists the others, and keeps the list while the reader
// steps through them; a selection made anywhere else (a row, a pin, a link) drops it.
function select(id, { fly = true, here = null } = {}) {
  const f = BY_ID.get(id);
  if (!f) return;
  state.selected = id;
  if (here) { state.here = here.length > 1 ? here : null; state.hereAll = false; }
  else if (!(state.here || []).includes(id)) state.here = null;
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
  state.here = null;
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
    ...(p.summary ? [h("p", { text: p.summary })] : []),  // append(null) would print "null"
  );
  const badges = h("div", { class: "badges" });
  if (p.kind === "forecast") {
    badges.append(h("span", { class: "badge measure", text: "Forecast: a probability, not a measurement" }));
  } else if (p.kind === "assessment") {
    badges.append(h("span", { class: "badge measure", text: "Judgment: a probability, not a measurement" }));
  } else if (p.tier === "anomaly") {
    // The badge follows the item's own status_class (15 Nov 2026). Only an item its program reached by its own method
    // (assessed) is called measured. That badge used to sit on every anomaly, official warnings and notices included
    // (an NGA navigational warning, an FAA restriction, a travel advisory), which are neither measured nor "not a
    // warning". The feeds carry no field that says "official notice", so the wording for the rest is the contract's
    // own for an observation: measured or reported, with no judgment about what it means.
    badges.append(h("span", { class: "badge measure",
      text: p.status_class === "assessed" ? "Measured, not a warning" : "Measured or reported, not a judgment" }));
  } else if (p.tier === "routine") badges.append(h("span", { class: "badge", text: "Routine: inside its usual range" }));
  if (p.tier === "warning") badges.append(h("span", { class: "badge", text: `Warning: ${prog.name}'s judgment` }));
  if (p.cop_expired) badges.append(h("span", { class: "badge stale", text: "Expired: past its valid-until time" }));
  if (p.status_class) badges.append(h("span", { class: "badge", text: STATUS[p.status_class] || p.status_class }));
  if (p.cop_stale) badges.append(h("span", { class: "badge stale", text: p.cop_source_stale
    ? `No current data from this ${prog.name} source: its last run could not read it well` : `No current data from ${prog.name}` }));
  for (const c of judgmentChips(p)) badges.append(h("span", { class: "badge", text: c.text }));
  card.append(badges);
  const facts = h("dl", { class: "facts" });
  const fact = (k, v) => facts.append(h("dt", { text: k }), h("dd", {}, v));
  if (pick) fact("Why it's here", `Pick ${pick.n}: ${pick.reason}.`);
  else if (p.cop_strength_text) fact("Size", p.cop_strength_text[0].toUpperCase() + p.cop_strength_text.slice(1));
  const voicePick = pick || READING_OF.get(p.id);
  const sameVoice = voicePick ? [voicePick.id, ...otherReadings(voicePick).map((r) => r.id)].filter((id) => id !== p.id) : [];
  if (sameVoice.length) {
    const progs = new Set([p.cop_program, ...sameVoice.map((id) => BY_ID.get(id).properties.cop_program)]);
    fact("Same GDELT coverage", h("span", {}, ...sameVoice.map((id) => {
      const r = id === voicePick.id ? { id, program: voicePick.program, tier: BY_ID.get(id).properties.tier }
        : voicePick.readings.find((x) => x.id === id);
      return h("button", { type: "button", class: "chip", "data-id": id, onclick: () => select(id) },
        `${readingText(r)}${id === voicePick.id ? ` (pick ${voicePick.n})` : ""}`);
    }), progs.size > 1 ? " Both programs read GDELT: one voice, not two independent sources." : null));
  }
  fact("Data as of", asOfEl(p));
  if (p.published_at) fact("Published", timeEl(p.published_at));
  if (typeof p.valid_until === "string") fact(p.cop_expired ? "Expired" : "Valid until", timeEl(p.valid_until));
  fact("Place", PRECISION[p.precision_class] || "Not stated");
  const pubs = [...new Set((p.sources || []).map((s) => s.publisher).filter(Boolean))];
  if (pubs.length) fact("Sources", pubs.slice(0, 3).join("; ") + (pubs.length > 3 ? ` and ${pubs.length - 3} more` : ""));
  card.append(facts);
  // What else is drawn under the click that opened this card, top first: the card of each is one press away.
  const here = (state.here || []).filter((id) => id !== p.id && BY_ID.has(id));
  if (here.length) {
    const shown = state.hereAll ? here : here.slice(0, 6);
    card.append(h("div", { class: "here" },
      h("p", { class: "here-h", text: `Also at this spot: ${here.length} more finding${here.length === 1 ? "" : "s"}` }),
      h("ul", { class: "list" }, ...shown.map((id) => rowFor(BY_ID.get(id), "row", false)),
        ...(here.length > shown.length ? [h("li", {}, h("button", { type: "button", class: "chip",
          onclick: () => { state.hereAll = true; renderCard(f); } }, `Show ${here.length - shown.length} more`))] : []))));
  }
  const href = p.link || (p.visual && p.visual.url) || prog.site;
  card.append(h("a", { class: "card-open", href, rel: "noopener" }, `Open in ${prog.name} →`));
  renderTimes(card);
}

// ---------------------------------------------------------------- panel
function rowFor(f, cls = "row", fly = true) {
  const p = f.properties, prog = PROGS[p.cop_program];
  const pick = PICK_OF.get(p.id);
  return h("li", {}, h("button", { type: "button", class: cls, "data-id": p.id, onclick: () => select(p.id, { fly }) },
    pick && cls === "pick" ? h("span", { class: "num", "aria-hidden": "true" }, String(pick.n)) : dotFor(p.cop_program, p),
    h("span", {},
      h("span", { class: cls === "pick" ? "pick-title" : "row-title", text: p.title }),
      h("span", { class: cls === "pick" ? "pick-meta" : "row-meta" },
        cls === "pick" ? dotFor(p.cop_program, p) : null, prog.name, " · ", asOfEl(p, "date"),
        p.status_class === "claimed" ? " · claimed" : null, p.cop_expired ? " · expired" : null,
        p.cop_stale ? " · no current data" : null),
      pick && cls === "pick" && otherReadings(pick).length
        ? h("span", { class: "pick-meta", text: `Same GDELT coverage: ${otherReadings(pick).map(readingText).join("; ")}` })
        : null)));
}
// A warning's row says whose judgment it is and how long it stands. Warnings are tier "warning": a forecast or an
// assessment by contract (the kit's linter refuses an observation at that tier), never a measurement, never a pick.
function warningRow(f) {
  const p = f.properties, prog = PROGS[p.cop_program];
  const what = p.kind === "forecast" ? "a forecast" : p.kind === "assessment" ? "an assessment" : "a judgment";
  return h("li", {}, h("button", { type: "button", class: "row", "data-id": p.id, onclick: () => select(p.id) },
    dotFor(p.cop_program, p),
    h("span", {},
      h("span", { class: "row-title", text: p.title }),
      h("span", { class: "row-meta" }, `${prog.name}'s own judgment: ${what}`,
        typeof p.valid_until === "string" ? h("span", {}, "· valid until ", timeEl(p.valid_until)) : null,
        p.cop_stale ? "· no current data" : null))));
}
const count = (n, one, many = `${one}s`) => `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`;
const programOrder = (a, b) => CFG.programs.findIndex((x) => x.id === a.properties.cop_program)
  - CFG.programs.findIndex((x) => x.id === b.properties.cop_program);
function renderPanel() {
  const presets = $("presets");
  presets.replaceChildren(...Object.entries(CFG.presets).map(([k, label]) =>
    h("button", { type: "button", class: "chip", "aria-pressed": String(state.preset === k),
      onclick: () => { state.preset = k; state.showAllAlso = state.showAllOff = state.showAllWarn = false; refreshLayers(); renderPanel(); writeHash(); } }, label)));
  $("show-routine").checked = state.routine;

  const inView = today.features.filter((f) => visibleProgram(f.properties.cop_program));
  const out = inView.filter((f) => standsOut(f.properties));

  // Warnings first, with or without a place: the only judgments in the picture. They used to be marks with no row.
  const warnings = out.filter((f) => f.properties.tier === "warning").sort(programOrder);
  const warnShown = state.showAllWarn ? warnings : warnings.slice(0, 10);
  $("warnings-sec").hidden = !warnings.length;
  $("warnings").replaceChildren(...warnShown.map(warningRow),
    ...(warnings.length > warnShown.length ? [h("li", {}, h("button", { type: "button", class: "chip", onclick: () => { state.showAllWarn = true; renderPanel(); } },
      `Show ${warnings.length - warnShown.length} more`))] : []));

  const pickShown = (pk) => pk && visibleProgram(pk.program) && BY_ID.has(pk.id) && standsOut(BY_ID.get(pk.id).properties);
  const placed = out.filter((f) => f.geometry && f.properties.tier === "anomaly" && !PICK_OF.has(f.properties.id)
    && !pickShown(READING_OF.get(f.properties.id)));
  placed.sort((a, b) => programOrder(a, b) || (b.properties.cop_strength ?? -1e9) - (a.properties.cop_strength ?? -1e9));
  const off = inView.filter((f) => !f.geometry);
  const offAnom = off.filter((f) => f.properties.tier === "anomaly" && standsOut(f.properties));

  // Picks are chosen when the picture is built; one that has since passed its valid-until time is named, not listed.
  const picksInView = PICKS.filter((p) => visibleProgram(p.program) && BY_ID.has(p.id));
  const picks = picksInView.filter((p) => standsOut(BY_ID.get(p.id).properties)).map((p) => BY_ID.get(p.id));
  const lapsed = picksInView.filter((p) => !standsOut(BY_ID.get(p.id).properties)).map((p) => p.n);
  const standing = CFG.programs.filter((p) => routineShown(p.id) && p.routine_in_preset)
    .map((p) => `${p.name}'s ${p.routine_label || "standing notices"}`)
    .concat(CFG.programs.flatMap((p) => (p.also_in || []).filter((r) => r.preset === state.preset)
      .map((r) => `${p.name}'s ${r.label}`)));
  // The line under "Today" when this view has no pick, written from what the view holds (15 Nov 2026). It used to
  // say there were no findings with a place while the map drew them, and that nothing stood out beside a warning.
  let none;
  if (placed.length) {
    none = `No pick in this view today. ${count(placed.length, "finding")} with a place ${placed.length === 1 ? "stands" : "stand"} out here, `
      + "listed under Also watching.";
  } else if (offAnom.length) {
    none = `No finding with a place stands out in this view today. ${count(offAnom.length, "finding")} without a place `
      + `${offAnom.length === 1 ? "does" : "do"}, listed under Off the map.`;
  } else {
    none = "Nothing measured or reported stands out in this view today."
      + (warnings.length ? ` ${count(warnings.length, "warning")} ${warnings.length === 1 ? "is" : "are"} listed above.` : "");
  }
  if (standing.length) none += ` The map shows ${standing.join(" and ")}.`;
  $("picks").replaceChildren(...picks.map((f) => rowFor(f, "pick")),
    ...(lapsed.length ? [h("li", { class: "empty", text: `Pick${lapsed.length === 1 ? "" : "s"} ${lapsed.join(" and ")} `
      + `${lapsed.length === 1 ? "has" : "have"} passed ${lapsed.length === 1 ? "its" : "their"} valid-until time since this picture was built and `
      + `${lapsed.length === 1 ? "is" : "are"} no longer listed.` })] : []),
    ...(picks.length ? [] : [h("li", { class: "empty", text: none })]));

  const alsoShown = state.showAllAlso ? placed : placed.slice(0, 10);
  const expired = inView.filter((f) => STANDS_OUT.includes(f.properties.tier) && f.properties.cop_expired).length;
  $("also").replaceChildren(...(placed.length ? alsoShown.map((f) => rowFor(f)) : [h("li", { class: "empty", text: "Nothing else stands out in this view." })]),
    ...(placed.length > alsoShown.length ? [h("li", {}, h("button", { type: "button", class: "chip", onclick: () => { state.showAllAlso = true; renderPanel(); } },
      `Show ${placed.length - alsoShown.length} more`))] : []),
    ...(expired ? [h("li", { class: "more" }, `${count(expired, "finding")} in this view ${expired === 1 ? "has" : "have"} passed `
      + `${expired === 1 ? "its" : "their"} valid-until time and no longer ${expired === 1 ? "stands" : "stand"} out. `
      + `${expired === 1 ? "It shows" : "They show"} with routine findings.`)] : []));

  const offShown = state.showAllOff ? offAnom : offAnom.slice(0, 8);
  const routineCounts = CFG.programs.map((p) => [p, off.filter((f) => f.properties.cop_program === p.id && !standsOut(f.properties)).length]).filter(([, n]) => n);
  $("offmap").replaceChildren(...offShown.map((f) => rowFor(f)),
    ...(offAnom.length > offShown.length ? [h("li", {}, h("button", { type: "button", class: "chip", onclick: () => { state.showAllOff = true; renderPanel(); } },
      `Show ${offAnom.length - offShown.length} more`))] : []),
    ...routineCounts.map(([p, n]) => h("li", { class: "more" }, `${p.name}: ${n} routine finding${n === 1 ? "" : "s"} with no place. `,
      h("a", { href: p.site, rel: "noopener" }, `Open ${p.name}`))),
    ...(!offAnom.length && !routineCounts.length ? [h("li", { class: "empty", text: "None for this view." })] : []));

  renderTriage();
  renderThreeSignal();
  renderHealth();
  renderBar();
  renderCredits();
  renderHow();
  if (state.selected && BY_ID.has(state.selected)) {
    document.querySelectorAll("[data-id]").forEach((e) => e.toggleAttribute("aria-current", e.getAttribute("data-id") === state.selected));
    document.querySelectorAll("[data-id][aria-current]").forEach((e) => e.setAttribute("aria-current", "true"));
  }
  renderTimes(document);
}
// Outage triage: for each Blackout outage, what Elements, Cordon and Watchfloor show under it. A reading sorts
// reports; the section says so, and shows "nothing found" and "no current data" as the different answers they are.
const TRIAGE_LISTS = { elements: "significant", cordon: "recent", watchfloor: "unusual_items" };
// A hazard that ended before the snapshot (from Elements' ended list) says when it ended; it is not on the map.
function triageItem(row) {
  const ended = row.ended_at ? h("span", { class: "row-meta" }, " (ended ", timeEl(row.ended_at), ")") : null;
  return BY_ID.has(row.id)
    ? h("button", { type: "button", class: "tri-item", "data-id": row.id, onclick: () => select(row.id) }, row.title, ended)
    : h("span", { class: "tri-item" }, row.title, ended);
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
    h("div", { class: "row-meta" }, r.outage_kind === "power" ? "ODIN reading at " : "Began ",
      timeEl(r.outage.started_at || r.outage.data_as_of), r.outage.source ? ` · ${r.outage.source}` : null),
    h("div", { class: "badges" },
      h("span", { class: "badge" + (down ? " stale" : " measure"), text: `Reading: ${r.reading}` }),
      h("span", { class: "badge", text: "Not a warning or a forecast" })),
    h("p", { class: "tri-text", text: r.reading_text }),
    h("dl", { class: "tri-layers" }, ...layers),
    r.base_rate ? h("p", { class: "tri-rate" }, h("strong", { text: "Base rate. " }), r.base_rate.text) : null,
    r.eaglei ? h("p", { class: "tri-rate" }, h("strong", { text: "How often this state goes over the line. " }),
      `${r.eaglei.text} ${r.eaglei.caveat}`) : null,
    r.ooni_now ? h("p", { class: "tri-rate" }, h("strong", { text: "OONI now. " }), r.ooni_now.title) : null,
    ...(r.caveats || []).map((c) => h("p", { class: "tri-caveat", text: c })),
    triageQuestion(r.question_draft),
    triageQuestion(r.cause_question_draft, "Second question, on cause (a draft, not registered)"));
}
// The drafted Verdict question: how this reading could be scored later. A draft, never shown as a forecast.
function triageQuestion(q, label) {
  if (!q) return null;
  const rise = q.rise_form;
  return h("details", { class: "tri-q" },
    h("summary", { text: label || "Question to score it by (a draft, not registered)" }),
    h("p", { text: q.note }),
    h("p", {}, h("strong", { text: "Question. " }), q.question),
    h("p", {}, h("strong", { text: "Settled by. " }), q.rule_draft),
    h("p", {}, h("strong", { text: "Window ends. " }), timeEl(q.window_ends)),
    q.verdict_id ? h("p", { class: "row-meta", text: `Verdict id if registered: ${q.verdict_id}` }) : null,
    q.reference ? h("p", {}, h("strong", { text: "Reference. " }), q.reference) : null,
    ...(q.notes || []).map((n) => h("p", { class: "tri-caveat", text: n })),
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
    const sw = b.signal_countries_would_read;
    foot.append(h("p", { class: "note" },
      sw && b.countries_with_signal
        ? `Base rates over ${b.counted_days} counted days in COP's archive: ${b.countries_with_signal} of ${b.countries} countries showed a signal on at least one. ` +
          `On those countries' days an outage would have read natural on ${pct(sw.natural)}, tension context on ${pct(sw["tension context"])} and open on ${pct(sw.open)}, whatever its cause. ` +
          `Across all ${b.countries} countries' ${b.country_days.toLocaleString("en-US")} country-days: natural ${pct(b.would_read.natural)}, ` +
          `tension context ${pct(b.would_read["tension context"])}, open ${pct(b.would_read.open)}. `
        : `Base rates: over ${b.country_days.toLocaleString("en-US")} country-days in COP's archive, an outage would have read natural on ${pct(b.would_read.natural)}, ` +
          `tension context on ${pct(b.would_read["tension context"])} and open on ${pct(b.would_read.open)}, whatever its cause. `,
      ...(b.cautions || []).map((c) => `${c} `),
      triage.windows && triage.windows.provisional ? "The time windows are provisional." : null));
    const s = b.states;
    if (s && s.state_days) foot.append(h("p", { class: "note" },
      (s.states_with_signal != null && s.days
        ? `US state power outages, over ${s.days} days: ${s.states_with_signal} of ${s.states} states (DC and Puerto Rico included) had a hazard that takes power lines down on at least one. ` +
          `Across all ${s.state_days.toLocaleString("en-US")} state-days, one would have read natural on ${pct(s.would_read.natural)} `
        : `US state power outages: over ${s.state_days.toLocaleString("en-US")} state-days, one would have read natural on ${pct(s.would_read.natural)} `) +
      `and open on ${pct(s.would_read.open)}, whatever its cause. `, ...(s.cautions || []).map((c) => `${c} `)));
  } else if (triage.records.length) {
    foot.append(h("p", { class: "note", text: "No base rates were available for this picture, so the readings stand without them." }));
  }
  const unplaced = triage.unplaced || [];
  if (unplaced.length) foot.append(h("p", { class: "note" },
    `Not read: ${count(unplaced.length, "Cloudflare Radar entry", "Cloudflare Radar entries")} ${unplaced.length === 1 ? "names" : "name"} no place, so no country layer can sit under ${unplaced.length === 1 ? "it" : "them"}: `,
    ...unplaced.map((u, i) => h("span", {}, i ? "; " : "", triageItem(u)))));
}
// The three-signal join: which of three signals fall in one area within the window, each layer's items in its own
// words, Vis's weather beside them and how often each fires anyway. It shows what lines up, never what it means.
const TS_SIGNALS = { interference: ["noisefloor", "Interference (Noisefloor)"],
  notice: ["cordon", "New notice (Cordon)"], connectivity: ["blackout", "Connectivity (Blackout)"] };
const TS_PROGRAMS = ["noisefloor", "cordon", "blackout", "vis"];
function threeSignalRecord(r) {
  const nodata = r.no_current_data || [];
  const signals = Object.entries(TS_SIGNALS).map(([key, [pid, name]]) => {
    const dead = nodata.includes(pid) || (key === "notice" && nodata.includes("cordon:navwarn"));
    const items = r.signals[key].items;
    const said = r.signals[key].present ? `Present: ${count(items.length, "item")}.` : dead ? "No current data." : "Not present.";
    return h("div", { class: "tri-layer" + (dead ? " dead" : "") },
      h("dt", {}, dotFor(pid, { cop_stale: dead }), name),
      h("dd", {}, said, items.length ? h("span", { class: "tri-items" }, ...items.slice(0, 4).map(triageItem)) : null));
  });
  const span = r.tightest_span;
  const c = r.context;
  return h("li", { class: "tri" },
    h("span", { class: "tri-outage", text: r.area_name }),
    h("div", { class: "row-meta" }, "Read at ", timeEl(r.read_at)),
    h("div", { class: "badges" },
      h("span", { class: "badge" + (r.reading === "incomplete" ? " stale" : " measure"), text: `Reading: ${r.reading}` }),
      h("span", { class: "badge", text: "Not a warning or a forecast" })),
    h("p", { class: "tri-text", text: r.reading_text }),
    h("dl", { class: "tri-layers" }, ...signals),
    span ? h("p", { class: "tri-rate" }, h("strong", { text: "Tightest span. " }),
      `All three within ${span.hours} hours, `, timeEl(span.from), " to ", timeEl(span.to), ".") : null,
    h("p", { class: "tri-rate" }, h("strong", { text: "Weather (Vis, context only). " }), r.weather.text),
    h("p", { class: "tri-rate" }, h("strong", { text: "Also in the area. " }),
      `${count(c.notices_other.length, "other new warning")}, ${count(c.notices_standing.length, "standing notice")}, ` +
      `${count(c.single_source_dips.length, "single-source dip")}, ${count(c.embassy_alerts.length, "embassy alert")}.`),
    r.base_rate ? h("p", { class: "tri-rate" }, h("strong", { text: "Base rate. " }), `${r.base_rate.text} ${r.base_rate.caution}`)
      : h("p", { class: "tri-caveat", text: "No archive was read for this picture, so there is no base rate." }),
    ...(r.caveats || []).map((t) => h("p", { class: "tri-caveat", text: t })),
    triageQuestion(r.question_draft));
}
function renderThreeSignal() {
  const sec = $("three-signal-sec");
  sec.hidden = !TS_PROGRAMS.some(visibleProgram);
  if (sec.hidden) return;
  const list = $("three-signal");
  if (!threeSignal) {
    $("three-signal-note").textContent = "The three-signal join is not available for this picture.";
    list.replaceChildren();
    return;
  }
  $("three-signal-note").textContent = `${threeSignal.what} ${threeSignal.not_a_warning}`;
  list.replaceChildren(...threeSignal.records.map(threeSignalRecord));
}
function renderHealth() {
  $("health").replaceChildren(...health.programs.map((hp) => {
    const prog = PROGS[hp.id];
    const lint = hp.lint;
    const bits = [];
    bits.push(hp.data_as_of ? ["Data through ", timeEl(hp.data_as_of)] : ["No data"]);
    // Counted from the picture as the page reads it (15 Nov 2026): an anomaly is unusual against its program's own
    // baseline, a warning is a program's own judgment, and an item past its valid-until time no longer stands out.
    const live = today.features.filter((f) => f.properties.cop_program === hp.id && !f.properties.cop_expired);
    const unusual = live.filter((f) => f.properties.tier === "anomaly").length;
    const warned = live.filter((f) => f.properties.tier === "warning").length;
    bits.push(` · ${count(hp.items, "finding")}, ${unusual} unusual against its own baseline`
      + (warned ? `, ${count(warned, "warning")}` : ""));
    if (hp.fetch.ok) bits.push(` · fetched in ${hp.fetch.seconds} s`);
    else bits.push(` · fetch failed${hp.from_previous ? ": showing the last good copy" : ""}`);
    if (lint) bits.push(lint.errors ? ` · ${lint.errors} contract problems` : " · passes the contract checks");
    // A program that reports each source's health (config "source_health"): the sources its last run did not read well.
    const src = hp.sources;
    if (src && src.total) bits.push(src.not_read.length
      ? ` · ${src.not_read.length} of ${src.total} sources not current: ${src.not_read.map((s) => `${s.id} (${s.state || "not read"})`).join(", ")}`
      : ` · all ${src.total} sources current`);
    const bad = hp.dropped ? hp.dropped.count : 0;  // items the build could not read, left out of the picture
    if (bad) bits.push(` · ${count(bad, "malformed item")} left out`);
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
// "How COP reads", first line: what today's picture holds, counted from the snapshot (15 Nov 2026). The page used to
// say every finding was a measurement outside its usual range, while it carried forecasts and mostly routine items.
function renderHow() {
  const all = today.features.map((f) => f.properties);
  const forecasts = all.filter((p) => p.kind === "forecast").length;
  const judgments = all.filter((p) => p.kind === "assessment").length;
  const observed = all.length - forecasts - judgments;
  const anomalies = all.filter((p) => p.tier === "anomaly" && !p.cop_expired).length;
  const warnings = all.filter((p) => p.tier === "warning" && !p.cop_expired).length;
  let text = `Today's picture holds ${count(all.length, "finding")}. ${observed.toLocaleString("en-US")} `
    + `${observed === 1 ? "is a measurement or a report" : "are measurements or reports"}, each from one program, and `
    + `${anomalies ? anomalies.toLocaleString("en-US") : "none"} of them ${anomalies === 1 ? "stands" : "stand"} out against `
    + `${anomalies === 1 ? "its" : "their"} program's own baseline. `;
  if (forecasts + judgments) {
    const judged = [forecasts ? count(forecasts, "forecast") : null, judgments ? count(judgments, "assessment") : null].filter(Boolean);
    text += `${judged.join(" and ")} ${forecasts + judgments === 1 ? "is a judgment" : "are judgments"}, not `
      + `${forecasts + judgments === 1 ? "a measurement" : "measurements"}: each carries a likelihood and a separate confidence, and is `
      + `labelled as its program's own. ${warnings ? count(warnings, "is a warning", "are warnings") : "None is a warning"} today.`;
  } else {
    text += "No program has published a forecast or an assessment today. When one does, it shows here as that program's "
      + "own judgment, with a likelihood and a separate confidence.";
  }
  $("how-counts").textContent = text;
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
      h("span", { class: "lg" }, h("span", { class: "swatch-line", "aria-hidden": "true" }), "Line: a route or trackline in a notice"),
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
