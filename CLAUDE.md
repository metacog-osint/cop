# COP: project notes for Claude

COP, the common operational picture of The Intelligence Company: every program's latest findings on one map, plus
the joins that read them together and an MCP server for agents (cop.theintelligence.company, public repo
`metacog-osint/cop`).

Older detail: the private planning notes. **Current state and open items:
`.claude\HANDOFF.md`** (gitignored, main checkout), read it first. Keep this file under 12 KB.

## Purpose and Tom's standing decisions
- **Purpose (Tom, 15 Nov 2026):** a warning analyst's first stop for a few named warning problems: per area, what
  changed this week across the layers against that place's own normal, read through named joins whose hit rates are
  published, ending each week in a signed, dated call that Verdict scores. The map is the index, not the product.
  Tom accepted D1-D14 of the private planning notes: first problems the Gulf/Hormuz, then internet
  shutdowns, Tom writes each charter (D2); no general convergence glyph, per-layer "how unusual here" plus named
  joins, the 15-day co-occurrence stays a diagnostic (D3); first analyst brief (D4); weekly signed call from the week
  of 23 Nov, Gulf first (D5); a Verdict question on every confirmed outage, with a second resolver (D6); picks per
  warning problem (D7, not built); triage rules M1-M3 (D8, built); gate retired (D9); repo public (D10); 12 Dec test
  date, rerun to 12 Jan if it misses (D11); Vis forecasts never picks (D12); public, non-commercial, PAI only (D13);
  Red River Valley view later, same bus (D14).
- **Name and address** (31 Oct): COP; Cloudflare Pages project `cop` (`cop-4gw.pages.dev`), Squarespace
  `CNAME cop`. Open to search engines; canonical `https://cop.theintelligence.company/`.
- **Public and non-commercial.** `config/sources.json` keeps `commercial_ok` (true/false/unknown with basis) for every
  source so a paid version could scrub from a list; `snapshot.py` names any feed source missing from it. Repo went
  public 15 Nov 2026 (D10). Nothing private in tracked files.
- **The mosaic gate is retired** (Tom, 15 Nov 2026): no held classes, no delay; Vis may serve as a natural-cause
  check; Cordon `tfr`/`faa` usable. **The voice rule replaces it:** a join shows each layer's measurement side by side
  with a base rate and a descriptive reading, never the inference in COP's voice (no "strike window", "attack
  likely", intent or timing of forces). A probability appears only on a question registered in Verdict. Each join
  carries the rule in its own words and a test checks them. `out/held/` is Tom's to delete.
- **Every item is kept** (Tom, 13 Nov): inactive items are not drawn, but ended lists and the ledger keep them.
- **Picks:** placed anomalies only, each program's strongest by its own `strength` rules, one program at a time in
  config order, at most `max_picks` (5); never compared across programs; labelled measurements, never warnings. An
  item its program says has ended is never a pick; what changed ranks before what is still there (Tom, 21 Nov).
- **Direction of concern is the brag (Tom, 21 Nov 2026):** which places are getting more or less concerning, from
  each layer's change. The word counts layers moving and never adds measurements (D3 stands for scores).
- Shipping follows the standing yes in the global `CLAUDE.md`. **Pushing `main` builds and deploys.**

## How it works
- `tools/snapshot.py` (stdlib) reads each program's contract-v2 feed. Name, family, feed, site and freshness windows
  come from the kit's suite registry (`doctrine/kit/vocab/programs.json`, edited in `../tic-doctrine`); a
  new program goes in the registry first. `config/programs.json` says only how COP draws it: `mark`, `country_mark`
  (fill/halo), `area_style`, `freshness` ("feed" or "data"), `routine_in_preset`, `routine_label`, `strength` rules
  (`abs` for signed values), `also_in`, `source_health`, `lane_map`, `ended`.
