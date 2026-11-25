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
// Columns listed in `codes` (program, kind, tier, status_class, us_person_data, stale; 15 Nov 2026; and
// data_as_of_precision, 16 Nov 2026) hold a number into
// codes[column], so filters compare numbers and only returned rows are decoded. An index without `codes` reads as is.
const columnsCache = new WeakMap();
function cols(index) {
  let c = columnsCache.get(index);
  if (!c) {
    c = Object.fromEntries(index.fields.map((k, i) => [k, i]));
    columnsCache.set(index, c);
  }
  return c;
}

const decodersCache = new WeakMap();
function decoders(index) {
  let d = decodersCache.get(index);
  if (!d) {
    const codes = index.codes || {};
    d = index.fields.map((k) => codes[k] || null);
    decodersCache.set(index, d);
  }
  return d;
}

// The stored form of a value in a column: its code, or -1 when no row has it (so nothing matches).
function stored(index, field, value) {
  const list = (index.codes || {})[field];
  return list ? list.indexOf(value) : value;
}

export function toObject(index, r) {
  const f = index.fields;
  const d = decoders(index);
  const o = {};
  for (let i = 0; i < f.length; i++) o[f[i]] = d[i] ? d[i][r[i]] : r[i];
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

// An item past its own valid_until is expired (ideas#164, 24 Nov 2026): its program still lists it, but what it
// reports no longer holds. Every row says so, judged at the time of the call, since the snapshot is up to 2 h old.
export function isExpired(validUntil, now) {
  const t = Date.parse(validUntil || "");
  return Number.isFinite(t) && t < now;
}

function publicRow(r, now) {
  const { shard, ...rest } = r; // shard is the server's business
  if (rest.data_as_of_precision == null) delete rest.data_as_of_precision; // said only for a date-only item
  if (now !== undefined) rest.expired = isExpired(rest.valid_until, now);
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
      "and shows its last good copy. Planned programs publish nothing yet. page_check is the hub's last site check " +
      "of the program's page (opened in headless Chrome twice a day): ok false means the page failed to load, run " +
      "its scripts or draw its map, whatever its data says; null means the check does not cover that page or was " +
      "not readable at COP's last build.",
    page_check: programs.page_check ?? null,
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
      page_check: p.page_check ?? null,
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
  const state = a.state ? String(a.state).toUpperCase() : null;
  const text = a.text ? String(a.text).toLowerCase() : null;
  const since = a.since ? Date.parse(a.since) : null;
  if (a.since && !Number.isFinite(since)) throw new ToolError(`since must be an ISO 8601 time, got "${a.since}".`);
  const near = a.near || null;
  const limit = Math.min(Math.max(1, a.limit ?? LIMIT_DEFAULT), LIMIT_MAX);
  const offset = Math.max(0, Math.floor(a.offset ?? 0));
  const includeExpired = Boolean(a.include_expired);

  const C = cols(index);
  const wantPrograms = programs && programs.map((p) => stored(index, "program", p));
  const wantKind = a.kind ? stored(index, "kind", a.kind) : null;
  const wantTier = a.tier ? stored(index, "tier", a.tier) : null;
  const tiers = decoders(index)[C.tier];
  const rank = (t) => TIER_RANK[tiers ? tiers[t] : t] ?? 9;
  const hits = []; // [rank, distance or 0, data_as_of, id, raw row]
  let expiredOut = 0;
  for (const r of index.rows) {
    if (wantPrograms && !wantPrograms.includes(r[C.program])) continue;
    if (wantKind !== null && r[C.kind] !== wantKind) continue;
    if (wantTier !== null && r[C.tier] !== wantTier) continue;
    if (country && !(r[C.countries] || []).includes(country)) continue;
    if (state && !(C.subdivisions === undefined ? [] : r[C.subdivisions] || []).includes(state)) continue;
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
    if (!includeExpired && C.valid_until !== undefined && isExpired(r[C.valid_until], now)) {
      expiredOut++;
      continue;
    }
    hits.push([rank(r[C.tier]), km ?? 0, String(r[C.data_as_of] || ""), String(r[C.id]), r, km]);
  }
  hits.sort((x, y) => x[0] - y[0] || x[1] - y[1] || (y[2] < x[2] ? -1 : y[2] > x[2] ? 1 : 0) || (x[3] < y[3] ? -1 : 1));
  // Paging (17 Nov 2026): offset skips that many matches in this order, so offset 50 then 100 walks every match.
  const page = hits.slice(offset, offset + limit).map((h) => {
    const o = toObject(index, h[4]);
    return h[5] === null ? o : { ...o, distance_km: Math.round(h[5] * 10) / 10 };
  });
  return {
    ...busEnvelope(index, now),
    matched: hits.length,
    returned: page.length,
    ...(expiredOut
      ? { expired_left_out: expiredOut, expired_note: `${expiredOut} matching items are past their valid_until and are left out; pass include_expired true to list them (each row says expired).` }
      : {}),
    ...(offset ? { offset } : {}),
    ...(hits.length > offset + page.length
      ? {
          next_offset: offset + page.length,
          more: `${hits.length - offset - page.length} more; pass offset ${offset + page.length} for the next page, or narrow the filters`,
        }
      : {}),
    order: near ? "warnings, then anomalies, then routine; nearest first within each" : "warnings, then anomalies, then routine; newest first within each",
    note: MEASURED_NOTE,
    items: page.map((r) => publicRow(r, now)),
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

// Watchfloor and Context both read GDELT (15 Nov 2026): COP counts them as one voice per zone, so a pick of that voice
// carries each program's reading of the zone under `readings`, each its own item with its own id, link and licence.
function readingOf(index, r, now) {
  const row = findRow(index, r.id);
  const prog = index.programs[r.program] || {};
  return {
    ...(row ? publicRow(row, now) : { id: r.id, program: r.program, missing: true }),
    warning_problem_id: r.warning_problem_id ?? null,
    strength: r.strength ?? null,
    licence: prog.licence ?? null,
  };
}

// direction: site/data/direction.json or null. Its picks list says which moving places each pick falls in.
export function topPicks(index, picks, now, direction = null) {
  const places = new Map(((direction && direction.picks) || []).map((x) => [x.pick, x]));
  const items = [];
  for (const p of picks.picks || []) {
    const r = findRow(index, p.id);
    const item = { rank: p.n, reason: p.reason, ...(r ? publicRow(r, now) : { id: p.id, program: p.program, missing: true }) };
    if (p.voice) Object.assign(item, { voice: p.voice, readings: (p.readings || []).map((x) => readingOf(index, x, now)) });
    const at = places.get(p.id);
    if (at) {
      item.places = at.places;
      item.in_rising_place = Boolean(at.rising);
      if (at.rising) {
        const names = at.places.filter((x) => x.direction === "rising").map((x) => `${x.name} (${x.up.join(", ")} up)`);
        item.reason = `${p.reason}. Listed first: it falls in a place COP reads as Rising: ${names.join("; ")}`;
      }
    }
    items.push(item);
  }
  // A pick in a Rising place comes first (ideas#164); the rest keep their order. picked_rank is the build's own order.
  items.forEach((x) => { x.picked_rank = x.rank; });
  items.sort((a, b) => Number(Boolean(b.in_rising_place)) - Number(Boolean(a.in_rising_place)) || a.picked_rank - b.picked_rank);
  items.forEach((x, i) => { x.rank = i + 1; });
  return {
    ...busEnvelope(index, now),
    note:
      "Each pick is one program's strongest measured anomaly that has a place, one program at a time. Picks are " +
      "measurements, not warnings, and strength is never compared across programs. Watchfloor and Context both " +
      "read GDELT, so they count as one voice: they share one place in each round, a zone gives at most one pick, " +
      "and that pick lists both programs' readings of the zone under readings (its own first). Their agreement is " +
      "one source read twice, never two independent confirmations; cite each reading by its own id. Picks are " +
      "chosen when the snapshot is built and never include an item already past its valid_until; a pick marked " +
      "expired true has passed it since, so treat it as no longer current. places lists the places on COP's " +
      "direction board that a pick falls in, when that place is moving; a pick in a place read as Rising is listed " +
      "first and its reason says so (picked_rank is its order before that). A place's word counts layers moving " +
      "against their own last two weeks: it is not a warning and says nothing about the pick's cause.",
    direction_as_of: direction ? direction.generated_at ?? null : null,
    picks: items,
    attribution: attributionFor(index, items.flatMap((i) => [i.program, ...(i.readings || []).map((x) => x.program)])),
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
  const notRead = (triage.not_read || []).filter((r) => !country || (r.countries || []).includes(country));
  const notReadText = notRead.length
    ? ` Blackout carries ${notRead.length === 1 ? "a report" : `${notRead.length} reports`} that triage does not read ` +
      `(not_read): ${notRead.map((r) => `${r.id} (${r.source})`).join("; ")}. Use get_item on ${notRead.length === 1 ? "it" : "each"}.`
    : "";
  return {
    as_of: triage.snapshot_generated_at,
    age_hours: ageHours(triage.snapshot_generated_at, now),
    source: index.bus_url,
    join: triage.join.id,
    what: triage.what,
    note:
      triage.not_a_warning +
      " Quote a reading with its base_rate and caveats, and say what each layer showed in its own words (layers.*.showed). " +
      "The readings: natural, tension context (a fresh security or demonstration notice, or press attention unusual " +
      "against the country's own last 28 days, with no active hazard: the context the outage sits in, never its cause), " +
      "open, unconfirmed (a single-source dip, no reading given), incomplete (a layer has no current data). " +
      "No reading says an outage was deliberate; Blackout's own outage item says whether OONI's blocking supports a " +
      "shutdown, with no probability. Each record's " +
      "outage.source says which Blackout kind it is (IODA, a Cloudflare-verified anomaly or a Radar Outage Center " +
      "entry); started_at is when it began; network is set when Cloudflare saw it on one network; stated_cause is " +
      "Cloudflare's own, quoted. unplaced lists Radar entries that name no place and so are not read. not_read lists Blackout's outage reports of a kind triage does not read (NetBlocks' reports): a country there has a report, so never say it has no outage. Records with outage_kind \"power\" are US state power outages (Blackout's BO-IND-14, " +
      "ODIN's reporting utilities, us_state names the state): they are read against hazards in or near that state, " +
      "Cordon and Watchfloor answer \"Not covered\" there (neither nothing found nor no data), so they read natural, " +
      "open or incomplete only (power_readings), and eaglei is how often that state goes over the line in EAGLE-I's " +
      "history. A hazard row with ended_at had ended before the snapshot: it comes from Elements' list of items that " +
      "ended in the last 7 days (ended_items gives that list's state; when it is missing the caveats say so). " +
      "The time windows are provisional. question_draft (whether OONI's own hourly counts show blocking rising in " +
      "the country, settled on OONI's API; a full shutdown silences the probes, so it can read No) and " +
      "cause_question_draft (whether an outside " +
      "source names a government order) are drafted questions that could score the reading later: a draft is not a " +
      "registered Verdict forecast and has no likelihood, so never quote one as a forecast (list_forecasts has the " +
      "registered ones). replay_rate (internet-outage records) and replay say how often Access Now's documented 2025 " +
      "shutdowns (the STOP dataset, replayed through triage) would have read the same way, beside how often those " +
      "countries' ordinary days would have: quote both figures together with replay.caveats (what both runs say) " +
      "and the record's replay_rate.caveats (what its own run adds; replay.two_layer.caveats and " +
      "replay.three_layer.caveats give each run's), credit Access Now, " +
      "#KeepItOn STOP, and never say triage detects, finds or predicts shutdowns. A record with no replay_rate " +
      "(US power, unconfirmed) has no replay figure.",
    readings: triage.readings,
    ...(triage.power_readings ? { power_readings: triage.power_readings } : {}),
    layer_states: triage.layer_states,
    windows: triage.windows,
    ...(triage.ended_items ? { ended_items: triage.ended_items } : {}),
    base_rates: triage.base_rates,
    replay: triage.replay || null,
    ...(triage.replay ? {} : { replay_note: "No STOP replay figures were available for this snapshot; the readings carry none." }),
    ...(triage.base_rates ? {} : { base_rates_note: "No base rates were available for this snapshot; the readings stand without them." }),
    count: records.length,
    ...(records.length
      ? {}
      : {
          none: dead
            ? "Blackout has no current data, so there is nothing to triage. That is not the same as no outages."
            : country
              ? `No outage for ${country} in the kinds triage reads (${(triage.inputs || []).join(", ")}).${notReadText}`
              : `No outages in the kinds triage reads (${(triage.inputs || []).join(", ")}).${notReadText}`,
        }),
    not_read: notRead,
    records,
    attribution: [...attributionFor(index, TRIAGE_PROGRAMS), ...(triage.replay ? [triage.replay.attribution] : [])],
  };
}

// --- three_signal ---------------------------------------------------------------------------------------------------

const THREE_SIGNAL_PROGRAMS = ["noisefloor", "cordon", "blackout", "vis", "watchfloor", "wake"]; // wake: UKMTO's reports, join 0.4

export function threeSignal(index, doc, args, now) {
  if (!doc) {
    throw new ToolError(
      "The three-signal join was not built for this snapshot. That says nothing about the area: use search_items " +
        "with near (lat 27.2, lon 56.4, km 900) for each program's own items.",
    );
  }
  const area = args && args.area ? String(args.area).toLowerCase() : null;
  const records = (doc.records || []).filter((r) => !area || r.area === area);
  if (area && !records.length) throw new ToolError(`No area "${area}" in the three-signal join.`);
  return {
    as_of: doc.snapshot_generated_at,
    age_hours: ageHours(doc.snapshot_generated_at, now),
    source: index.bus_url,
    join: doc.join.id,
    what: doc.what,
    note:
      doc.not_a_warning +
      " Report what each signal's items show in their own words (signals.*.items, with ids to cite), which signals " +
      "were present only at their usual rate (signals_at_usual_rate, not counted), the tightest " +
      "span, Vis's weather as context (weather.text; Vis's items are forecasts, not official forecasts) and the base " +
      "rate. context.ukmto lists UKMTO's reports in the circle (via Wake; credit UKMTO, Royal Navy): they are not " +
      "verified and name no one responsible, \"Attack\" is UKMTO's report type and not a finding, and the attacks " +
      "count toward the notice only when context.ukmto.counts is true (above their own rate). " +
      "Do not state what the pattern means. The time windows are provisional. question_draft is a drafted " +
      "question that could score the reading later: it is not a registered Verdict forecast and has no likelihood.",
    readings: doc.readings,
    windows: doc.windows,
    archive_given: doc.archive_given,
    count: records.length,
    records,
    attribution: attributionFor(index, THREE_SIGNAL_PROGRAMS),
  };
}

// --- direction ------------------------------------------------------------------------------------------------------

const DIRECTION_PROGRAMS = ["noisefloor", "cordon", "blackout", "wake", "watchfloor"];

export function direction(index, doc, args, now) {
  if (!doc) {
    throw new ToolError(
      "The direction of concern was not built for this snapshot. That says nothing about any place: use " +
        "search_items for each program's own items.",
    );
  }
  const area = args && args.area ? String(args.area).toLowerCase() : null;
  const strip = ({ press_zone, ...x }) => x; // eslint-disable-line no-unused-vars
  const problems = (doc.problems || []).filter((x) => !area || x.area === area).map(strip);
  const areas = (doc.areas || []).filter((x) => !area || x.area === area).map(strip);
  if (area && !problems.length && !areas.length) throw new ToolError(`No place "${area}" on the direction board.`);
  return {
    as_of: doc.generated_at,
    age_hours: ageHours(doc.generated_at, now),
    source: index.bus_url,
    join: doc.join.id,
    what: doc.what,
    note:
      doc.not_a_warning +
      " Each place's word (Rising, Easing, Mixed, Edging up, Edging down, Steady, Building a baseline) counts how " +
      "many of its layers moved against their own 14 days before; quote it with the layers named in up and down, " +
      "and each layer's own text. A layer marked condition (ship transits) is shown beside the reading and never " +
      "counted, since PortWatch lags by up to 16 days. since is the day the current word began and was the word " +
      "before it. record is Verdict's count of how the registered questions have resolved; it scores forecasts, not " +
      "these words. Cite the item ids a layer gives. Thresholds: https://cop.theintelligence.company/scoring.html",
    windows: doc.windows,
    summary: doc.summary,
    rising: doc.rising,
    easing: doc.easing,
    problems,
    areas,
    record: doc.record ?? null,
    attribution: attributionFor(index, DIRECTION_PROGRAMS),
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

export const EVENT_ORIGINS = ["verdict", "cordon", "orbit"];
const PROGRAM_ORIGINS = new Set(["orbit"]); // from COP's events.json; the rest come from Verdict's calendar

// programEvents: agent/events.json (Orbit's scheduled launches), or null when the build did not write it.
export function upcomingEvents(verdict, programEvents, args, now) {
  const days = Math.min(Math.max(1, (args && args.days) ?? 30), 366);
  const origin = args && args.origin;
  const wantVerdict = !origin || !PROGRAM_ORIGINS.has(origin);
  const wantPrograms = !origin || PROGRAM_ORIGINS.has(origin);
  const calendar = verdict && verdict.calendar;
  if (wantVerdict && !calendar && !(wantPrograms && programEvents)) {
    throw new ToolError("Verdict's calendar could not be fetched for this snapshot and no earlier copy exists.");
  }
  if (wantPrograms && !programEvents && !wantVerdict) {
    throw new ToolError("COP's program events (events.json) were not built for this snapshot.");
  }
  const from = new Date(now).toISOString().slice(0, 10);
  const to = new Date(now + days * DAY).toISOString().slice(0, 10);
  const inWindow = (e) => e.date && e.date >= from && e.date <= to && (!origin || e.origin === origin);
  const nowIso = new Date(now).toISOString();
  const fromVerdict = wantVerdict && calendar ? (calendar.data.events || []).filter(inWindow) : [];
  // A launch whose stated time has passed has gone or slipped; Orbit's next run says which.
  const fromPrograms =
    wantPrograms && programEvents ? (programEvents.events || []).filter((e) => inWindow(e) && !(e.at && e.at < nowIso)) : [];
  const events = [...fromVerdict, ...fromPrograms].sort(
    (a, b) => a.date.localeCompare(b.date) || (a.at || "").localeCompare(b.at || "") || a.id.localeCompare(b.id),
  );
  const orbit = programEvents && programEvents.programs && programEvents.programs.orbit;
  const missing = [];
  if (wantVerdict && !calendar) missing.push("verdict calendar");
  if (wantPrograms && !programEvents) missing.push("program events");
  const attribution = [];
  if (wantVerdict && calendar) attribution.push(verdict.attribution);
  if (fromPrograms.length && orbit && orbit.attribution) attribution.push(orbit.attribution);
  if (!attribution.length) attribution.push(COP_CREDIT);
  return {
    ...(calendar
      ? verdictEnvelope(verdict, "calendar", now)
      : { as_of: programEvents.bus_generated_at, age_hours: ageHours(programEvents.bus_generated_at, now),
          stale: false, source: programEvents.bus_url }),
    ...(programEvents
      ? { orbit: orbit
          ? { as_of: orbit.data_as_of ?? null, age_hours: ageHours(orbit.data_as_of, now), state: orbit.state ?? null }
          : null }
      : {}),
    ...(missing.length ? { missing } : {}),
    window: { from, to },
    note:
      "Dated, resolvable events worth forecasting. verified false means the date comes from secondary reporting. " +
      "origin cordon marks events Cordon found (bulletin expiries, travel advisory changes). origin orbit marks " +
      "scheduled orbital launches from Orbit's feed: the time is the provider's or press's as Launch Library 2 lists " +
      "it, often moves, and is given to the precision it states (net_precision; at is null when only the day is " +
      "known); launch_status is Launch Library 2's (Go for Launch, To Be Confirmed, To Be Determined). A launch whose time has passed is left out. Use " +
      "get_item with an orbit event's id for its sources.",
    count: events.length,
    events,
    attribution,
  };
}
