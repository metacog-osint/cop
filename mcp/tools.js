// The tools of COP's MCP server, as plain functions over the files tools/agent_index.py writes to site/data/agent/
// (index.json, items/NN.json, programs.json, verdict.json) plus COP's own data/picks.json. No SDK and no Cloudflare
// imports, so `node --test mcp/` can run them directly. server.js wraps them for MCP.
//
// Every result carries `as_of` (when the data it rests on was built), `stale` where a part is a last good copy, and
// the attribution lines of the programs it draws on, so an agent can cite and date every answer.

export const LIMIT_DEFAULT = 20;
export const LIMIT_MAX = 50;
export const KINDS = ["observation", "assessment", "forecast"];
export const TIERS = ["routine", "anomaly", "warning"]; // routine and anomaly are measured; warning is judged
const TIER_RANK = { warning: 0, anomaly: 1, routine: 2 };
const HOUR = 3600 * 1000;

const MEASURED_NOTE =
  "Every item here is a measurement or a report (kind observation), not a warning or a prediction, unless its tier " +
  "is warning or its kind is assessment or forecast. Tier anomaly means unusual against that program's own baseline.";

export class ToolError extends Error {}

// Index rows are columnar ({fields, rows}). The hot path filters the raw arrays by column number and builds objects
// only for the rows it returns: turning all ~1,000 rows into objects first cost more CPU than the search itself.
const columnsCache = new WeakMap();
function cols(index) {
  let c = columnsCache.get(index);
  if (!c) {
    c = Object.fromEntries(index.fields.map((k, i) => [k, i]));
    columnsCache.set(index, c);
  }
  return c;
}

export function toObject(index, r) {
  const f = index.fields;
  const o = {};
  for (let i = 0; i < f.length; i++) o[f[i]] = r[i];
  return o;
}

// Every row as an object (tests and small callers; the tools themselves don't need it).
const objects = new WeakMap();
export function rowsOf(index) {
  if (!objects.has(index)) objects.set(index, index.rows.map((r) => toObject(index, r)));
  return objects.get(index);
}

function ageHours(iso, now) {
  const t = Date.parse(iso || "");
  return Number.isFinite(t) ? Math.round(((now - t) / HOUR) * 10) / 10 : null;
}

export const COP_CREDIT = "COP, The Intelligence Company (https://cop.theintelligence.company)";

function attributionFor(index, programIds) {
  const out = [COP_CREDIT];
  for (const id of [...new Set(programIds)].sort()) {
    const p = index.programs[id];
    if (p && p.attribution) out.push(p.attribution);
  }
  return out;
}

function publicRow(r) {
  const { shard, ...rest } = r; // shard is the server's business
  return rest;
}

function busEnvelope(index, now) {
  return {
    as_of: index.bus_generated_at,
    age_hours: ageHours(index.bus_generated_at, now),
    source: index.bus_url,
  };
}

// --- suite_status ---------------------------------------------------------------------------------------------------

export function suiteStatus(programs, now) {
  return {
    as_of: programs.bus_generated_at,
    age_hours: ageHours(programs.bus_generated_at, now),
    source: programs.bus_url,
    rebuilt_every_hours: programs.bus_refresh_hours,
    note:
      "COP merges every live program's feed into one snapshot. state: fresh, late or stale judges each program's " +
      "newest data against its own fresh_hours and stale_hours; a program with from_previous true failed to fetch " +
      "and shows its last good copy. Planned programs publish nothing yet.",
    programs: programs.programs.map((p) => ({
      id: p.id,
      name: p.name,
      status: p.status,
      ...(p.status_note ? { status_note: p.status_note } : {}),
      family: p.family,
      site: p.site ?? null,
      feed: p.feed ?? null,
      terms: p.terms ?? null,
      fresh_hours: p.fresh_hours ?? null,
      stale_hours: p.stale_hours ?? null,
      in_cop: p.cop ?? null,
    })),
  };
}

// --- search_items ---------------------------------------------------------------------------------------------------

function toRad(d) {
  return (d * Math.PI) / 180;
}

