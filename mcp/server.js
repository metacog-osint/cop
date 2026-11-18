// COP's MCP server: nine read-only tools over COP's merged snapshot (the bus) and Verdict's ledger, served by
// functions/mcp.js at https://cop.theintelligence.company/mcp.
//
// Built on the official MCP TypeScript SDK v2 (@modelcontextprotocol/server). Its createMcpHandler serves
// 2026-07-28 clients and, statelessly, 2025-era clients from the same tool definitions, with no Durable Objects.
// Nothing here calls an AI model: the server only serves public data; the agent's own model does the reasoning.

import { McpServer, createMcpHandler } from "@modelcontextprotocol/server";
import * as z from "zod";
import * as T from "./tools.js";
import { makeData } from "./data.js";

export const VERSION = "0.1.0";
export const SITE = "https://cop.theintelligence.company";

export const INSTRUCTIONS = `COP is The Intelligence Company's common operational picture: the latest findings of its public monitoring programs (Watchfloor and Context for conflict attention and press framing, Vigil for ransomware claims and exploited vulnerabilities, Noisefloor for GPS interference, Elements for natural hazards, Cordon for official notices and travel advisories, Vis for operational weather and night light in Watchfloor's conflict zones, Blackout for internet outages, censorship measurements and cloud and satellite providers' own status reports, Headwind for currencies, energy and food prices against their own normal range and changes to sanctions and screening lists, Orbit for orbital launches, scheduled launches, reentries and satellite catalog changes from GCAT and Launch Library 2, Launch for the FAA's space-operations flight restrictions and the scheduled launches that coincide with launch closure areas, Undertow for covert influence operations that Meta, Google, OpenAI, TikTok and the EEAS have publicly disrupted or named, at operation level, with DISARM technique tags, and, kept apart, reports by independent researchers (DFRLab, CheckFirst, CORRECTIV) and by the G7 Rapid Response Mechanism of operations it tracks and of activity it watches, Wake for vessel transits through 28 maritime chokepoints and port calls at 59 ports from IMF PortWatch, weekly, AIS records of gaps and slow spells near subsea cables and pipelines and of vessels broadcasting an IMO number listed on a sanctions list, and UKMTO's maritime incident reports, Relief for the IFRC's emergency appeals and DREF allocations and the WHO's Disease Outbreak News notices) merged into one snapshot, plus Verdict, its public forecast ledger. Publicly available information only. Site: ${SITE}

How to use these tools well:
1. Cite every claim with the item's id and link, and name the source publisher (get_item gives each item's sources with publisher, licence and grade).
2. Most snapshot items are measurements or reports (kind "observation"). Tier "anomaly" means unusual against that program's own baseline; it is not a warning or a prediction, so never present one as such. Vis's items are forecasts (kind "forecast"): a probability with its ICD 203 likelihood term and a separate confidence, at indicative thresholds; tier "warning" marks Vis's Amber or Red. Quote them exactly, call them forecasts, and never present them as official forecasts or as fit for flight planning. Blackout's items are measurements except one kind of judgment (kind "assessment", tier "warning", with a probability, its ICD 203 likelihood term and a separate confidence): a cable-corridor outage, when drops coincide along a submarine cable corridor. Quote it exactly as Blackout's judgment. Blackout's country outages are observations with no probability (since blackout-1.1): direction "supports" with hypothesis BO-H-1A means OONI measured blocking rising in the outage's own hours, which supports a shutdown; "neutral" means the drop alone doesn't tell a shutdown from a power or physical fault. Say which, in Blackout's words, and never call an outage a shutdown or give it a likelihood. Its provider items are the providers' own status reports, not outside measurements.
3. Verdict forecasts carry a probability and its ICD 203 likelihood term, with confidence kept separate. Quote them exactly as given.
4. Check freshness: every result has as_of and age_hours, and anything marked stale is a last good copy, not current data. An item with data_as_of_precision "day" or "month" has only a date or a month from its source: its data_as_of is midnight UTC on that day or on the 1st of that month, so give the date or the month, never a time, and never convert it to another time zone.
5. us_person_data says whether an item names US organizations or individuals. Apply your own rules for handling US-person information.
6. Each item keeps its own program's licence. Credit the attribution lines each result gives. The picture is public and non-commercial.
7. outage_triage is COP's own reading across layers: for each Blackout outage, whether a new natural hazard (Elements), a fresh travel or embassy notice (Cordon) or unusual press attention (Watchfloor) sits under it in that country around that hour; for each US state power outage (outage_kind "power"), whether a hazard that takes power lines down began in or near that state, where Cordon and Watchfloor answer "Not covered". A reading (natural, tension context, open, unconfirmed or incomplete) sorts public reports; tension context names the notices and attention around an outage, never its cause, and no reading says an outage was deliberate. It is not a warning, not a forecast and not a finding of cause, and it carries no probability, so never present one as such. Always give its base rate (how often an outage would have read that way whatever its cause) and its caveats with it. Each internet-outage record's replay_rate says how often Access Now's documented 2025 shutdowns (#KeepItOn STOP, replayed through triage; credit "Access Now, #KeepItOn STOP") would have read the same way and how often the same countries' ordinary days would have: give both figures, never say triage detects or finds shutdowns, and quote replay.caveats with them. "Nothing found" and "No current data" are different answers: never report a layer with no current data as quiet. Each record's question_draft and cause_question_draft are drafted questions, not registered Verdict forecasts, and have no likelihood.
8. Undertow has two kinds of item, both at operation level. A disclosure is a publisher's own report of an influence operation it disrupted or named (Meta, Google, OpenAI, TikTok, the EEAS): its countries list the operation's origin beside its targets (Google and OpenAI name no target, so theirs list the countries their description names), and only stated targets are marked on the map. The origin is where the publisher says the operators were, never a finding of who directed them, and Undertow's confidence (Low or Moderate, never High) is about origin only. A reported-activity item (it carries an activity block) says that an independent reporter wrote about an operation Undertow tracks: give the reporter, its grade (B2 or B3, below a platform's own report), the first and last report, and the state. "active" means the reporter's last report is within 180 days and "quiet" means none since; quiet never means the operation ended, and a report says the reporter wrote about the operation, not that it found the operation running. Its countries are the countries named in the reports, which can include where the operators are said to be; only those that a platform's or the EEAS's disclosure of the operation records as targets are marked on the map. They are not an origin, and the item adds no origin and no confidence unless its own summary says a checked register entry does. A watch-register item (id UT-WATCH-...) is a report entered by hand after review: a reporter's description of activity, which may name tracked operations (its named_operations) or none, marked at the targets entered from the report; no platform disclosure has been matched to it unless its activity block's closed_into names one, and its activity_period, when given, is when the activity ran, not the report date. A reporter's "state-aligned" or "Kremlin-aligned" is recorded as alignment, never as an origin. Each item's disarm_rules_status says whether its DISARM technique mapping has been reviewed; while it reads "proposed", call the techniques provisional.
9. Wake has four kinds of item; the second part of its id names the kind. Chokepoint and port items (wake:chokepoint:..., wake:port:...) are IMF PortWatch's counts of AIS-visible transits or port calls, as 7-day means that Wake computes and compares with the 90 days before and the same weeks a year earlier; tier "anomaly" means the week is unusual against those baselines, not a warning or a finding of cause. PortWatch publishes weekly, so the newest day is usually 2 to 16 days old: give the week the item covers. A ship with its transponder off is not counted, and a port's tonnage is the IMF's estimate from draughts, not customs figures. AIS gap and slow items (wake:ais-gap:..., wake:ais-slow:...) are what one vessel's AIS record shows near a subsea cable or pipeline (Digitraffic, the Baltic): a gap is given between Wake's lower and upper bounds, and a receiver outage, a transponder fault and a vessel switching off look the same, so never say the vessel went dark on purpose or caused damage; a slow spell is not a reason. Listed items (wake:ais-listed:...) say a vessel broadcasts an IMO number listed on a named sanctions list: always say "broadcasts an IMO number listed on", never "sanctioned vessel", because an IMO number in AIS is a claim, not an identity, and the item says nothing about owner, operator or purpose; an item with no geometry had a position that failed Wake's checks. UKMTO items (wake:ukmto:...) are the Royal Navy's UKMTO reports as published (number, type, alert level, place, incident time), always routine, not verified by Wake, and they name no one responsible: give UKMTO as the source, its report type in UKMTO's words, and its own page (sources[0].url) for the report itself.
10. three_signal is COP's own reading of the Strait of Hormuz and the Gulf, four signals since join 0.3: whether GPS interference (Noisefloor), a new notice (Cordon: a navigation warning naming military activity, a new airspace bulletin or a revision that is not a scheduled renewal, or a US embassy security alert), a confirmed internet outage (Blackout) and unusual press reporting of violence in an area country (Watchfloor) fall in the area within 48 hours of each other, with Vis's weather forecast beside them as context. A signal that fires on most archived days counts only when it rises above its own rate there (signals_at_usual_rate lists those that did not). A reading (converged, partial, none or incomplete) says which signals are present and how close together they fall. It is not a warning, a forecast or a finding of cause, and it carries no probability: report what each layer shows and the base rate (how often each signal fires anyway in the archive), and do not state what the pattern means. question_draft is a drafted question, not a registered Verdict forecast.
11. Relief's items are the publishers' own records: an IFRC emergency appeal or DREF allocation (hazard, amounts in Swiss francs, people targeted, dates) or a WHO Disease Outbreak News notice (title, date, country, link). Tier is always "routine": Relief makes no anomaly, warning or forecast, so never present an item as one. An IFRC appeal shows where the Red Cross and Red Crescent asked for money, not every place in need; crises led by others may have no appeal. place_context's Poisson figure (tail_probability) is a measurement of how often the country's appeal starts in the last 90 days have occurred against its own ten-year rate, not a warning or a prediction. A WHO notice usually comes weeks after the first report. Never quote WHO notice text: give its title, date, country and link only.
Start with suite_status to see what each program covers and how fresh it is, then search_items or top_picks.`;

