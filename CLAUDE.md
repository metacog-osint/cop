# COP: project notes for Claude

COP, the common operational picture of The Intelligence Company: every program's latest findings on one map.
Roadmap step 2 (`[local path]`); built 31 Oct 2026 from the plan at
`[local path]`. Design spec: the COP section of
`[local path] DoD sources and COP visuals.md`.

## Tom's decisions (31 Oct 2026)
- **Name:** COP (plain-language alternatives were considered and declined).
- **Address:** `cop.theintelligence.company` (Cloudflare Pages project `cop`, default address `cop-4gw.pages.dev`
  because cop.pages.dev was taken). Squarespace DNS: `CNAME cop → cop-4gw.pages.dev` (added 31 Oct 2026). Repo
  secrets `CLOUDFLARE_PAGES_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` set 31 Oct; the build deploys every two hours.
  **The two-hour clock** (6 Nov 2026): GitHub fired the `17 */2` schedule only every 7 to 14 hours, so the hub's
  `tic-dispatch` Worker starts `build.yml` at :20 past every even UTC hour (hub `dispatch/dispatch.js`). The cron
  stays as a fallback; the `gate` job skips a scheduled run when a build succeeded in the last 45 minutes.
- **Public and non-commercial for now** (Tom, 31 Oct 2026, later the same day): COP is open to everyone and to search
  engines, like the other programs. If Tom ever markets it, the sources a paid version can't use get scrubbed first,
  thoroughly and ethically. `config/sources.json` keeps `commercial_ok` for every source (true, false or unknown, with
  its basis) so that scrub starts from a list; `snapshot.py` lists any feed source missing from the register.
- **Site public, repo private** (Tom, 31 Oct 2026). Opening the code is a one-way door, staying private costs little
  (about 600 of the free 2,000 Actions minutes a month), and the cross-program integration is the part a paid version
  would be. Never copy COP code into public repos. If it opens later: AGPL-3.0 like Noisefloor and Vigil.
- **Review before v1:** the convergence layer (several programs agreeing over one place) is where aggregation starts
  saying more than any single public source (the mosaic effect); review those flags before v1 ships.
  **The gate (12 Nov 2026, `tools/gate.py`):** every join writes through it. A join is "open" only if every layer it
  declares and every item it cites is on the gate's open list; Noisefloor, Vis and Cordon's navwarn and czib kinds are
  military context, and any layer or kind not listed is held too. Held joins write only under `out/held/` and have no
  switch that publishes them. `python tools/gate.py --check` runs before the deploy. A new program or item kind must be
  added to one of the two lists there (a test fails until it is). Tom confirmed both lists on 12 Nov 2026.
  Cordon's `fcdo` (UK FCDO travel advice), `canada` (Global Affairs Canada) and `cdc` (CDC travel health notices)
  kinds went on the open list on 14 Nov 2026 (Tom's "Yes, as proposed": notices of the same nature as State's).
  Cordon's `tfr` and `faa` kinds (FAA flight restrictions, airport programs) are on neither list, so they are held.
  **Releasing a held join (Tom, 12 Nov 2026):** only Tom releases one, by hand, outside the build, and each release
  gets a dated line in `[local path]` saying what was released and to whom. Claude never copies a
  file out of `out/held/`, and no code path may be added that does.