- Output in `site/data/`: `today.geojson` (the suite's **bus**; other programs read it for tips, kit `tips.py`),
  `picks.json`, `health.json`, `countries-today.geojson`, `world.geojson`. A failed feed keeps its last good items,
  marked stale (grey); it never fails the build. `sift()` drops malformed shapes (counted in `health.json` and
  `dropped`); a wrong value stays as a lint error.
- `source_health`: the feed's own per-source `health` block (kit `lanes_ok`) greys and unpicks items of a source not
  read. `data_as_of_precision` "day"/"month": shown as a date or month in UTC, never moved to the viewer's zone; a day
  item counts to the end of its day for freshness. `valid_until` is read: expired items stop standing out.
- Build: `build.yml` (cron `17 */2`, fallback); the hub's `tic-dispatch` Worker starts it at :20 past every even UTC
  hour; the `gate` job skips a scheduled run within 45 min of a success. Archive copies go to releases (daily, plus one per 6-hour slot).
- Page: static `site/`, `cop.js` ES module, vendored MapLibre GL JS 6.14.0, kit `tic-doctrine.js`, hub
  `shared/tic-time.js` (copy, keep its version line). Natural Earth land below zoom 3.5, OpenFreeMap above. CSP in
  `site/_headers`: self plus `tiles.openfreemap.org`; no inline styles or scripts. `site/scoring.html` states every
  threshold; a test checks it against the code. Share card: `tools/card.py` (headless Chrome/Edge, falls back to
  `site/card-fallback.png`, never fails the build).
- Kit: vendored in `doctrine/kit/` with `python ../tic-doctrine/tools/sync.py .`, then copy
  `doctrine/kit/js/tic-doctrine.js` to `site/vendor/` (a test checks they match).

## Layers (one line each; history and measurements in the archive)
| Program | Items and how COP draws them | Gotchas |
|---|---|---|
| Watchfloor, Context | conflict attention, press framing; Information preset | feeds mark measurements `reported` |
| Vigil, Noisefloor, Elements, Cordon | as their feeds; Cordon notices dashed | Cordon space-related `navwarn` also under Spectrum and space (`cop_also`, keep in `MAP_KEYS`) |
| Vis | `kind: forecast`, never picks; Amber/Red are tier `warning`, shown in every view | Open-Meteo non-commercial |
| Blackout | outages, OONI, provider status, public-report counts (`blackout:public:*`, reported, not measured) | no `measures`: picks rank by the scope in the title; outages are observations, no probability; `valid_until` run +12 h |
| Headwind | halos; Economic stress preset; signed z (`abs`) | `source_health` on; `ended` |
| Orbit, Launch | routine only, Spectrum and space; Launch areas dashed | event times read from titles for `events.json` |
| Undertow | halos at stated targets only; ids `UT-<LANE>-*` via `ID_PREFIXES` | origin is never a finding; reported activity kept apart |
| Wake | Maritime preset: chokepoints, ports, `ais-listed`, `ais-gap`, `ais-slow`, `ukmto` | "broadcasts an IMO number listed on", never "sanctioned vessel"; UKMTO reports unverified, name no one |
| Relief | halos, Society preset; ids `RL-IFRC-*`, `RL-WHO-*` | never quote WHO notice text |

## Joins (all write through `tools/gate.py`)
- `gate.py`: a join declares its inputs (COP programs; `READ_NOT_DRAWN` for Verdict); an item from an undeclared input
  is refused; output reaches `site/`, `functions/` or `mcp/` only when the join's own `published` switch is on (Tom's
  call per join). `python tools/gate.py --check` runs before deploy.
- **Outage triage** (`tools/triage.py`, `triage_rates.py`, `triage_replay.py`; published): for each confirmed outage,
  one line per layer (hazard, notice, attention), a reading (natural, tension context, open, incomplete; never a
  cause), base rates led by the country's own count, and the STOP replay's rates beside them. Rules (D8): episodic
  hazards count while active plus 48 h; embassy alerts sorted by type, counted once from first seen, 48 h; attention
  from Watchfloor's own causal 28-day z; FCDO, Canada and SafeTravel count only on a level change. US state power
  outages have their own seven rules and county match. Drafted Verdict questions are "draft, not registered".
  **Registering one is Tom's:** `python tools/register_question.py <verdict_id> <probability> [--commit]` (commits,
  never pushes; Verdict's CI wants it within the hour; never run by the build). A failure never fails the build.