const READ_ONLY = { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false };
const HOUR_MS = 3600 * 1000;

function ok(obj) {
  return { content: [{ type: "text", text: JSON.stringify(obj) }], structuredContent: obj };
}

function refuse(message) {
  return { isError: true, content: [{ type: "text", text: message }] };
}

const NoInput = z.object({});

const SearchInput = z.object({
  program: z
    .array(z.string())
    .max(12)
    .optional()
    .describe("Program ids to include, e.g. [\"cordon\", \"elements\"] (suite_status lists them). Default: all."),
  country: z
    .string()
    .regex(/^[A-Za-z]{2}$/)
    .optional()
    .describe("ISO 3166-1 alpha-2 country code, e.g. \"SD\" for Sudan."),
  state: z
    .string()
    .regex(/^[A-Za-z]{2}-[A-Za-z0-9]{1,3}$/)
    .optional()
    .describe("ISO 3166-2 state code, e.g. \"US-ND\" for North Dakota. Matches items their program placed in that " +
      "state (so far Cordon's FAA items and Elements' US hazards); an item for the whole country does not match."),
  kind: z.enum(T.KINDS).optional().describe("observation, assessment or forecast."),
  tier: z.enum(T.TIERS).optional().describe("routine or anomaly (measured), warning (judged)."),
  text: z.string().min(2).max(100).optional().describe("Words to find in the item's title or id (case-insensitive)."),
  since: z.string().optional().describe("ISO 8601 UTC time; only items whose data is this new or newer."),
  near: z
    .object({
      lat: z.number().min(-90).max(90),
      lon: z.number().min(-180).max(180),
      km: z.number().positive().max(5000),
    })
    .optional()
    .describe("Only items within km of this point (items with an area count when the point is inside or near it)."),
  limit: z.number().int().min(1).max(T.LIMIT_MAX).optional().describe(`Items to return, default ${T.LIMIT_DEFAULT}.`),
  offset: z
    .number()
    .int()
    .min(0)
    .max(100000)
    .optional()
    .describe("Skip this many matches, for the next page: pass the next_offset a previous result gave."),
});

