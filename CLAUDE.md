# COP: project notes for Claude

COP, the common operational picture of The Intelligence Company: every program's latest findings on one map.
Roadmap step 2 (`D:\Projects\_ideas\new-programs.md`); built 31 Oct 2026 from the plan at
`C:\Users\PSRA User 4\.claude\plans\modular-launching-prism.md`. Design spec: the COP section of
`D:\Projects\_ideas\reports\Non DoD sources and COP visuals.md`.

## Tom's decisions (31 Oct 2026)
- **Name:** COP (plain-language alternatives were considered and declined).
- **Address:** `cop.theintelligence.company` (Cloudflare Pages project `cop`).
- **Private repo** (`metacog-osint/cop`). COP is the most likely product to earn money: the public site shows a free
  picture that makes it known while the code and details stay private. Never copy COP code into public repos.
- **Commercial use:** every source carries `commercial_ok` in `config/sources.json` (true, false or unknown, with its
  basis). The free picture uses everything; a paid tier may use only cleared sources. Tom reviews the values before
  anyone pays. `snapshot.py` lists any feed source missing from the register as unregistered.
- Hidden from search engines (meta, `X-Robots-Tag`, `robots.txt`) until Tom approves going public.

## How it works
- `tools/snapshot.py` (standard library) fetches the four contract-v2 feeds in `config/programs.json` (Watchfloor,
  Context, Vigil, Noisefloor; PropertyCast is not a COP layer), lints each with the vendored kit, merges them into
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