- **Three-signal join** (`tools/three_signal.py`, join 0.4, published, page heading "Four signals"): interference,
  notice, connectivity and press per area; converged needs all four. Base rates over 30 archived days; a signal usual
  on over half of them counts only above its 90th percentile. UKMTO's reports are context; an Attack counts toward the
  notice only above the 90th percentile of UKMTO's own series (option D, Tom 18 Nov). Vis is weather context only.
  Wake can only be a condition (PortWatch lags 7-16 days).
- **Undertow sanctions and election joins:** built, `published` off (Tom's call); write only to `out/`.
- **Direction** (`tools/direction.py`, `config/areas.json`, published as `direction.json`): each layer's last 7 days
  against the 14 before, per warning problem and Watchfloor zone; thresholds on `site/scoring.html`. Reads
  **`tools/memory.py`** (release `memory`, never published): one record per subject, kept after it leaves, compared with
  each new item. Noisefloor counts come from its full daily files because its feed was capped at 6 events a day until
  15 Nov; one alert under two ids counts once; PortWatch transits are a condition, never counted.

## MCP server (`/mcp`)
- `https://cop.theintelligence.company/mcp`: read-only, public, no sign-in, no AI model called. `tools/agent_index.py`
  writes `site/data/agent/` (`index.json` columnar with coded labels, `items/00-31.json` shards, `programs.json` with
  page checks, `verdict.json`, `events.json`). `functions/mcp.js` -> `mcp/server.js` (MCP TS SDK v2,
  `@modelcontextprotocol/server` 2.3.1, `zod` 4.6.7, pinned) -> `mcp/tools.js`, `mcp/data.js`.
- Nine tools, fixed order, all `readOnlyHint`: `suite_status`, `search_items`, `get_item`, `top_picks`,
  `outage_triage`, `list_forecasts`, `forecast_record`, `upcoming_events`, `three_signal`. Results carry `as_of`,
  `age_hours`, stale flags and attribution; server instructions hold the rules per program.

## Ledger and watchdog
- `tools/ledger.py`: kit history log `tic-history/1`; one line per item new, changed (tier or stage) or ended; lane
  = program, or `<program>/<source id>` where the feed has a health block. An item ends only when its program and
  source were read; stale copies move nothing. Assets of release `ledger`; **never published** (refuses `site/`,
  `functions/`, `mcp/`). Needs the last state or `--first`; `--replay out/archive` rebuilds.
- `tools/triage_log.py`: every triage reading, per outage and place; release `triage-log`. Not published.
- `tools/data_quality.py` (`watch.yml` job `data-quality`): review rules on the bus; report only, never fails.
- `tools/suite_watch.py` (`watch.yml`, also dispatched every 3 h): exits 1 when COP's build is over 6 h old, a
  program is not fresh, a data time is 30+ min in the future, a program has malformed items, or the hub's site check
  (`tic-sitecheck/1`) failed a page or is 14+ h old. A failed run is the alert (GitHub mail); optional secret
  `HC_PING_URL` pings Healthchecks.io.

## Commands
- `python tools/snapshot.py` (live) or `--fixtures tests/fixtures --now 2026-10-31T20:30:00Z`; `--dead <program>`.
  Then `python tools/agent_index.py` (offline: `--verdict tests/fixtures/verdict`).
- Tests: `npm ci --ignore-scripts`, then `python tests/run_all.py` (the list `build.yml` and PR check `tests.yml` share).
- **Known quirks** (memory `cop-local-test-quirks`): `npm test`'s 5 ms CPU check fails on this laptop even on
  untouched `main`; the browser pane's map needs 10-20 s and reads zero width while hidden.
- Local Cloudflare runtime: `npx --yes wrangler@4.151.3 pages dev --port 8788 --ip 127.0.0.1 --compatibility-date
  2026-10-26`; afterwards stop node/esbuild/workerd by PID and delete `.wrangler/`.
- `python tools/serve.py` serves `site/` on 5071 with `site/_headers`.
