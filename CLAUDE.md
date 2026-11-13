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
  added to one of the two lists there (a test fails until it is).
- Open to search engines since 31 Oct 2026 (no noindex; canonical `https://cop.theintelligence.company/`).

## How it works
- `tools/snapshot.py` (standard library) fetches the contract-v2 feeds of the programs in `config/programs.json` (Watchfloor,
  Context, Vigil, Noisefloor, from 31 Oct Elements and Cordon on GitHub Pages, from 8 Nov 2026 Vis, and from 12 Nov 2026 Blackout; PropertyCast is not a COP layer).
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
- **Blackout** (12 Nov 2026, kit 0.4.3): internet outages (IODA, by country and on five watched ASNs), OONI
  censorship rates and cloud and satellite providers' own status reports. Outage and OONI items are country-level
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
- **Page:** static `site/` (no build step). `cop.js` is an ES module using vendored MapLibre GL JS 6.14.0
  (`site/vendor/maplibre-gl/`, BSD-3; pinned to a release at least two weeks old, integrity checked against npm) and
  the kit's `tic-doctrine.js`. Base map: Natural Earth land from `data/world.geojson` below zoom 3.5 (OpenFreeMap's
  world tiles are ~1.5 MB each), OpenFreeMap positron or dark above it; if OpenFreeMap fails, the land carries every
  zoom and a note says so. Times through the hub's `shared/tic-time.js` (copy, keep the version line).
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
  with COP's health) and `verdict.json` (Verdict's `forecasts.json`, `scores.json` and calendar; a part that fails
  keeps the last published copy, marked stale). Why the slices: the free plan allows 10 ms of CPU per request and
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
- **Tools** (fixed order, all `readOnlyHint`): `suite_status`, `search_items` (program, country, kind, tier, text,
  since, near; 20 rows by default, 50 at most; warnings, then anomalies, then routine), `get_item` (the full item
  with sources), `top_picks`, `list_forecasts`, `forecast_record` (says plainly when nothing has resolved),
  `upcoming_events`. Every result carries `as_of`, `age_hours`, stale flags and the attribution lines to credit.
  The server's instructions tell agents to cite ids and publishers, never call a measurement a warning, quote ICD 203
  terms as given and check freshness.
- **Limits:** Pages Functions share the free 100,000 requests a day with Workers. The project is "fail open", so past
  the quota `/mcp` stops answering until 00:00 UTC while the map keeps serving. A browser opening `/mcp` gets a short
  plain-text help page.
- **Not yet checked:** a real Claude session using the server (the terminal `claude` sign-in had expired on 6 Nov;
  the MCP Inspector 2.12.0 CLI and the SDK client both passed).

## Commands
- `python tools/snapshot.py` (live) or `--fixtures tests/fixtures --now 2026-10-31T20:30:00Z` (offline);
  `--dead <program>` simulates a failed feed. Then `python tools/agent_index.py` (or `--verdict
  tests/fixtures/verdict` offline) for the MCP server's files.
- `python tests/test_snapshot.py` (plain script; picks rule, dead feeds, contract validity, 3:1 outline contrast on
  both base maps, registered sources, vendored kit copy, no CDN scripts or inline styles).
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

## Not in v0
Convergence glyph with base rates, lifeline layer, likelihood and confidence display (the kit's `judgmentChips` is
wired into the card for when judgments appear), the 14-day timeline (daily snapshots are archived for it).