- **Outage triage (`tools/triage.py`, `tools/triage_rates.py`; on the page and in the MCP server since step 5,
  13 Nov 2026; merged to `main` and deployed on Tom's yes, `df1e431`, 05:34 UTC 13 Nov 2026):** `JOIN["published"]` is on. The build
  downloads this month's and last month's archive copies to `out/rates-archive` (never `out/archive`, which it
  uploads from), runs `triage_rates.py` (about 3 seconds) and then `triage.py --publish site/data`, which writes
  `site/data/triage.json` through the gate. A failure there never fails the build: without base rates the readings
  say so, and without the file the page and the server say triage is unavailable. The public document
  (`triage.public()`) has each record's reading, one `showed` line per layer ("Nothing found" and "No current
  data." are different answers), the base rate, the caveats and, since Tom's yes of 13 Nov 2026, the drafted
  Verdict question as `question_draft` (status "draft, not registered", no probability or approval field; the page
  folds it under "Question to score it by"). The working draft with its empty probability stays in `out/triage/`. The wording that a reading is not a warning, a forecast or a finding of cause
  lives in `triage.py` (`NOT_A_WARNING`) and the page and the server print it from the document. Page: the
  "Outage triage" section of the panel (`renderTriage` in `cop.js`), shown with Blackout's preset and "all".
  MCP: the `outage_triage` tool (optional `country`). Rules approved by
  Tom on 12 Nov 2026: a hazard counts only if it began in the 72 hours before the outage; a notice counts only if
  dated in the 48 hours before it and not lowered or re-issued at the same level; raised attention counts only where
  the country had it on under 25% of archived days. The windows are provisional. Rerun `triage_rates.py` about 28 Nov
  2026, when the archive holds 28 days, and look at the windows then. Watchfloor lags a day, so run triage a second
  time the day after an outage. The build now reruns triage every two hours, so the second look happens by itself
  while the outage is still in Blackout's feed.
  **UK and Canadian travel advice (join 0.7; Tom, 14 Nov 2026, "Yes, as proposed"):** triage reads Cordon's `fcdo`
  and `canada` items as notices (`NOTICE_KINDS`, declared inputs `cordon:fcdo`, `cordon:canada`), but only as a
  change: an item counts only when Cordon marks it anomaly (it saw the level or the advice change;
  `CHANGE_ONLY_KINDS`). Any other update is an edit, direction "unchanged", listed and not counted. Both sources
  re-date a country for every edit: on the 14 Nov snapshot 16 of the 17 items in the 48-hour window were edits
  (contacts, natural-disaster sections, air-traffic notes). Cordon's anomaly does not say up or down, so the level is
  still compared with the archive (`notice_level`: Canada `[level, regional 1/0]`, so a regional advisory removed at
  the same level is a lowering, as Mexico's of 12 Nov was; FCDO one flag per "advise against" statement, strongest
  first, so adding one raises it). `triage_rates.py` keeps those ranks in `advisory_levels` beside State's; the
  archive has them only from 14 Nov, so until it holds an earlier copy a change counts as "unknown", with the
  caveat. CDC's notices are open at the gate but not read: a disease notice says nothing about a cut network, and
  one update to CDC's global measles notice (235 countries) would put a fresh notice in every country.
  Measured 14 Nov on the archive (31 Oct-14 Nov, 1,673 counted country-days): fresh notice 1.91%, readings natural
  0.84%, deliberate 4.72%, open 94.44%, the same before and after; 14 Nov alone, 6 of 239 countries with a fresh
  notice and 12 deliberate, both before and after (Canada's Mexico change, the only one, sits where a State notice
  already was). Counting every update instead would have given 21 and 26 that day (2.81% and 5.56% overall). No
  archived outage changes reading. The base rates name `canada` and `fcdo` as added after 8 Nov
  (`notice_kinds_in_archive_from`). Live, 21:21 UTC 14 Nov: DR Congo's outage lists FCDO and Canada as standing
  beside State's advisory, reading unchanged, gate open.
  **US state power outages (join 0.6; seven rules approved by Tom on 13 Nov 2026, "Yes, build all seven"):** each
  Blackout BO-IND-14 item (a state over 50,000 meters out at ODIN's reporting utilities; id
  `blackout:us-power:<st>:<day>`) gets a record with `outage_kind` "power" and `us_state`. (1) Place: NWS alerts by
  their own county codes; a point Elements puts in the US is in the state whose county internal point is nearest,
  and any point within 100 km of a state's county points is near it (`config/us_counties.json`, Blackout's copy of
  the Census 2025 Gazetteer; Great Sitkin sits 292 km from its census area's point, hence the nearest rule); US
  items with no county or point are "unplaced", listed only if significant, never counted; SPC risk is background,
  over the outage's own point only. (2) Size: NWS warnings for High Wind, Extreme Wind, Winter Storm, Ice Storm,
  Blizzard, Lake Effect Snow, Hurricane, Tropical Storm, Typhoon, Storm Surge, Tornado, Severe Thunderstorm,
  Extreme Cold, Extreme Heat, Red Flag and Dust Storm count at Elements' Severe level (`POWER_HAZARDS`), plus
  anything Elements rates anomaly; floods, freezes and marine warnings are minor. (3) An NWS alert begins at its
  onset, not its latest update (all NWS items). (4) A volcano alert counts only when its level was raised, from the
  archive's `volcano_levels` (every country; a re-send at the same or a lower level is standing; no earlier level
  counts, marked unknown). (5) Readings natural, open or incomplete (`POWER_READINGS`); Cordon and Watchfloor answer
  "Not covered"; only Elements going dark makes it incomplete. (6) The drafted question: still over 50,000, 24 hours
  after ODIN's reading (rule window +24 h to +26 h, `eaglei_rule`, the exact text Verdict's `EAGLEI_RE` settles;
  settles in early 2027, leans Yes). (7) Two base rates: the state's EAGLE-I rate as Blackout attaches it
  (`eaglei`) and a state-day rate from the archive (`triage_rates.py` `state_background`: 3.6% natural over 728
  state-days, 31 Oct to 13 Nov, against 1.1% on Elements' own tier alone). Measured before the change: the US read
  natural at country level on 13 of 14 archived days, from daily volcano re-sends and Alaska's blizzards; Cordon
  and Watchfloor named the US on 0 of 6,154 item-days. Fixtures: `tests/fixtures/us_power` (MANIFEST.json; made by
  `tests/make_us_power_fixtures.py` from Blackout's code on its recorded ODIN responses and COP's archive).
  Limits: the archive is one snapshot a day, so hour-long warnings are missed in the rate; Elements carries active
  alerts only, so "open" can mean the storm has passed. Follow-ups outside COP: Elements keeping expired NWS warnings
  24 hours, and Blackout putting the outage counties' FIPS codes in the item for a county-level match.
- Open to search engines since 31 Oct 2026 (no noindex; canonical `https://cop.theintelligence.company/`).

## How it works
- `tools/snapshot.py` (standard library) fetches the contract-v2 feeds of the programs in `config/programs.json` (Watchfloor,
  Context, Vigil, Noisefloor, from 31 Oct Elements and Cordon on GitHub Pages, from 8 Nov 2026 Vis, from 12 Nov 2026 Blackout, from 13 Nov 2026 Headwind and Orbit; PropertyCast is not a COP layer).
  **Since kit 0.4.0 (1 Nov 2026)** each program's name, family, feed, site and freshness windows come from the kit's
  suite registry (`doctrine/kit/vocab/programs.json`, edited in `[local path]`); `config/programs.json`
  keeps only how COP draws each program. A new program goes into the registry first, then gets drawing rules here.
  COP's `site/data/today.geojson` is also the suite's bus: other programs read it for tips (kit `tips.py`).
  Per program: `country_mark` (fill shades the country, halo rings its label point), `area_style` (dashed for legal
  areas), `freshness` ("feed" uses the feed's generated_at, for notice programs whose items carry issue dates) and
  `routine_in_preset` (show routine items when that program's preset is chosen, for standing notices). It lints each with the vendored kit, merges them into
  `site/data/today.geojson` (itself contract v2, each item tagged `cop_program`), writes `picks.json`, `health.json`,
  `countries-today.geojson` and `world.geojson`. A failed feed keeps its last good items from the published snapshot,
  marked stale (grey); it never fails the build.
- **Picks:** anomalies with a place only; each program's own strongest first by its `strength` rules (rule order first,
  then value), one program at a time, at most `max_picks` (5). Strength is never compared across programs. Picks are
  measured anomalies only, so they are labelled measurements, never warnings.
- **Forecasts: Vis** (8 Nov 2026, Tom chose "option A"). Vis's items are `kind: "forecast"` (31 points a run, at
  airfields and ports in Watchfloor's zones) plus one summary observation without a place. They are never picks and
  have no `strength` rules. Its Amber or Red forecasts carry tier `warning` and show in every view (`STANDS_OUT` in
  `cop.js`; no other program sends warnings yet); the rest show with routine items or under its own "Weather" preset
  (`routine_in_preset`, and `routine_label` names them in the panel: "Vis's forecast for every point"). The card
  says "Forecast: a probability, not a measurement" (plus "Warning: Vis's judgment" at tier warning) and shows the
  kit's likelihood and confidence chips. Colour: Okabe-Ito yellow `#F0E442`, the last unused hue, outlines `#5C5500`
  and `#FFF8B8`. The MCP instructions say Vis's items are forecasts; `search_items` lists warnings first, so Vis's
  Amber and Red lead an unfiltered search. Its three sources are in `config/sources.json` (Open-Meteo:
  `commercial_ok` false, free API is non-commercial only).
- **Blackout** (12 Nov 2026, kit 0.4.3; kit 0.4.4 on 13 Nov 2026 brought its registry entry up to date with
  Cloudflare Radar and eleven status feeds, and put Radar's CC BY-NC 4.0 in its `terms`): internet outages (IODA,
  by country and on five watched ASNs; Cloudflare Radar's annotated outages and verified anomalies), OONI
  censorship rates and cloud, software and satellite providers' own status reports. Outage and OONI items are country-level
  points, so `country_mark` "fill" shades the country; provider items have `geometry: null` and list under Off the
  map. Its items carry no `measures`, so it has no `strength` rules and its pick is its feed's first placed anomaly
  (Blackout lists outages by drop, largest first, then OONI by anomaly rate); the pick's reason carries no value.
  `freshness` "feed": its items keep the event's or the OONI day's time, not the run's. Own preset "Internet
  connectivity". Colour: Paul Tol's indigo `#332288` (outlines `#1E1452`, `#CBC3F5`), because the seven non-black
  Okabe-Ito hues are taken and black at country-fill opacity looks like the stale grey. Its fifteen sources are in
  `config/sources.json`: OONI `commercial_ok` false (its data licence is CC BY-NC-SA 4.0, github.com/ooni/license
  `data/LICENSE.md`, read 12 Nov 2026), IODA and the providers "unknown" (no licence stated).
  Added 13 Nov 2026 with Blackout's new sources: Cloudflare Radar `commercial_ok` false (CC BY-NC 4.0); the
  Microsoft 365, Cloudflare, Google Workspace, Apple, Akamai, Slack and Zoom status feeds "unknown" (Apple's
  site terms bar republishing its content without consent; Blackout carries only facts and a link). Blackout's own feed
  now states those licences itself (corrected 12 Nov 2026), and COP shows that text as Blackout sends it.
  `tests/fixtures/blackout.geojson`: Blackout's live feed of 23:11 UTC 12 Nov, every date moved back 12 days 4 hours.
  Blackout also publishes judgments (kind `assessment`, tier `warning`): a likely shutdown only with OONI-confirmed
  blocking in the same country (BO-H-1A), and a cable-corridor outage (BO-H-2A). They show in every view like Vis's
  warnings; the card says "Judgment: a probability, not a measurement" and "Warning: Blackout's judgment", and the MCP
  instructions say the same. None was live on 12 Nov, so the fixture has none.
- **Headwind** (13 Nov 2026, kit 0.4.6, which sets its registry entry live with its feed's own licence line):
  currencies, energy and food prices against their own range, sanctions and screening-list changes, and BIS and
  OFAC rules. Currency, local-food and country-tagged rule items are country-level points, drawn as rings at the
  country's label point (`country_mark` "halo"; a routine reading exists for about forty countries at once, so
  fills would shade half the map). Energy, FAO index and list-change items have `geometry: null` and list under
  Off the map. Nearly every item is tier `routine`, so `routine_in_preset` shows them under its own preset,
  "Economic stress" (`routine_label` "reading for every series"). Its `measures.z` is signed and `strength`
  compares raw values, so it has no `strength` rules; its pick would be its feed's first placed anomaly. Give
  `strength` an absolute-value option before ranking its anomalies by size. `freshness` "feed": its items carry
  market dates and the ECB publishes on business days only. Colour: Paul Tol's wine `#882255` (outlines
  `#4A0F2C`, `#F2BFD6`). Eight sources in `config/sources.json`, licences as Headwind's notes record them: the UN
  list `commercial_ok` false; FAO, WFP, OFAC and trade.gov "unknown"; ECB, EIA and the Federal Register true.
  On the gate's open list (`tools/gate.py`; confirmed by Tom, 14 Nov 2026); no join reads it yet.
  `tests/fixtures/headwind.geojson`: its live feed of 16:28 UTC 13 Nov, every timestamp moved back 12 days 21 hours.
- **Orbit** (13 Nov 2026, kit 0.4.8, which sets its registry entry live): orbital launches of the last 7 days at
  the launch site (GCAT site coordinates, precision `approximate`), scheduled launches at the pad (Launch Library
  2), one reentry summary and catalog-change items (new entries, owner changes), the last two with `geometry:
  null`, listed under Off the map. Describe it from what is live: no manoeuvres or counterspace events yet.
  Every item is kind `observation`, tier `routine`, `us_person_data: organizations`, so it has no `strength` rules
  and makes no picks; `routine_in_preset` shows its items under "Spectrum and space", the preset it shares with
  Noisefloor (its registry family), and `routine_label` names them. `freshness` "feed": a launch item keeps the
  launch time and a scheduled launch keeps Launch Library 2's last update, which can be months old. Registry
  windows 24 h fresh and 72 h stale, as Headwind's: it runs every 6 h at :41 UTC and GitHub often fires late.
  Colour: Paul Tol's olive `#999933` (outlines `#4D4D19`, `#E6E6B3`); his remaining hues sit too close to
  Okabe-Ito hues already in use. Two sources in `config/sources.json`, licences as Orbit's
  `doctrine/sources.json` records them: GCAT (CC BY 4.0, cited as "Data from GCAT (J. McDowell,
  planet4589.org/space/gcat)") `commercial_ok` true; Launch Library 2 (no data licence stated; credited, images
  not used) "unknown". **On the gate's open list** (`tools/gate.py`; Tom, 13 Nov 2026, after it was first held
  because its launches include national-security payloads at named sites and times, all publicly announced).
  No join reads it yet. `tests/fixtures/orbit.geojson`: its live feed of 22:03 UTC 13 Nov (21 items: 6 launches, 14
  scheduled, 1 reentry summary, no catalog changes yet), every timestamp moved back 13 days 2 hours, and from 14 Nov
  the "YYYY-MM-DD HH:MM UTC" times in titles and summaries too, so scheduled launches sit after the fixture's time.
  Its scheduled launches also reach the MCP tool `upcoming_events` (14 Nov 2026; see the MCP server section).