const GetItemInput = z.object({ id: z.string().min(1).max(200).describe("An item id from search_items or top_picks.") });

const TriageInput = z.object({
  country: z
    .string()
    .regex(/^[A-Za-z]{2}$/)
    .optional()
    .describe("ISO 3166-1 alpha-2 country code. Default: every outage in the snapshot."),
});

const ThreeSignalInput = z.object({
  area: z.string().regex(/^[a-z0-9-]{1,40}$/).optional().describe("An area key (hormuz). Default: every area."),
});

const ForecastInput = z.object({
  status: z.enum(T.FORECAST_STATUSES).optional().describe("open, resolved or all (default all)."),
});

const EventsInput = z.object({
  days: z.number().int().min(1).max(366).optional().describe("How many days ahead to look, default 30."),
  origin: z.enum(T.EVENT_ORIGINS).optional().describe("Only events found by this program."),
});

// data: { index(), programs(), picks(), triage(), threeSignal(), verdict(), events(), shard(n) }, each a promise of the
// parsed file (see data.js; triage(), threeSignal() and events() resolve to null when the file is absent).
export function buildServer(data, clock = Date.now) {
  const server = new McpServer(
    { name: "cop", title: "COP, The Intelligence Company", version: VERSION, websiteUrl: SITE },
    {
      instructions: INSTRUCTIONS,
      capabilities: { tools: {} },
      cacheHints: {
        "tools/list": { ttlMs: HOUR_MS, cacheScope: "public" },
        "server/discover": { ttlMs: HOUR_MS, cacheScope: "public" },
      },
    },
  );

  const tool = (name, title, description, inputSchema, run) =>
    server.registerTool(
      name,
      { title, description, inputSchema, annotations: { title, ...READ_ONLY } },
      async (args) => {
        try {
          return ok(await run(args || {}, clock()));
        } catch (e) {
          if (e instanceof T.ToolError) return refuse(e.message);
          throw e;
        }
      },
    );

  // Registered in a fixed order (the spec asks for a deterministic tools/list).
  tool(
    "suite_status",
    "Suite status",
    "Every program in The Intelligence Company's suite (live and planned): what it watches, its site and feed, its " +
      "licence, and for live programs how fresh its data is in COP's snapshot. Start here.",
    NoInput,
    async (_a, now) => T.suiteStatus(await data.programs(), now),
  );
  tool(
    "search_items",
    "Search the picture",
    "Find items in COP's current snapshot by program, country, kind, tier, words in the title, time or distance from " +
      "a point. Returns compact rows (id, program, title, times, countries, point, link, US-person label), warnings " +
      "first, then anomalies, then routine items. Use get_item for an item's sources and measurements.",
    SearchInput,
    async (a, now) => T.searchItems(await data.index(), a, now),
  );
  tool(
    "get_item",
    "Get one item",
    "One item in full (feed contract tic-feed/2): every source with publisher, licence and grade, its measurements, " +
      "baseline, place and links.",
    GetItemInput,
    async (a, now) => {
      const index = await data.index();
      const row = T.findRow(index, a.id);
      return T.getItem(index, row ? await data.shard(row.shard) : null, a.id, now);
    },
  );
  tool(
    "top_picks",
    "Today's picks",
    "COP's picks for today: each program's strongest measured anomaly that has a place, at most five, with the " +
      "reason each was picked. Watchfloor and Context both read GDELT and count as one voice: one pick per zone, " +
      "carrying both programs' readings.",
    NoInput,
    async (_a, now) => T.topPicks(await data.index(), await data.picks(), now),
  );
  tool(
    "outage_triage",
    "Outage triage",
    "COP's reading of each internet outage Blackout reports: what natural hazards (Elements), travel and embassy " +
      "notices (Cordon) and press attention (Watchfloor) show for that country around that hour, including " +
      "\"nothing found\" and \"no current data\", with the base rate and caveats; and of each US state where ODIN's " +
      "reporting utilities show more than 50,000 meters without power, against hazards in or near that state. A " +
      "reading is not a warning, a forecast or a finding of cause.",
    TriageInput,
    async (a, now) => T.outageTriage(await data.index(), await data.triage(), a, now),
  );
  tool(
    "list_forecasts",
    "Verdict forecasts",
    "Verdict's public forecasts: each question, its probability and ICD 203 likelihood term, confidence, the rule " +
      "that settles it, when the answer becomes knowable, and the outcome once resolved.",
    ForecastInput,
    async (a, now) => T.listForecasts(await data.verdict(), a, now),
  );
  tool(
    "forecast_record",
    "Verdict track record",
    "Verdict's scores: how many forecasts are registered and resolved, and the Brier score with its decomposition " +
      "once any have resolved.",
    NoInput,
    async (_a, now) => T.forecastRecord(await data.verdict(), now),
  );
  tool(
    "upcoming_events",
    "Upcoming events",
    "Dated, resolvable events in the next days (data releases, rule dates, bulletin expiries, travel advisory " +
      "changes, scheduled orbital launches), with the forecasts that name them.",
    EventsInput,
    async (a, now) => T.upcomingEvents(await data.verdict().catch(() => null), await data.events(), a, now),
  );
  tool(
    "three_signal",
    "Three-signal join",
    "COP's reading of the Strait of Hormuz and the Gulf: whether GPS interference (Noisefloor), a new notice " +
      "(Cordon), a confirmed internet outage (Blackout) and unusual press reporting of violence (Watchfloor) fall in " +
      "the area within 48 hours of each other, each against its own rate there, with each layer's items, Vis's " +
      "weather forecast as context and how often each signal fires anyway. A reading is not a warning, a forecast " +
      "or a finding of cause.",
    ThreeSignalInput,
    async (a, now) => T.threeSignal(await data.index(), await data.threeSignal(), a, now),
  );
  return server;
}

