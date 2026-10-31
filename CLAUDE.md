# COP: project notes for Claude

COP, the common operational picture of The Intelligence Company: every program's latest findings on one map.
Roadmap step 2 (`D:\Projects\_ideas\new-programs.md`); built 31 Oct 2026 from the plan at
`C:\Users\PSRA User 4\.claude\plans\modular-launching-prism.md`. Design spec: the COP section of
`D:\Projects\_ideas\reports\Non DoD sources and COP visuals.md`.

## Tom's decisions (31 Oct 2026)
- **Name:** COP (plain-language alternatives were considered and declined).
- **Address:** `cop.theintelligence.company` (Cloudflare Pages project `cop`, default address `cop-4gw.pages.dev`
  because cop.pages.dev was taken). Squarespace DNS: `CNAME cop → cop-4gw.pages.dev` (added 31 Oct 2026). Repo
  secrets `CLOUDFLARE_PAGES_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` set 31 Oct; the build deploys every two hours.
- **Public and non-commercial for now** (Tom, 31 Oct 2026, later the same day): COP is open to everyone and to search
  engines, like the other programs. If Tom ever markets it, the sources a paid version can't use get scrubbed first,
  thoroughly and ethically. `config/sources.json` keeps `commercial_ok` for every source (true, false or unknown, with
  its basis) so that scrub starts from a list; `snapshot.py` lists any feed source missing from the register.
- **Site public, repo private** (Tom, 31 Oct 2026). Opening the code is a one-way door, staying private costs little
  (about 600 of the free 2,000 Actions minutes a month), and the cross-program integration is the part a paid version
  would be. Never copy COP code into public repos. If it opens later: AGPL-3.0 like Noisefloor and Vigil.
- **Review before v1:** the convergence layer (several programs agreeing over one place) is where aggregation starts
  saying more than any single public source (the mosaic effect); review those flags before v1 ships.
- Open to search engines since 31 Oct 2026 (no noindex; canonical `https://cop.theintelligence.company/`).

## How it works
- `tools/snapshot.py` (standard library) fetches the contract-v2 feeds in `config/programs.json` (Watchfloor,
  Context, Vigil, Noisefloor, and from 31 Oct Elements and Cordon on GitHub Pages; PropertyCast is not a COP layer).
  Per program: `country_mark` (fill shades the country, halo rings its label point), `area_style` (dashed for legal
  areas), `freshness` ("feed" uses the feed's generated_at, for notice programs whose items carry issue dates) and
  `routine_in_preset` (show routine items when that program's preset is chosen, for standing notices). It lints each with the vendored kit, merges them into
  `site/data/today.geojson` (itself contract v2, each item tagged `cop_program`), writes `picks.json`, `health.json`,
  `countries-today.geojson` and `world.geojson`. A failed feed keeps its last good items from the published snapshot,
  marked stale (grey); it never fails the build.
- **Picks:** anomalies with a place only; each program's own strongest first by its `strength` rules (rule order first,
  then value), one program at a time, at most `max_picks` (5). Strength is never compared across programs. Every item
  today is an observation, so picks are labelled measurements, never warnings.
- **Page:** static `site/` (no build step). `cop.js` is an ES module using vendored MapLibre GL JS 6.14.0
  (`site/vendor/maplibre-gl/`, BSD-3; pinned to a release at least two weeks old, integrity checked against npm) and
  the kit's `tic-doctrine.js`. Base map: Natural Earth land from `data/world.geojson` below zoom 3.5 (OpenFreeMap's
  world tiles are ~1.5 MB each), OpenFreeMap positron or dark above it; if OpenFreeMap fails, the land carries every
  zoom and a note says so. Times through the hub's `shared/tic-time.js` (copy, keep the version line).
- **CSP** in `site/_headers` (Cloudflare Pages format): self only, plus `tiles.openfreemap.org` for connect and
  images; `worker-src 'self' blob:` (MapLibre's module worker). No inline styles or scripts; colours come from config
  through MapLibre paint or the CSSOM.
- **Kit:** vendored in `doctrine/kit/` with `python D:/Projects/tic-doctrine/tools/sync.py D:/Projects/cop`, then copy
  `doctrine/kit/js/tic-doctrine.js` to `site/vendor/` (a test checks they match).

## Commands
- `python tools/snapshot.py` (live) or `--fixtures tests/fixtures --now 2026-10-31T20:30:00Z` (offline);
  `--dead <program>` simulates a failed feed.
- `python tests/test_snapshot.py` (plain script; picks rule, dead feeds, contract validity, 3:1 outline contrast on
  both base maps, registered sources, vendored kit copy, no CDN scripts or inline styles).
- `python tools/serve.py` serves `site/` on 5071 with `site/_headers` (browsers refuse 5060/5061, the SIP ports).

## Not in v0
Convergence glyph with base rates, lifeline layer, likelihood and confidence display (the kit's `judgmentChips` is
wired into the card for when judgments appear), the 14-day timeline (daily snapshots are archived for it), share cards.