- **Launch** (14 Nov 2026, kit 0.4.9, which adds its registry entry live): FAA space-operations TFRs (polygons) and
  scheduled launches that coincide with a launch closure area in time and place, or that GCAT has since dated inside
  its window (the area's polygon). Time-only coincidences stay on Launch's own page. Every item is kind
  `observation`, tier `routine`, precision `area`, `us_person_data: organizations`; no `strength` rules;
  `routine_in_preset` under "Spectrum and space" beside Orbit and Noisefloor. `mark` "areas", `area_style`
  "dashed" (notices, as Cordon's), `country_mark` "halo", `freshness` "feed". Colour: Paul Tol's purple `#AA4499`
  (outlines `#55224C`, `#DDB4D6`); Orbit's note above found Tol's remaining hues close to the Okabe-Ito ones in use
  (this one to Context's `#CC79A7`), so the dashed outline is what sets Launch apart: not run through the colour
  validator yet. Sources `faa-tfr` (US Government work, `commercial_ok` true) and `orbit-snapshot` ("unknown"), as
  Launch's `doctrine/sources.json` records them. **Held on the gate's MILITARY list** (confirmed by Tom, 14 Nov 2026;
  Orbit was held first and later opened): closure areas and the launches they match include national-security launches and missile
  firings. No join reads it. `tests/fixtures/launch.geojson`: its live feed of 14:14 UTC 14 Nov (3 TFRs), every
  timestamp moved back 13 days 18 h 9 min. A local live build 14 Nov read it fresh, 3 items, no lint errors.
- **Items shown in a second preset (`also_in`, 14 Nov 2026, Tom's "Yes, add the filter"):** Cordon's NGA
  warnings that name rocket launching, space debris, a space vehicle or spacecraft, or reentry (kind `navwarn`,
  title or summary, Orbit's `navwarn` pattern) also show under "Spectrum and space", in Cordon's blue dashed style,
  whether or not the routine switch is on. `snapshot.py` tags them `cop_also: ",spectrum,"` (a string, because
  MapLibre's `in` tests strings; it must stay in `MAP_KEYS` in `cop.js`, or the map's copy loses it and nothing
  draws); `baseFilter()` adds them in that preset and the panel names them. Why not Orbit's feed: Cordon already
  carries the same NGA warnings (19 of 19 on 14 Nov), so Orbit items would draw each area twice. TFRs are not
  tagged: Launch draws the space-operations ones in that preset (Cordon's copies show under Everything and
  Notices, so those seven TFRs appear twice in Everything). The gate is unchanged: no join reads `cop_also`, and
  Cordon's `navwarn` kind stays on the MILITARY list. Checked 14 Nov on a local live build: 19 tagged, drawn
  and clickable under Spectrum and space, no console errors.
- **Page:** static `site/` (no build step). `cop.js` is an ES module using vendored MapLibre GL JS 6.14.0
  (`site/vendor/maplibre-gl/`, BSD-3; pinned to a release at least two weeks old, integrity checked against npm) and
  the kit's `tic-doctrine.js`. Base map: Natural Earth land from `data/world.geojson` below zoom 3.5 (OpenFreeMap's
  world tiles are ~1.5 MB each), OpenFreeMap positron or dark above it; if OpenFreeMap fails, the land carries every
  zoom and a note says so. Times through the hub's `shared/tic-time.js` (copy, keep the version line).
- **Geometry types on the map** (13 Nov 2026, for Cordon's NGA warnings drawn in the shape their words give):
  Point/MultiPoint (`points`, `halos`), Polygon/MultiPolygon (`cells-*`, `areas-line`) and, since this change,
  LineString/MultiLineString: `lines-casing` (outline colour, for contrast) under `lines` (the program's fill
  colour; a claim keeps only the fill colour), always solid whatever `area_style` says, plus an invisible 14 px
  `lines-hit` layer that takes clicks and hover. All three use `baseFilter()`. A GeometryCollection (a warning
  with both an area and a trackline) is split into one map feature per part at load (`parts` in `cop.js`), each
  carrying the item's id, because MapLibre filters by the part's type. Shapes that cross 180 degrees carry
  longitudes past +-180 (Cordon keeps them continuous); MapLibre draws them as given. `placeOf` (and `card.py`
  `place_of`, same rule) puts a non-point item at the middle of the box around its positions; `three_signal.py`
  `_points` reads every vertex of a MultiLineString and every part of a collection. Checked 13 Nov 2026 on a test
  snapshot built from Cordon's then-uncommitted shapes (24 lines and one real collection, HYDROLANT 682/2024, plus
  a made-up collection across 180 degrees): drawn, clickable, hidden by the presets that hide Cordon's routine items.
- **CSP** in `site/_headers` (Cloudflare Pages format): self only, plus `tiles.openfreemap.org` for connect and
  images; `worker-src 'self' blob:` (MapLibre's module worker). No inline styles or scripts; colours come from config
  through MapLibre paint or the CSSOM.
- **Share card (v0.1, 31 Oct 2026):** `tools/card.py` draws today's picks as a 1200x627 page (map in Web Mercator
  fitted to the picks, list on the right, fonts embedded, time in UTC with the zone named) and screenshots it with
  headless Chrome (Edge locally) into `site/data/card.png`, the page's og:image. `--stamp-page` (build only) adds
  `?v=<assessment time>` to the image address, since social sites cache previews by address. If no browser runs or
  the size is wrong, it copies `site/card-fallback.png` (regenerate with `--generic`); it never fails the build.
- **Kit:** vendored in `doctrine/kit/` with `python [local path] [local path]`, then copy
  `doctrine/kit/js/tic-doctrine.js` to `site/vendor/` (a test checks they match).

## MCP server (`/mcp`, built 6 Nov 2026)
`https://cop.theintelligence.company/mcp` lets AI agents (Claude, ChatGPT, Gemini, Claude Code) query the picture:
read-only, public, no sign-in. Tom chose the address (6 Nov 2026) over a separate project or a workers.dev address.
Plan: `[local path]`; the reason for building it is gap 1 of
`[local path] projects and gaps.md`. The server only serves data; no AI model is called.
- **Data:** `tools/agent_index.py` runs after `snapshot.py` and writes `site/data/agent/`: `index.json` (one columnar
  row per bus item: id, program, kind, tier, title, times, countries, a point and box, link, US-person label, stale,
  shard), `items/00–31.json` (full items by id), `programs.json` (the whole suite registry, planned programs too,
  with COP's health), `verdict.json` (Verdict's `forecasts.json`, `scores.json` and calendar; a part that fails
  keeps the last published copy, marked stale) and `events.json` (14 Nov 2026: the programs' own dated events beside
  Verdict's calendar, so far Orbit's scheduled launches). The feed contract has no field for an event's time, so
  `build_events` reads it from the title ("..., 2026-11-14 17:32 UTC") and Launch Library 2's NET precision from the
  summary: Second, Minute or Hour keep the time (`at`); Day, Morning, Afternoon or Night keep the date only; anything
  coarser (Week, Month, a quarter) is left out, because its date is a placeholder, and counted under `skipped` with
  every unreadable item, so a change in Orbit's wording shows as a number. An item's own `event` object (Verdict's
  calendar shape) wins over the text. Every launch is `verified: false` (an aggregator's date). Why the slices: the free plan allows 10 ms of CPU per request and
  parsing the 1.5 MB bus takes about that. Measured 6 Nov on the real bus (956 items, 278 KB index): a cold request
  costs about 2.1 ms (parse 1.5, two searches 0.6). The build annotates a warning past 600 KB (`INDEX_WARN_BYTES`).
  `config/sources.json` (`commercial_ok`) is never read; licences reach agents per item from `sources[].licence`.
- **Code:** `functions/mcp.js` (the only route that runs code; static files stay free and unlimited) calls
  `mcp/server.js` (official MCP TypeScript SDK v2, `@modelcontextprotocol/server` `createMcpHandler`: serves protocol
  2026-07-28 and, statelessly, 2025-era clients from the same tools), `mcp/tools.js` (the tool logic, plain
  functions) and `mcp/data.js` (reads the slices through `env.ASSETS`, parsed once per isolate). Packages pinned
  exactly in `package-lock.json`, each at least two weeks old when chosen: `@modelcontextprotocol/server` 2.3.1,
  `zod` 4.6.7; dev only `@modelcontextprotocol/client` 2.3.1. `wrangler.toml` only names the output folder and keeps
  the compatibility date the project already had (2026-10-31; no bindings or env vars, checked through the API).
- **Tools** (eight, fixed order, all `readOnlyHint`): `suite_status`, `search_items` (program, country, kind, tier,
  text, since, near; 20 rows by default, 50 at most; warnings, then anomalies, then routine), `get_item` (the full
  item with sources), `top_picks`, `outage_triage` (reads `data/triage.json` directly; a missing file is a tool
  error that says triage was not built, not that there are no outages), `list_forecasts`, `forecast_record` (says plainly when nothing has resolved),
  `upcoming_events` (Verdict's calendar plus Orbit's scheduled launches, `origin` "orbit"; a launch whose stated
  time has passed is left out; either half answers without the other and lists the absent one under `missing`;
  Orbit's attribution only when a launch is returned). Every result carries `as_of`, `age_hours`, stale flags and
  the attribution lines to credit.
  The server's instructions tell agents to cite ids and publishers, never call a measurement a warning, quote ICD 203
  terms as given and check freshness.
- **Limits:** Pages Functions share the free 100,000 requests a day with Workers. The project is "fail open", so past
  the quota `/mcp` stops answering until 00:00 UTC while the map keeps serving. A browser opening `/mcp` gets a short
  plain-text help page.
- **Checked live 13 Nov 2026:** the SDK client against `https://cop.theintelligence.company/mcp` listed the eight
  tools and called `outage_triage` (all, one country, a country with no outage). **Not yet checked:** a Claude
  session using the server (the terminal `claude` sign-in had expired on 6 Nov; a session that connected before a
  deploy keeps the old tool list until it reconnects).

## Commands
- `python tools/snapshot.py` (live) or `--fixtures tests/fixtures --now 2026-10-31T20:30:00Z` (offline);
  `--dead <program>` simulates a failed feed. Then `python tools/agent_index.py` (or `--verdict
  tests/fixtures/verdict` offline) for the MCP server's files.
- `python tests/test_snapshot.py` (plain script; picks rule, dead feeds, contract validity, 3:1 outline contrast on
  both base maps, registered sources, vendored kit copy, no CDN scripts or inline styles).
- Outage triage by hand: `python tools/triage_rates.py --archive out/archive`, then `python tools/triage.py
  --snapshot site/data/today.geojson --ended out/ended/elements.geojson --publish site/data`. Tests: `python
  tests/test_triage.py` (41), `test_triage_us_power.py` (24), `test_triage_ended.py` (9), `test_rebuild_ended.py`
  (8), `test_gate.py` (22), `test_three_signal.py` (20).
- **Ended items (Tom, 13 Nov 2026: inactive items are not displayed, but every item is kept, because comparing now
  with the past is a core benefit of the suite).** Elements keeps a history log (`history/` in its repo, format
  `tic-history/1`) and serves the items that ended in the last 7 days at `data/ended.geojson`. `snapshot.py` fetches
  each program's `ended` URL (`config/programs.json`; Elements only so far) to `out/ended/<program>.geojson`, not
  published and not drawn, and with `--archive` keeps `<day>.<program>-ended.json.gz` beside the day's snapshot (the
  build uploads both). `triage.py --ended` adds those items to the hazard window (`with_ended`: never twice, marked
  `cop_ended`; rows carry `ended_at`; the record's `ended_items` gives the list's state, and a missing or old list
  is a caveat); `triage_rates.py` reads each day's list when the archive has it. Measured 1-14 Nov 2026 from
  Elements' own git history: the live feed alone still held a power-line warning in 19% of the state-times when one
  had begun in the 72-hour window, so base rates from days without the list run low and say so. The lists for 31 Oct
  to 13 Nov were rebuilt from Elements' history log with `tools/rebuild_ended.py --history <elements>/history
  --archive <folder>` (it fills only days without a list; each file says `rebuilt`, from what, and when the log
  starts) and uploaded to the releases on Tom's yes, 14 Nov 2026; the rebuilt 14 Nov list matched the one Elements
  served item for item. Base-rate cautions name rebuilt lists and the days whose log was younger than the window
  (`ended_list_kind`: served, rebuilt, short, none).
- `python tests/test_agent_index.py` (every item once in the index and its shard, no field lost, row size, registry
  order, Verdict's last-copy rule, nothing from `config/sources.json`).
- `npm ci --ignore-scripts`, then `npm test` (`node --test "mcp/*.test.js"`; builds its data from the fixtures with
  the two Python tools, so Python must be on the path): tool logic, both protocol eras, CORS and the help page, and a
  CPU timing check. `COP_AGENT_DATA=<a site/data folder>` times the real snapshot instead of the fixtures.
- Local run with Cloudflare's runtime, after building `site/data/`: `npx --yes wrangler@4.151.3 pages dev --port
  8788 --ip 127.0.0.1 --compatibility-date 2026-10-26` (that Wrangler's local runtime stops at 26 Oct; production
  runs 31 Oct). Check with `npx --yes @modelcontextprotocol/inspector@2.12.0 --cli http://127.0.0.1:8788/mcp
  --transport http --method tools/list`. Afterwards stop the node, esbuild and workerd processes by PID and delete
  `.wrangler/`.
- `python tools/serve.py` serves `site/` on 5071 with `site/_headers` (browsers refuse 5060/5061, the SIP ports).

## The suite watchdog (14 Nov 2026, Tom's "Yes, add it")
- `tools/suite_watch.py` reads COP's public snapshot and exits 1 when COP's own build is over 6 hours old, when a
  program's state is not "fresh", or when a program's data time is more than 30 minutes in the future (Elements ran
  1.6 h ahead on 12 Nov 2026 and read fresh). `.github/workflows/watch.yml` runs it on its own schedule
  (`47 */3`, which GitHub fires late) and by hand; it never runs on a push and never deploys. A failed run is the
  alert: GitHub mails the owner.
- Optional repo secret `HC_PING_URL` (a Healthchecks.io check address, set by Tom, never written in a file): each run
  pings it, and its `/fail` address on a problem, so Healthchecks.io can say when the watchdog itself stopped.
- The hub's `tic-dispatch` Worker starts it every three hours at :00 (01, 04, ... 22 UTC; hub `15caf79`, deployed
  14 Nov 2026); the cron stays as a fallback. Dispatched runs send no inputs.
- **Testing the alerts** (14 Nov 2026, Tom's yes): Actions > watch > Run workflow with `test_failure` ticked, or
  `gh workflow run watch.yml --repo metacog-osint/cop -f test_failure=true`. `--test-failure` adds one problem that
  says it is a test, so the run fails, GitHub mails the owner and Healthchecks.io gets `/fail`. The next normal run
  turns the check green again.
- Tests: `python tests/test_suite_watch.py` (10).

## Not in v0
Convergence glyph with base rates, lifeline layer, likelihood and confidence display (the kit's `judgmentChips` is
wired into the card for when judgments appear), the 14-day timeline (daily snapshots are archived for it).

## The suite ledger (built 14 Nov 2026 on Tom's "History log in kit"; not yet on `main` when written)

- `tools/ledger.py` keeps every item the picture has carried: at each build it compares the snapshot with the last
  state and writes one line per item that is new, changed (tier or response stage) or ended, with the full item. The
  format and rules are the doctrine kit's history log (`tic-history/1`, kit 0.5.0, `doctrine/kit/docs/history-log.md`),
  the same one Elements keeps and every program is meant to adopt.
- A lane is a program. A program counts as read when its state in the snapshot is fresh or late; a stale or failed
  program ends nothing. COP's own marks (`cop_pick`, `cop_strength`, `cop_stale`) are no change.
- **Source lanes** (14 Nov 2026, Tom's "complete items 3-6"): where a program's feed has a per-source `health` block
  (Cordon; Blackout since `d23e7a5`, 14 Nov), `snapshot.py` keeps `lanes` and `lanes_read` (the kit's `lanes_ok`) in
  that program's `cop_programs` entry, and the lane is `<program>/<item's first source id>`, through the program's
  `lane_map` in `config/programs.json` (Cordon: `usembassy-*` to `us-embassy-alerts`). An item ends only when its
  program and its source were both read, so a Blackout source that failed while the feed came ends nothing. A source
  the block does not name falls back to the program lane. Lanes are recomputed from each open item every build, so
  items logged before the change moved over. Dry run on the live state, 21:41 UTC 14 Nov: 1,519 open, every Cordon
  (9 lanes) and Blackout (8 in use of 21) item on its source lane, none fell back. Tests 16 to 22.
- Files are assets of the release `ledger`: `state.json.gz` and one `YYYY-MM-DD.jsonl.gz` per UTC day. **Not
  published**: the ledger holds every layer side by side, so the tool refuses `site/`, `functions/` and `mcp/`.
  Anything drawn from it for the page or the MCP server is a join and goes through `tools/gate.py`.
- Without the last state every item would be logged as new again, so the tool stops unless `--first` is passed, and
  the build step skips (with a warning) when a listed asset cannot be downloaded.
- `python tools/ledger.py --replay out/archive --assets out/ledger-replay` rebuilds a ledger from the archived daily
  snapshots, one step a day, every line marked `backfill`. Tests: `python tests/test_ledger.py` (22).