export function makeHandler(data, clock = Date.now) {
  return createMcpHandler(() => buildServer(data, clock));
}

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
  "Access-Control-Allow-Headers": "*",
  "Access-Control-Expose-Headers": "*",
  "Access-Control-Max-Age": "86400",
};

const HELP = `This is COP's MCP server, for AI agents (Model Context Protocol, Streamable HTTP; protocol 2026-07-28 and 2025-era clients).

Add it to an MCP client with this address: ${SITE}/mcp
Claude Code: claude mcp add --transport http cop ${SITE}/mcp

Eight read-only tools over COP's current snapshot and Verdict's forecast ledger. Public information only; no sign-in.
The map itself is at ${SITE}/
`;

function withCors(res) {
  const out = new Response(res.body, res);
  for (const [k, v] of Object.entries(CORS)) out.headers.set(k, v);
  out.headers.set("X-Robots-Tag", "noindex");
  return out;
}

// One handler per isolate; `env.ASSETS` is the same for every request an isolate serves.
let shared = null;

export async function handle(request, env, clock = Date.now) {
  if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: CORS });
  const accept = request.headers.get("accept") || "";
  if (request.method === "GET" && accept.includes("text/html")) {
    return withCors(new Response(HELP, { headers: { "Content-Type": "text/plain; charset=utf-8" } }));
  }
  if (!shared) {
    shared = makeHandler(makeData((path) => env.ASSETS.fetch(new URL(path, request.url))), clock);
  }
  return withCors(await shared.fetch(request));
}