function haversineKm(lat1, lon1, lat2, lon2) {
  const a =
    Math.sin(toRad(lat2 - lat1) / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(toRad(lon2 - lon1) / 2) ** 2;
  return 6371.0088 * 2 * Math.asin(Math.min(1, Math.sqrt(a)));
}

// Distance from (lat, lon) to an item: to its box when it has one (0 inside it), else to its point.
export function distanceKm(row, lat, lon) {
  if (row.bbox) {
    const [w, s, e, n] = row.bbox;
    const clat = Math.min(Math.max(lat, s), n);
    const clon = w <= e ? Math.min(Math.max(lon, w), e) : lon; // a box across the antimeridian: compare latitude only
    return haversineKm(lat, lon, clat, clon);
  }
  if (row.point) return haversineKm(lat, lon, row.point[1], row.point[0]);
  return null;
}

export function searchItems(index, args, now) {
  const a = args || {};
  const known = Object.keys(index.programs);
  const programs = a.program == null ? null : [].concat(a.program);
  for (const p of programs || []) {
    if (!known.includes(p)) throw new ToolError(`Unknown program "${p}". Programs in the snapshot: ${known.join(", ")}.`);
  }
  const country = a.country ? String(a.country).toUpperCase() : null;
  const text = a.text ? String(a.text).toLowerCase() : null;
  const since = a.since ? Date.parse(a.since) : null;
  if (a.since && !Number.isFinite(since)) throw new ToolError(`since must be an ISO 8601 time, got "${a.since}".`);
  const near = a.near || null;
  const limit = Math.min(Math.max(1, a.limit ?? LIMIT_DEFAULT), LIMIT_MAX);

  const C = cols(index);
  const hits = []; // [rank, distance or 0, data_as_of, id, raw row]
  for (const r of index.rows) {
    if (programs && !programs.includes(r[C.program])) continue;
    if (a.kind && r[C.kind] !== a.kind) continue;
    if (a.tier && r[C.tier] !== a.tier) continue;
    if (country && !(r[C.countries] || []).includes(country)) continue;
    if (text && !((r[C.title] || "").toLowerCase().includes(text) || (r[C.id] || "").toLowerCase().includes(text))) {
      continue;
    }
    if (since !== null) {
      const t = Date.parse(r[C.data_as_of] || r[C.published_at] || "");
      if (!(t >= since)) continue;
    }
    let km = null;
    if (near) {
      km = distanceKm({ point: r[C.point], bbox: r[C.bbox] }, near.lat, near.lon);
      if (km === null || km > near.km) continue;
    }
    hits.push([TIER_RANK[r[C.tier]] ?? 9, km ?? 0, String(r[C.data_as_of] || ""), String(r[C.id]), r, km]);
  }
  hits.sort((x, y) => x[0] - y[0] || x[1] - y[1] || (y[2] < x[2] ? -1 : y[2] > x[2] ? 1 : 0) || (x[3] < y[3] ? -1 : 1));
  const page = hits.slice(0, limit).map((h) => {
    const o = toObject(index, h[4]);
    return h[5] === null ? o : { ...o, distance_km: Math.round(h[5] * 10) / 10 };
  });
  return {
    ...busEnvelope(index, now),
    matched: hits.length,
    returned: page.length,
    ...(hits.length > page.length ? { more: `${hits.length - page.length} more; narrow the filters or raise limit (max ${LIMIT_MAX})` } : {}),
    order: near ? "warnings, then anomalies, then routine; nearest first within each" : "warnings, then anomalies, then routine; newest first within each",
    note: MEASURED_NOTE,
    items: page.map(publicRow),
    attribution: attributionFor(index, page.map((r) => r.program)),
  };
}

// --- get_item -------------------------------------------------------------------------------------------------------

export function findRow(index, id) {
  const i = cols(index).id;
  const r = index.rows.find((x) => x[i] === id);
  return r ? toObject(index, r) : null;
}

export function getItem(index, shardDoc, id, now) {
  const row = findRow(index, id);
  const feature = row && shardDoc && shardDoc.items ? shardDoc.items[id] : null;
  if (!feature) {
    throw new ToolError(
      `No item "${id}" in the current snapshot (built ${index.bus_generated_at}). Items leave the snapshot when ` +
        "their program stops publishing them; search_items lists what is there now.",
    );
  }
  const prog = index.programs[row.program] || {};
  return {
    ...busEnvelope(index, now),
    stale: row.stale,
    program: { id: row.program, name: prog.name ?? null, state: prog.state ?? null, licence: prog.licence ?? null },
    note:
      "properties.sources lists every source with its publisher, licence and grade; cite them. " +
      "Feed contract: https://github.com/metacog-osint/tic-doctrine (tic-feed/2). " + MEASURED_NOTE,
    item: feature,
    attribution: attributionFor(index, [row.program]),
  };
}

// --- top_picks ------------------------------------------------------------------------------------------------------

export function topPicks(index, picks, now) {
  const items = [];
  for (const p of picks.picks || []) {
    const r = findRow(index, p.id);
    items.push({ rank: p.n, reason: p.reason, ...(r ? publicRow(r) : { id: p.id, program: p.program, missing: true }) });
  }
  return {
    ...busEnvelope(index, now),
    note:
      "Each pick is one program's strongest measured anomaly that has a place, one program at a time. Picks are " +
      "measurements, not warnings, and strength is never compared across programs.",
    picks: items,
    attribution: attributionFor(index, items.map((i) => i.program)),
  };
}

// --- outage_triage --------------------------------------------------------------------------------------------------

const TRIAGE_PROGRAMS = ["blackout", "elements", "cordon", "watchfloor"];

export function outageTriage(index, triage, args, now) {
  if (!triage) {
    throw new ToolError(
      "Outage triage was not built for this snapshot. That says nothing about outages: use search_items with " +
        "program [\"blackout\"] for Blackout's own items.",
    );
  }
  const country = args && args.country ? String(args.country).toUpperCase() : null;
  const records = (triage.records || []).filter((r) => !country || r.country === country);
  const blackout = (triage.layer_states || {}).blackout;
  const dead = blackout === "stale" || blackout === "missing";
  return {
    as_of: triage.snapshot_generated_at,
    age_hours: ageHours(triage.snapshot_generated_at, now),
    source: index.bus_url,
    join: triage.join.id,
    what: triage.what,
    note:
      triage.not_a_warning +
      " Quote a reading with its base_rate and caveats, and say what each layer showed in its own words (layers.*.showed). " +
      "The readings: natural, deliberate, open, unconfirmed (a single-source dip, no reading given), incomplete (a layer " +
      "has no current data). The time windows are provisional.",
    readings: triage.readings,
    layer_states: triage.layer_states,
    windows: triage.windows,
    base_rates: triage.base_rates,
    ...(triage.base_rates ? {} : { base_rates_note: "No base rates were available for this snapshot; the readings stand without them." }),
    count: records.length,
    ...(records.length
      ? {}
      : {
          none: dead
            ? "Blackout has no current data, so there is nothing to triage. That is not the same as no outages."
            : country
              ? `No outage for ${country} in Blackout's current data.`
              : "No outages in Blackout's current data.",
        }),
    records,
    attribution: attributionFor(index, TRIAGE_PROGRAMS),
  };
}

// --- Verdict --------------------------------------------------------------------------------------------------------

function verdictPart(verdict, part) {
  const p = verdict[part];
  if (!p) throw new ToolError(`Verdict's ${part} could not be fetched for this snapshot and no earlier copy exists.`);
  return p;
}

function verdictEnvelope(verdict, part, now) {
  const p = verdict[part];
  return {
    as_of: p.fetched_at ?? verdict.generated_at,
    age_hours: ageHours(p.fetched_at ?? verdict.generated_at, now),
    stale: Boolean(p.stale),
    source: verdict.source,
  };
}

export const FORECAST_STATUSES = ["open", "resolved", "all"];

export function listForecasts(verdict, args, now) {
  const part = verdictPart(verdict, "forecasts");
  const status = (args && args.status) || "all";
  const items = (part.data.items || []).filter((f) => status === "all" || f.status === status);
  return {
    ...verdictEnvelope(verdict, "forecasts", now),
    ledger_as_of: part.data.as_of ?? null,
    note:
      "Verdict logs each forecast before its outcome can be known, with the rule that will settle it; nothing " +
      "registered is edited or removed. probability is the number; likelihood is its ICD 203 term; confidence is " +
      "separate from likelihood. Quote them as given.",
    count: items.length,
    forecasts: items,
    attribution: [verdict.attribution],
  };
}

export function forecastRecord(verdict, now) {
  const part = verdictPart(verdict, "scores");
  const s = part.data || {};
  const resolved = s.resolved ?? 0;
  return {
    ...verdictEnvelope(verdict, "scores", now),
    note:
      resolved === 0
        ? "No forecast has resolved yet, so there is no accuracy record. Do not infer skill from the open forecasts."
        : "Brier score (lower is better) with its decomposition and skill against the base rate, by program.",
    scores: s,
    attribution: [verdict.attribution],
  };
}

const DAY = 24 * HOUR;

export function upcomingEvents(verdict, args, now) {
  const part = verdictPart(verdict, "calendar");
  const days = Math.min(Math.max(1, (args && args.days) ?? 30), 366);
  const origin = args && args.origin;
  const from = new Date(now).toISOString().slice(0, 10);
  const to = new Date(now + days * DAY).toISOString().slice(0, 10);
  const events = (part.data.events || []).filter(
    (e) => e.date && e.date >= from && e.date <= to && (!origin || e.origin === origin),
  );
  return {
    ...verdictEnvelope(verdict, "calendar", now),
    window: { from, to },
    note:
      "Dated, resolvable events worth forecasting. verified false means the date comes from secondary reporting. " +
      "origin cordon marks events Cordon found (bulletin expiries, travel advisory changes).",
    count: events.length,
    events,
    attribution: [verdict.attribution],
  };
}
