# COP: project notes for Claude

COP, the common operational picture of The Intelligence Company: every program's latest findings on one map.
Roadmap step 2 (`[local path]`); built 31 Oct 2026 from the plan at
`[local path]`. Design spec: the COP section of
`[local path] DoD sources and COP visuals.md`.

## Purpose (Tom, 15 Nov 2026)
COP is **a warning analyst's first stop for a few named warning problems**: for each problem's area it shows what
changed this week across the layers, measured against that place's own normal; it reads that through named joins
whose hit rates are published; and it ends each week in a signed, dated call that Verdict scores. The map is the
index, not the product, and agents get the same through the MCP server. Tom accepted D1 to D14 of
`[local path]` as recommended (15 Nov 2026, evening):
- First warning problems: the Gulf and Strait of Hormuz, then internet shutdowns (D2); Tom writes each charter.
- The general convergence glyph is dropped; "how unusual for this place" per layer and named joins replace it, and
  the 15-day co-occurrence measure stays a diagnostic (D3).
- Pick the first analyst and send the brief now (D4); a weekly signed call from the week of 23 Nov, Gulf first (D5);
  a Verdict question registered on every confirmed outage, with a second resolver (D6).
- Picks per warning problem, from what changed, replace one pick per program in config order (D7). Not built yet.
- Triage's three rules change (D8, as in the purpose doc §2, Change): count an episodic hazard's active interval
  plus a lag, not its formation date; sort embassy alerts by type and count each once; take "unusual attention"
  from Watchfloor's own 28-day z-score, computed causally (`cop-joins.md` M1 to M3). Built 15 Nov 2026 with the
  wider input (M5) and the D6 path, join 0.9, branch `triage/step2`: see "Triage step 2" under Outage triage.
- The mosaic gate is retired (D9, below). "Repo private" is overtaken (D10). The 12 Dec test date stays, rerun to
  12 Jan unchanged if it misses (D11). Vis forecasts stay out of the picks (D12). Public, non-commercial, publicly
  available information only (D13). The Red River Valley view comes later, on the same bus (D14).

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
- **Site public, repo private** (Tom, 31 Oct 2026; **overtaken**: the repo went public about 12:10 PM CST
  15 Nov 2026 to save Actions minutes, and Tom accepted that record the same day, D10). Opening the code is a one-way door, staying private costs little
  (about 600 of the free 2,000 Actions minutes a month), and the cross-program integration is the part a paid version
  would be. Never copy COP code into public repos. If it opens later: AGPL-3.0 like Noisefloor and Vigil.
- **Review before v1:** the convergence layer (several programs agreeing over one place) is where aggregation starts
  saying more than any single public source (the mosaic effect); review those flags before v1 ships. Superseded for
  joins by Tom's ruling of 15 Nov 2026 below.
  **The mosaic gate is retired (Tom, 15 Nov 2026, about 6:15 PM CST).** From 12 to 15 Nov `tools/gate.py` held back
  any join that read a layer on its MILITARY list (Noisefloor, Vis, Launch, Cordon `navwarn` and `czib`, Orbit
  `maneuver`) or any layer or kind on neither list (Cordon `tfr` and `faa`), wrote held joins only under `out/held/`,
  and let only Tom release one by hand. Tom's ruling: the gate "was likely something I just agreed to"; "We operate
  exclusively with publicly available information and our objective is to show what can be done with it. That
  doesn't mean we have to spell it out, though." No held classes, no delayed release; Vis may serve as a check for
  natural causes; Cordon's `tfr` and `faa` kinds are usable. The repo, its code and its archive and ledger releases
  had gone public at about 12:10 PM CST that day, so the gate withheld only COP's own sentence (record and reasoning:
  `[local path]` §7 and D9).
  **The voice rule replaces it** (Tom confirmed the wording, 15 Nov 2026): a join shows each layer's measurement side
  by side, with a base rate and a descriptive reading, and never states the inference in COP's voice: no "strike
  window open", no "attack likely", no intent, no timing of forces. A probability appears only on a question
  registered in Verdict. Each join keeps that rule in its own wording (`three_signal.py` `READINGS`, `WHAT`,
  `NOT_A_WARNING`; a test checks its own words for "operation", "workable", "likely" and the like).
  **What `tools/gate.py` still does:** every join writes through it. A join must declare its inputs, each a COP
  program (`config/programs.json`); every item it cites must belong to a declared input (otherwise "refused", written
  nowhere); and it reaches `site/`, `functions/` or `mcp/` only when its own `published` switch is on (Tom's call, per
  join). `python tools/gate.py --check` runs before the deploy and fails on any refused or unpublished join there.
  Anything left in `out/held/` from before is not copied or published (the joins are regenerated from public inputs);
  the folder is Tom's to delete.
  **The three-signal join is published** (join 0.2, same day, Tom's "Retire and publish three-signal"): the build
  runs `tools/three_signal.py --snapshot site/data/today.geojson --archive out/rates-archive --publish site/data` after
  triage and writes `site/data/three-signal.json` (`three_signal.public()`: each area's reading, the three signals
  with their items, the tightest span, Vis's weather as context only, the counts of other notices, the base rate from
  the archive replay, the caveats and the drafted question marked "draft, not registered" with no probability field;
  the working document stays in `out/three-signal/`). Vis is now described as the area's weather forecast ("Green at
  worst"), not as a window. Page: the "Three signals" section (`renderThreeSignal` in `cop.js`), shown in any view that
  shows Noisefloor, Cordon, Blackout or Vis. MCP: the `three_signal` tool. A failure never fails the build. Local live
  build, about 6:45 PM CST 15 Nov: Hormuz read partial (interference near Doha, Abu Dhabi and Dubai; no new notice
  naming military activity; no confirmed outage), weather Green at worst; base rate over 4 archived days with every
  layer current: interference 4, notice 0, connectivity 0, all three 0.
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
  **Triage step 2 (join 0.9; D8, M5 and D6 of the purpose doc, Tom's yes of 15 Nov 2026; branch `triage/step2`,
  built 15-16 Nov 2026).** The three 12 Nov rules above are replaced:
  (1) *Episodic hazards* (`cop-joins.md` M2): a GDACS cyclone or flood (`EPISODIC_GDACS`) counts while active, from
  its "from" to its "to" plus `EPISODIC_LAG_H` (48 h), not only when it began in the 72 hours. Standing kinds
  (drought, volcano status) keep "began in the window". MURASU-26 (red, India; last advisory 3 Nov 06:00 UTC) now
  reads significant on the 4 Nov snapshot, standing on 5 Nov.
  (2) *Embassy alerts* (M1): sorted by the type their own titles give (`alert_type`): security and demonstration
  count; weather, natural disaster, health, routine messages and security alerts about a holiday or a scheduled
  event (`EVENT_WORDS`) are listed under `recent_not_counted` and not counted (a weather alert adds a caveat pointing
  to Elements). An alert counts once, from the earliest date COP saw on it in snapshots taken by the outage's time
  (`alert_first_dated`, from `history.alert_dates`), so a re-dated edit is "re-dated", not fresh. The 48-hour window
  is 48 hours (it was floored to midnight, so up to 72); a date with no time counts from the start of its day.
  (3) *Unusual attention* (M3): a Watchfloor item naming the country, raised on its own 28-day z-score (baseline
  `wf-28d`: the 28 complete days before the day it measures, never that day; `more-context/pipeline/measures.py`).
  Raised on the 90-day median only is standing attention ("elevated"), listed and not counted. A Watchfloor day that
  begins after the outage is listed as after, not counted. The 25%-of-archived-days share is gone, so the replay is
  causal: each day reads the z-scores its own snapshot carried.
  (4) *"Deliberate" is now "tension context"* (`tri.TENSION`) on the page, in the MCP server and in triage.json
  (readings, base-rate keys, `by_country`). "Deliberate" stays Blackout's own word for its BO-H-1A judgment. Reading
  texts say what the layers showed and how often that happens anyway; none states a cause.
  (5) *Wider input* (M5): besides IODA's `outage` items, Cloudflare Radar's verified anomalies (`radar-anomaly`,
  BO-IND-10; network scope when the title names an AS, with a caveat) and Outage Center entries that name a country
  (`radar-outage`, BO-IND-9; Cloudflare's stated cause quoted in `outage.stated_cause`, never assessed). Both are
  timed from the start in Blackout's summary ("since <t>, ongoing" / "from <t> to <t>"), not the run time, and count
  as confirmed. A Radar entry with no country goes to `unplaced` (listed on the page, not read). Records carry
  `outage.source`, `started_at`, `ended_at`, `network`; one more than 7 days old gets a caveat.
  (6) *Two questions on every confirmed outage* (D6; M6): the OONI question as before, now with Verdict's fields
  (`resolution`: known_at the day after the 72-hour window, resolve_by a week later, "check after" in the rule), and
  `cause_question_draft`: whether Cloudflare's Outage Center ("government directed"), Access Now, or two of
  Reuters, AP and AFP attribute the outage to a government order within 7 days (`CAUSE_WINDOW_D`; void if already
  published before registration). Each public draft has `verdict_id`; `registerable` is true for confirmed outages.
  **Registering one is Tom's, one command, his number:** `python tools/register_question.py <verdict_id>
  <probability> [--commit] [--dry-run] [--rise]` (in a COP checkout; reads the live triage.json or `--triage`). It
  builds a tic-feed/2 forecast (program verdict, VD-PIR-2, the kit's likelihood term and rule-based confidence,
  alternatives per question), checks it with the kit's contract and Verdict's own `forecast_problems`, refuses an
  unconfirmed outage, a closed window, a question settled before it is asked, and a Verdict checkout not on main or
  behind origin/main, then appends it to `verdict/drafts/cop-triage.json` approved by `--approved-by` (default Tom
  Brummer) and runs Verdict's `register`. Verdict's CI wants the commit within the hour: `--commit` commits the two
  files (never pushes) and the command prints the push. The build never runs it (a test checks).
  Measured on COP's archive (31 Oct-16 Nov 2026, 17 days, 9 counted for readings, 2,151 country-days), before
  (main `4d76f56`) and after: natural 0.74% -> 0.74%, deliberate 5.35% -> tension context 5.25%, open 93.91% ->
  94.00%; fresh notice 2.09% -> 1.02%; unusual attention 3.32% -> 3.69%. Twelve notices stopped counting (Holiday
  Travel in Russia and Ukraine, Cartagena Independence Day, the ASEAN Summit, Mexico's El Nino weather alert,
  Guatemala's "Message for U.S. Citizens", a re-dated Mexico City demonstration alert, and five more than 48 hours
  old that the midnight floor had counted); none started counting. Iran's attention now counts on 4, 5, 6, 8 and 11 Nov
  (0 days before). Countries' tension-context shares moved both ways: Colombia 67% -> 11%, Russia 44% -> 11%, Saudi
  Arabia 78% -> 56%; China and Croatia 0% -> 67%, Peru 56%, Ireland and Poland 44%, Iran 22% (raised on their own
  28-day z-scores, which the old share switched off). The replay: the 4 archived IODA outages keep their readings
  (Tunisia open; DR Congo, Niger, Seychelles unconfirmed); 3 Radar anomalies are new (Iraq, Russia, Algeria: open).
  No archived outage changes reading. Scripts: `out/compare.py`, `out/notice_diff.py` in the worktree (not kept).
  **Base-rate population (M4 of the 15 Nov joins review; Tom's yes, 15 Nov 2026, state rates included):** each
  reading's base rate leads with the country's own count and the distinct events behind it ("In Saudi Arabia ...
  on 5 of 8 counted days, from 3 notices"; hazards, notices, runs of consecutive days with unusual attention:
  `by_country_events`), then the pooled rate with its population named ("Across all 239 countries ... (only 61
  countries showed any signal)"). State power rates do the same (`by_state_events`). The page footer leads with the
  countries that showed a signal. `triage_rates.py --today` (default: now, UTC) leaves today's partial snapshot out
  of the background (`days_left_out_partial`); it still feeds the history and the replay. `HIT_RATE_CAUTION` stood
  beside every rate until the STOP replay (below); with the replay on file it is replaced on the country rates, and
  kept, reworded (`triage_replay.STATE_CAUTION`), on the state power rates, which STOP does not cover. Measured on the archive (31 Oct-16
  Nov): counted days 9 -> 8, country-days 2,151 -> 1,912, natural 0.74% -> 0.78%, tension context 5.25% -> 5.39%,
  open 94.0% -> 93.8%; on the 61 countries' days: natural 3.1%, tension context 21.1%, open 75.8%; state-days 884
  -> 832, natural 14.7% -> 14.8%, 30 of 52 with a signal. Readings unchanged: the 7 replayed outages keep theirs.
  **STOP replay (cop-purpose sequence step 4, first half; Tom's choice of 15 Nov 2026, "start with the two layers",
  then Cordon; on `main` as `9fc5d16`, 16 Nov 2026).** `tools/stop_replay.py` reads each shutdown Access Now's
  #KeepItOn STOP sheet dates to 2025 (255 shutdown-country rows; the sheet is Blackout's fixture
  `blackout/tests/fixtures/stop/stop_sheet_2026-11-13.csv`, no data licence, so counts and rates only) at 12:00 UTC on
  its first day: natural if `tri.hazards` finds a significant hazard; else tension context if `tri.notices` counts a
  Cordon notice (with `--cordon`) or the country was on Watchfloor's Rising list the day before; else open;
  incomplete when that Watchfloor day is missing, partial or unscored. Control days: every 2025 day in the same country
  with no shutdown within 7 days (open-ended ones taken as 30), compared country by country; Myanmar has none, so its
  83 shutdowns are left out of the matched comparison. A same-day attention variant is labelled sensitivity.
  Datasets under `history/` follow `history/CONTRACT.md` (`cop-layer-history/1`; manifests hash their rows byte for
  byte, so `.gitattributes` keeps them from line-ending conversion): `history/elements/` (`tools/history_elements.py`),
  `history/watchfloor/` (`tools/history_watchfloor.py`, from Watchfloor's own GDELT backfill kept on C:,
  `[local path]`; 19 partial days in June 2025 and 28 unscored days, 1-14 Dec 2024
  and 2-15 Jul 2025, when under 14 complete days sat in the 28-day baseline), `history/cordon/`
  (`tools/history_cordon_*.py`: FCDO advice and US embassy alerts dense, State's levels weak; no Canada, NZ or
  departure postures). Runs: `history/replays/stop-2025-two-layer-2026-11-16` and `...-three-layer-...`; each run
  takes about 8 minutes. Results (matched shutdowns against their control days), two layers, 172: natural 15.1% vs
  5.8%, tension context 8.1% vs 5.6%, open 62.2% vs 83.5%, incomplete 14.5% vs 5.1%; nationwide shutdowns tension
  context 13% vs 5%; 16 of India's protest shutdowns read natural on a GDACS flood active elsewhere in India (triage
  reads countries); exam shutdowns fall in GDELT's June gap (45% incomplete). Three layers, Cordon's 25 countries,
  157 matched: tension context 7.0% -> 13.4% on shutdowns, 5.8% -> 8.8% on control days. Tests:
  `test_stop_replay.py` (17), `test_history_cordon.py` (29), both in the build. Not built: option C (forward labels).
  **The replay beside the base rates (step 4, second half; Tom's yes of 16 Nov 2026, branch `stop-triage-wiring`).**
  `tools/triage_replay.py` reads the newest `history/replays/stop-2025-two-layer-*` and `...-three-layer-*` manifests
  (counts and rates only; credit "Access Now, #KeepItOn STOP"; `--replays` on `triage.py`, default `history/replays`,
  so the build needs no new flag) and `triage.py` `attach_replay` puts them in `triage.json` as `replay` (pooled
  shares for shutdowns and ordinary days, both layer counts, a row per country, the caveats of both runs) and as
  `replay_rate` on each internet-outage record whose reading is natural, tension context, open or incomplete: Cordon's
  25 countries get the three-layer figures (157 matched), every other country the two-layer ones (172) with a caveat
  that Cordon was not replayed there. Unconfirmed dips and US power records carry none. The page prints it beside each
  record's Base rate and as a footer paragraph with the replay caveats folded; the MCP `outage_triage` returns `replay`
  and `replay_rate`, credits Access Now, and its note and rule 7 forbid saying triage detects, finds or predicts
  shutdowns. `HIT_RATE_CAUTION`'s replacement is built from the figures' directions (`triage_replay.caution`). Without
  the manifests nothing changes (the old caution stays). Tests: `test_triage_replay.py` (13) and the MCP triage test.
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
  **New Zealand SafeTravel (same join 0.7, 14 Nov 2026, Tom's "Yes, add safetravel"):** kind `safetravel` is read
  exactly like `fcdo` and `canada` (in `NOTICE_KINDS`, `CHANGE_ONLY_KINDS`, `LEVELLED_KINDS`; declared input
  `cordon:safetravel`). MFAT dates its advice by the day of any page update (no time), and Cordon marks an item
  anomaly only when the level or the regional note changed, so a plain update day is an edit, not counted.
  `notice_level` ranks it `[place in SAFETRAVEL_LEVELS, regional note 1/0]` (normal, low, medium, high, avoid, as
  `cordon/safetravel.py` `ORDER`), so dropping a regional note at the same level is a lowering. Measured on the
  archive with COP's 22:21 UTC copy of 14 Nov (120 SafeTravel items, all routine, newest dated 2 Nov, none in the
  48-hour window): fresh notice 1.91%, natural 0.84%, deliberate 4.72%, open 94.44%, the same before and after; 14
  Nov alone 6 notices and 12 deliberate of 239 both times. Live 22:21 UTC: DR Congo's outage lists SafeTravel as a
  fourth standing notice, reading unchanged (unconfirmed), gate open.
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
  **Both settled or built (checked 14 Nov 2026):** Elements' 7-day `ended.geojson` (read by `triage.py --ended`)
  held 403 expired NWS warnings at 22:36 UTC, which covers the first. Blackout branch `power/county-fips` adds
  `us_counties` (`{fips, name, meters}`) to each BO-IND-14 item (on Blackout `main` as `2ea3a4d`, 14 Nov 2026).
  **(8) County-level match (join 0.8; Tom, 14 Nov 2026, "build the county-level match"):** when the item lists its
  counties (`outage_counties`, only FIPS the county table puts in that state), Elements is read for those counties:
  NWS alerts by shared county codes, point hazards within `STATE_NEAR_KM` (100 km) of an outage county's internal
  point, areas over one county's internal point; SPC background and whole-US items keep their own tests. A
  significant or standing hazard elsewhere in the state goes to `elsewhere` (listed, never counted). The layer's
  `scope` is "counties" (else "state", with a caveat that the item lists none), and `meters_under_hazards` gives the
  share of meters out in counties a counted hazard covers, with the largest county that has none; below half, a
  caveat says so, because one hazard still makes the reading natural. The state-day base rate is still state-level,
  so a caveat says it overstates natural for a county reading. Recorded check (13 Nov ODIN, line lowered to 10,000):
  California stays natural on Medford's High Wind Warning over Siskiyou and Hanford's Winter Storm Warning over
  Fresno, Sacramento's and Reno's warnings move to `elsewhere`, and the hazards cover under half the meters out
  (Los Angeles County, 11,231 of 13,723, has none). Fixture `blackout-us-power-counties.json` (Blackout 2ea3a4d).
  Live on `main` as `d1e91eb` (build 43663941459, 15 Nov 2026).
- Open to search engines since 31 Oct 2026 (no noindex; canonical `https://cop.theintelligence.company/`).

## How it works
- `tools/snapshot.py` (standard library) fetches the contract-v2 feeds of the programs in `config/programs.json` (Watchfloor,
  Context, Vigil, Noisefloor, from 31 Oct Elements and Cordon on GitHub Pages, from 8 Nov 2026 Vis, from 12 Nov 2026 Blackout, from 13 Nov 2026 Headwind and Orbit, from 14 Nov 2026 Launch and Undertow, from 15 Nov 2026 Wake, from 16 Nov 2026 Relief; PropertyCast is not a COP layer).
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
  Blackout also publishes judgments (kind `assessment`, tier `warning`): a cable-corridor outage (BO-H-2A). Since
  blackout-1.1 (16 Nov 2026) its likely-shutdown judgment is gone: a country outage is an observation whose
  `direction` toward BO-H-1A says whether OONI's blocking in the outage's own hours supports a shutdown, with no
  probability (MCP rules 2 and 7 and the `outage_triage` note say so since 18 Nov 2026). They show in every view like Vis's
  warnings; the card says "Judgment: a probability, not a measurement" and "Warning: Blackout's judgment", and the MCP
  instructions say the same. None was live on 12 Nov, so the fixture has none.
- **Headwind** (13 Nov 2026, kit 0.4.6, which sets its registry entry live with its feed's own licence line):
  currencies, energy and food prices against their own range, sanctions and screening-list changes, and BIS and
  OFAC rules. Currency, local-food and country-tagged rule items are country-level points, drawn as rings at the
  country's label point (`country_mark` "halo"; a routine reading exists for about forty countries at once, so
  fills would shade half the map). Energy, FAO index and list-change items have `geometry: null` and list under
  Off the map. Nearly every item is tier `routine`, so `routine_in_preset` shows them under its own preset,
  "Economic stress" (`routine_label` "reading for every series"). Its `measures.z` is signed, so its one
  `strength` rule has `"abs": true` (14 Nov 2026): its anomalies rank by the size of z, a fall as high as a rise of
  the same size, and the pick's reason shows the signed value ("z-score -3.1 against its own range"). Any rule can
  take `abs`. No Headwind anomaly was live or in the fixture when this was built (60 routine items), so it is tested
  on the function only. `freshness` "feed": its items carry
  market dates and the ECB publishes on business days only. Colour: Paul Tol's wine `#882255` (outlines
  `#4A0F2C`, `#F2BFD6`). Eight sources in `config/sources.json`, licences as Headwind's notes record them: the UN
  list `commercial_ok` false; FAO, WFP, OFAC and trade.gov "unknown"; ECB, EIA and the Federal Register true.
  On the gate's open list (`tools/gate.py`; confirmed by Tom, 14 Nov 2026); no join reads it yet.
  `tests/fixtures/headwind.geojson`: its live feed of 16:28 UTC 13 Nov, every timestamp moved back 12 days 21 hours.
  **`ended` (15 Nov 2026, Headwind plan step 14):** `https://headwind.theintelligence.company/data/ended.geojson`, Headwind's
  history log (`tic-history/1`, committed by its workflow to `history/` in its repo). Until Headwind's `headwind-1.6` batch is
  deployed the address answers 404, and `snapshot.py` logs "headwind ended items: fetch failed": deploy Headwind first.
  **`source_health` (15 Nov 2026, Headwind plan step 15):** an opt-in program flag. `snapshot.py`'s `unread_sources()` reads
  the feed's own `health` block with the kit's `lanes_ok`; an item whose lane (first source id, through `lane_map`) the
  block marks not read well gets `cop_stale` and `cop_source_stale`, is drawn grey, is never picked, and its card says
  "No current data from this Headwind source". `health.json` (and `cop_programs`) carry `sources` (`total`,
  `not_read` with each id, state and detail), and the Sources board names them. Headwind and, from 16 Nov 2026, Undertow set
  it; Cordon and Blackout keep their behaviour. Dry run on Headwind's live feed, 15 Nov: 13 sources, none unread. The suite ledger
  is unchanged: it still counts a source-stale item as seen while its program was read (the open last_seen
  question below).
- **Orbit** (13 Nov 2026, kit 0.4.8, which sets its registry entry live): orbital launches of the last 7 days at
  the launch site (GCAT site coordinates, precision `approximate`), scheduled launches at the pad (Launch Library
  2), one reentry summary and catalog-change items (new entries, owner changes), the last two with `geometry:
  null`, listed under Off the map. **From 15 Nov 2026 also orbit changes in watched satellites** (Space-Track element
  sets, Orbit `orbit-2.4`; kind `maneuver`, `geometry` null, Off the map; Tom's "Yes, add them"), each carrying
  Orbit's approved Space-Track citation in its source licence and summary. No counterspace events.
  Every item is kind `observation`, tier `routine`, `us_person_data: organizations`, so it has no `strength` rules
  and makes no picks; `routine_in_preset` shows its items under "Spectrum and space", the preset it shares with
  Noisefloor (its registry family), and `routine_label` names them. `freshness` "feed": a launch item keeps the
  launch time and a scheduled launch keeps Launch Library 2's last update, which can be months old. Registry
  windows 24 h fresh and 72 h stale, as Headwind's: it runs every 6 h at :41 UTC and GitHub often fires late.
  Colour: Paul Tol's olive `#999933` (outlines `#4D4D19`, `#E6E6B3`); his remaining hues sit too close to
  Okabe-Ito hues already in use. Two sources in `config/sources.json`, licences as Orbit's
  `doctrine/sources.json` records them: GCAT (CC BY 4.0, cited as "Data from GCAT (J. McDowell,
  planet4589.org/space/gcat)") `commercial_ok` true; Launch Library 2 (no data licence stated; credited, images
  not used) "unknown"; Space-Track (`space-track`, 15 Nov 2026) `commercial_ok` false. **On the gate's open list by
  kind** (`tools/gate.py`; Tom, 13 Nov 2026, after it was first held because its launches include national-security
  payloads at named sites and times, all publicly announced): `launch`, `upcoming`, `reentries`, `late-object`,
  `owner-change`. **`orbit:maneuver` was on the MILITARY list until the gate was retired the same evening** (15 Nov 2026, with Tom's yes to manoeuvres in COP's
  feed): most watched satellites are military or intelligence satellites, so no join may cite them.
  No join reads it yet. `tests/fixtures/orbit.geojson`: its live feed of 22:03 UTC 13 Nov (21 items: 6 launches, 14
  scheduled, 1 reentry summary, no catalog changes yet), every timestamp moved back 13 days 2 hours, and from 14 Nov
  the "YYYY-MM-DD HH:MM UTC" times in titles and summaries too, so scheduled launches sit after the fixture's time.
  Its scheduled launches also reach the MCP tool `upcoming_events` (14 Nov 2026; see the MCP server section).
- **Launch** (14 Nov 2026, kit 0.4.9, which adds its registry entry live): FAA space-operations TFRs (polygons) and
  scheduled launches that coincide with a launch closure area in time and place, or that GCAT has since dated inside
  its window (the area's polygon). Time-only coincidences stay on Launch's own page. Every item is kind
  `observation`, tier `routine`, precision `area`, `us_person_data: organizations`; no `strength` rules;
  `routine_in_preset` under "Spectrum and space" beside Orbit and Noisefloor. `mark` "areas", `area_style`
  "dashed" (notices, as Cordon's), `country_mark` "halo", `freshness` "feed". Colour (changed 14 Nov 2026, item 5 of Tom's list): violet `#9933FF` (outlines `#4A1A80`, `#D9BFFF`). Paul
  Tol's purple `#AA4499`, its first colour, failed the dataviz validator's normal-vision floor against Context
  `#CC79A7` (ΔE 13.9) and Headwind `#882255` (13.4; floor 15) and sat in the colour-blind floor band against Vigil
  (6.4 protan). A search over sRGB found 190 colours that pass against all ten other programs; `#9933FF`'s worst
  pairs are Vigil, colour-blind ΔE 11.5 (target 8), and Context, normal 23.1, with contrast 4.41 on the light land
  and 3.98 on the dark. The dashed outline still sets Launch's areas apart. The eleven colours together fail
  all-pairs (Orbit and Watchfloor 3.9 deutan, Orbit and Noisefloor 12.4 normal), as any 11 hues would; outlines,
  mark types and the panel's labels carry identity. Sources `faa-tfr` (US Government work, `commercial_ok` true) and `orbit-snapshot` ("unknown"), as
  Launch's `doctrine/sources.json` records them. **Held on the gate's MILITARY list until the gate was retired, 15 Nov 2026** (confirmed by Tom, 14 Nov 2026;
  Orbit was held first and later opened): closure areas and the launches they match include national-security launches and missile
  firings. No join reads it. `tests/fixtures/launch.geojson`: its live feed of 14:14 UTC 14 Nov (3 TFRs), every
  timestamp moved back 13 days 18 h 9 min. A local live build 14 Nov read it fresh, 3 items, no lint errors.
- **Undertow** (14 Nov 2026, kit 0.5.3, which adds its registry entry live): covert influence operations that Meta,
  Google, OpenAI (its RSS feed only), TikTok (through the EU Code of Conduct Transparency Centre) and the EEAS have
  publicly disrupted or named, at operation level, with DISARM v1.6.1 technique tags. Every item is kind
  `observation`, tier `routine`, `reported`; no `strength` rules and no picks (it has no baseline of disclosures).
  An item whose publisher names targets is a Point (one target) or MultiPoint at the target countries' label points,
  precision `country`; 121 of 425 on 14 Nov name no target and list under Off the map. **An item's `countries` holds
  the origin beside the targets**, so `country_mark` is "halo" (rings at the geometry, which is targets only): "fill"
  would shade origin countries through `countries`, and Context already fills under the same preset. `mark`
  "halos", preset "Information" beside Context (its registry family), `routine_in_preset`, `freshness` "feed" (items
  carry disclosure dates, some months old). Its ids are `UT-<LANE>-<key>`, not `undertow:<kind>:...`, so the gate
  reads them through `ID_PREFIXES` in `tools/gate.py` (the lane is the kind; an unlisted upper-case prefix is held).
  **On the gate's open list** (Tom, 14 Nov 2026). Two joins read it, both unpublished: the sanctions join and the election join (below). Colour: magenta `#CD00A0` (outlines
  `#670050`, `#F3BFE7`). A search over sRGB on a 5-step grid found 107 colours that pass the dataviz validator
  against all eleven others, every one a magenta or a dark brown; `#CD00A0`'s worst pairs are Vigil, colour-blind
  ΔE 9.0 (target 8), and Context, normal 16.9 (floor 15), with contrast 4.59 on the light land and 3.82 on the dark
  (the best brown reaches only 3.03 on the dark land). Rings against Context's filled markers carry identity too.
  Six sources in `config/sources.json`, as Undertow's `doctrine/sources.json` records them: Meta (MIT), the EEAS
  (reuse with acknowledgement) and DISARM (CC BY-SA 4.0) `commercial_ok` true; Google, OpenAI's RSS and TikTok's
  Code reports "unknown". A seventh, `dfrlab-rss` (16 Nov 2026, Undertow `a3e1a65`): DFRLab articles naming an
  operation Undertow tracks, as corroboration (grade B2), `commercial_ok` false (Atlantic Council terms:
  noncommercial use). Two more on 16 Nov 2026 (Undertow's reported-activity slice): `checkfirst-rss` (B2) and
  `correctiv-rss` (B3), both `commercial_ok` "unknown" (no terms published; names, dates and links only). From then
  Undertow's reporter items are reported activity, one per reporter and operation, with an `activity` block (state
  active or quiet) and no origin. Until 16 Nov 2026 this line said the MCP instructions named DFRLab; they did not
  (`d92a3e6` changed only this file and `config/sources.json`). **Since 16 Nov 2026 (branch
  `undertow/reporter-sources`)** the instructions name DFRLab, CheckFirst and CORRECTIV as reports kept apart from
  disclosures, and rule 8 has two parts. Disclosures: `countries` lists the origin beside the targets, only targets
  are marked, an origin is never a finding of who directed an operation, confidence is about origin only.
  Reported activity (an `activity` block): give the reporter, grade, first and last report and state; "quiet"
  never means ended; a name match says the reporter wrote about the operation, not that it found it running; its
  `countries` are the countries named in the reports (they can include the operators' country), not an origin, and
  it adds no origin or confidence. **Only stated targets are marked** (Undertow, Tom's call of 16 Nov 2026): a
  reporter's item at countries its reports name that a disclosure of the operation records as targets, and Google's
  and OpenAI's items, which name no target, not at all (all 369 list under Off the map; before, 264 were drawn on
  the countries their description names). On 16 Nov, 47 of Undertow's 434 items carry a place.
  **`undertow-watch-register`** (16 Nov 2026, with Undertow's first watch row, Armenia, DFRLab, target Armenia):
  `commercial_ok` false while its only row is DFRLab's (each row's licence travels in the item's source). Rule 8
  names it since 16 Nov 2026 (Tom's yes, after the live inspector read showed rule 8 described reported activity only
  as reports on tracked operations): a `UT-WATCH-` item is marked at the targets entered from the report, and has no
  matched disclosure unless `activity.closed_into` names one. **Widened the same day** (Undertow's watch rows may
  list several tracked operations; Tom's "Yes, as recommended"): the item may name tracked operations or none,
  `activity_period` is when the activity ran, and a reporter's "state-aligned" is alignment, never origin. The G7
  Rapid Response Mechanism (Global Affairs Canada) is a hand-entered reporter, named in the opening paragraph. Both: call DISARM techniques provisional while
  `disarm_rules_status` reads "proposed". The card shows a "Reported activity" badge and Reported by, First report,
  Last report, Reports and State facts (`renderCard`, `p.activity`); `get_item` returns the whole block (the shards
  keep every field); `search_items` rows do not carry it, but the title ends "(active)" or "(quiet)".
  **The Undertow sanctions join** (16 Nov 2026, join 0.1, branch `undertow/sanctions-join`; Tom's "Yes, as
  recommended" to `_ideas\drafts\2026-11-16-undertow-cross-suite-joins.md`): `tools/undertow_sanctions.py` matches the
  entries of Headwind's `data/list-changes.json` (fetched by URL; 30 days) that Headwind types `entity` exactly against
  `config/undertow-names.json` (15 rows: SDA, ANO Dialog and Struktura on OFAC, EU, UK, France and Canada, each name as
  Headwind prints it; Haimai and CGTN are on no list, so no row), and puts each match beside the Undertow items whose
  `operation.sponsor` names the organisation: "Headwind saw <name> added to <list> (<programmes>) on <date>. <Publisher>
  names <organisation> in its disclosure of <operation>." A person is never matched; a row whose entry id Headwind
  shows under another name is listed in `stale_rows` and matches nothing. Headwind prints an EU entry's first alias,
  which for SDA is Polish and for ANO Dialog Russian, so those rows carry that string. Base rate: listing actions
  (one per list and date) from Undertow's `operation.listed_by` (Undertow's standing listings, read from the publishers'
  notices), per month since the first; on Undertow's 16 Nov local build 5 in 41 months, 0.12 a month, last 21 Feb 2025.
  Inputs `undertow` and `headwind:sanctions`; **`published` is off** (Tom's call), so the build writes only
  `out/undertow-sanctions/`. No page or MCP view yet: they come with the switch. `config/undertow-names.json`
  `checked_by` is empty until Tom checks the table.
  **The Undertow election join** (18 Nov 2026, join 0.1, branch `undertow/election-join`; step 3 of the joins plan,
  fallback (a) because ElectionGuide's calendar is "for non-commercial use" with no data-use policy found; countries
  Tom's choice of 18 Nov 2026, all 40 of Undertow's stated targets): `tools/undertow_elections.py` reads Verdict's
  `calendar.geojson` (kind `election`, `ref` `election:<CC>:<vote>:<round>`; 48 national votes, 1 Nov 2024 to
  31 Dec 2027, hand-entered in Verdict on 18 Nov 2026 from the electoral bodies' own pages, 9 marked unverified) and
  matches an Undertow item whose stated target (`operation.targets`) votes, when the item's period overlaps the 90
  days before the vote through election day (provisional). The period is a watch row's `activity.activity_period` or
  a disclosure's `operation.period` (month, "a to b", H1/H2, Q, year) cut at its publication date; never a report
  date, so reporter items with report dates only are left out and counted. Each match keeps `period_days` and `fit`
  ("inside" or "overlaps"); nothing is dropped for being long. Voice: "<Country> voted on <date> in <a vote>;
  <publisher> reported <operation> targeting <country> from <a> to <b> days before (<period>)." ("votes on" for one
  to come); no intent, no claim it aimed at the vote (`NOT_A_FINDING`, a voice test). Base rate: share of
  item-country pairs in a window, against the share of days in a window and the chance a period of the same length
  placed at random overlaps one. `config/undertow-elections.json` is the scope (40 countries, names, span); a target
  outside it is counted and never matched. Inputs `undertow` and `verdict:calendar`: Verdict is no layer, so
  `gate.READ_NOT_DRAWN` lets a join declare it. **`published` is off** (Tom's call): the build writes only
  `out/undertow-elections/`. Live run 18 Nov (Undertow feed 14:30 UTC, the new calendar): 35 matches at 19 votes,
  the Armenia case among them (DFRLab's activity period 2026-01 to 2026-06, report 12 Oct); 28 of 100 pairs (28%) in
  a window against 9% of days and 21% by random placement. Most matches are the EEAS's year-long periods (fit
  "overlaps", 365 days). Tests: `test_undertow_elections.py` (16).
  `tests/fixtures/undertow.geojson`: 22 items of its live feed of 01:24 UTC 15 Nov (every lane, every geometry kind
  and confidence state), every timestamp moved back 14 days 5 h 18 min. **Size:** with all 425 items a local live
  build's bus grew from 2.9 MB (live COP) to 4.2 MB and the agent index from 519 KB to 608 KB (Undertow's rows
  106 KB), past `INDEX_WARN_BYTES`, which annotates and does not fail. That build (evening of 14 Nov CST) read all
  twelve feeds fresh, Undertow 425 items, no lint errors; under Information its rings drew and its card opened.
  **Date-only items (15 Nov 2026, kit 0.5.6, Tom's "One kit release"):** an item whose source gives no time carries
  `data_as_of_precision` ("day": midnight UTC on that date; "month": midnight UTC on the 1st). The card's "Data as
  of" and the list rows show it through `asOfEl` (the kit's `asOfDateLabel`): "June 2026" or "Oct 30, 2026", read in
  UTC, with no `data-tic`, so TicTime never moves it into the viewer's zone. Before, Undertow's "2025-06" read
  "May 31, 7:00 PM CDT". Undertow sets it on 384 of 425 items (23 month, 361 day; Undertow `d2d0b8f`); any program
  may. Checked in the browser pane on a local build with Undertow's new feed: the "Data Center Bandwagon" card reads
  "Data as of June 2026". **In the agent index since 16 Nov 2026** (branch `undertow/precision-ended`): a
  coded column `data_as_of_precision` (about 2 bytes a row, some 4 KB on the live bus of 2,146 rows, 555 KB on
  15 Nov); `search_items` rows name it only when it is set, and MCP rule 4 says to give a date-only item's date or
  month, never a time, and never to move it into another zone.
  **Whole-day items from Watchfloor and Noisefloor (16 Nov 2026, branch `fix/day-precision-freshness`):** both feeds
  now say `data_as_of_precision: "day"` with `data_as_of` at the day's midnight (they sent `T23:59:59Z`, which read
  "5:59 PM CST"). Three readers had assumed the end of the day, so each now counts a day item to its end:
  `snapshot.covered_until` for a program's age (fresh 36 h, late past it; a day item counts to the end of its day, never
  later than now; `health.json` still reports the source's own time), `triage.day_span` for Watchfloor's "day begins
  after the outage" and "data reaches the outage", and `three_signal` for Noisefloor's 96-hour look-back. Archived
  snapshots keep the old `T23:59:59Z` with no precision, and `day_span` reads both the same. Deploy this before the
  Watchfloor and Noisefloor feed commits go live, or both programs read "late" for about 15 hours a day.
  **`ended` and `source_health` (16 Nov 2026, same branch):** Undertow keeps the kit's history log (`tic-history/1`,
  Undertow `393758e`) and serves `https://undertow.theintelligence.company/data/ended.geojson`; its feed's `health`
  block is keyed by the source id its items carry (`meta-threat-research`, `google-iob`, `openai-news-rss`,
  `disinfocode-tiktok`, `eeas-fimi-4`; state `up` or `failing`), so no `lane_map` is needed. A failed Undertow lane
  keeps its last good copy, which COP now draws grey and the ledger does not count as seen. Until Undertow's
  commit is deployed the address answers 404 and `snapshot.py` logs "undertow ended items: fetch failed": deploy
  Undertow first. No join reads Undertow's ended list yet.
- **Wake** (15 Nov 2026, kit 0.5.5, which sets its registry entry live): IMF PortWatch's daily counts of
  AIS-visible vessel transits through 28 maritime chokepoints, as 7-day means that Wake computes and compares with
  the 90 days before and the same weeks a year earlier (Wake `wake-2.0`). One Point per chokepoint, precision
  `point`, kind `observation`; tier `anomaly` when either comparison trips, else `routine`. Ids are
  `wake:chokepoint:<id>:<newest day>`, so the gate reads them as program `wake`, kind `chokepoint`. Its own preset
  **"Maritime"** (new), `mark` "markers", `country_mark` "halo" (its `countries` are Wake's littoral states, and the
  point already says where), `routine_in_preset` with `routine_label` "transit readings at every chokepoint this
  week". One `strength` rule: `measures.change_year_ago_pct` with `abs` (a 95% fall at Hormuz outranks a 44% rise at
  Luzon; the label keeps the sign). `freshness` "data": `data_as_of` is PortWatch's newest day, and the registry's
  240 h fresh and 408 h stale windows allow its weekly publication and 2-9 day lag. `ended`:
  `https://wake.theintelligence.company/data/ended.geojson` (Wake's history log, `tic-history/1`). **Wake is read and
  deployed by hand once a week** until the IMF permits automated pulls, so a week without a by-hand run turns it
  late, then stale. Colour: deep teal `#006D77` (outlines `#00363B`, `#A6E3E9`), the candidate farthest from every
  fill in use (CIE76 34.7, nearest Vigil). Source `imf-portwatch` in `config/sources.json`, `commercial_ok`
  "unknown" (the IMF's terms allow reuse with attribution and name no non-commercial limit, but automated bulk
  pulls need its written permission, not yet asked). The MCP instructions name it, and rule 9 says what its counts
  are, that an anomaly is not a warning, to give the week an item covers, and that dark transits are not counted.
  **Known limit:** `three_signal.py` joins within 48 hours, and PortWatch lags 7-16 days, so a Wake reading can only
  sit beside a signal as a condition (as Vis does), never be one. In the Maritime view, Wake's anomalies list under
  "Also watching" when the five picks are taken by other programs, while "Today" still says nothing is outside its
  usual range in this view (COP's general pick logic, unchanged here). `tests/fixtures/wake.geojson`: its live feed
  of 14:48 UTC 15 Nov (28 items, 7 anomalies), every timestamp moved back 14 days 18 h 19 min. The MCP tests used
  "wake" as their unknown program; they use "tempo" now. Checked offline 15 Nov in the browser pane: the Maritime
  chip draws 28 teal points, a card opens with "Measured, not a warning" and the signed size line.
  **Six kinds (17 Nov 2026, Wake on a 6-hour schedule; COP's entry updated 18 Nov 2026, branch
  `claude/wake-items-scoring-452805`).** Live on 18 Nov: 28 `chokepoint`, 59 `port` (PortWatch port calls, the same
  signed `measures.change_year_ago_pct`, so the one strength rule ranks ports and chokepoints together), 61
  `ais-listed` (a Digitraffic vessel that broadcasts an IMO number listed on a sanctions list, from Headwind's vessel
  table; `geometry` null when a report failed Wake's position checks), 7 `ais-gap` (LineStrings) and 1 `ais-slow`
  (AIS records near subsea lines in the Baltic; anomalies with no value on the strength rule, so they rank after any
  port or chokepoint), and 33 `ukmto` (UKMTO's incident reports of the last 30 days, always routine, `countries: []`,
  `link: null`). Every kind shows under Maritime (`routine_label` "weekly readings at every chokepoint and port, AIS
  records and UKMTO's reports"). `also_in`: UKMTO reports whose title type is Attack, Hijack or (Attempted) Boarding
  also under Conflict (advisories and suspicious activity stay in Maritime); `ais-listed` also under Economic stress,
  beside Headwind's list changes. `source_health` is on: Wake's `health` block is keyed by the source id its items
  carry (`imf-portwatch`, `imf-portwatch-ports`, `digitraffic-ais`, `ukmto-incidents`, `headwind-vessels`), no
  `lane_map`. A card whose item has no `link` now shows its first source's own page ("Source page", any program), so
  a UKMTO card links ukmto.org's recent-incidents page; "Open in Wake" stays. MCP rule 9 now covers all four groups:
  listed items always "broadcasts an IMO number listed on", never "sanctioned vessel"; a gap is not a vessel going
  dark on purpose; UKMTO's reports are UKMTO's, unverified, naming no one responsible. Seven sources added to
  `config/sources.json` as Wake's feed states them. The fixture gained one item of each new kind (from Wake's feed of
  00:50 UTC 18 Nov, moved back 17 days 4 h 21 min; date-only values and dates in text by 17 days). **Not changed:**
  the registry's windows for Wake (240 h fresh, 408 h stale, set for weekly PortWatch) are the kit's; with AIS and
  UKMTO every 6 hours, the program reads fresh while PortWatch alone could be weeks old, and the per-source health
  is what catches a stalled lane. **UKMTO and the three-signal join:** not counted (Tom's call). Measured 18 Nov in
  `[local path]`: an Attack sits in the 96-hour lookback on 46
  of 46 days from 3 Oct, so counting them makes the notice signal permanent.
- **Relief** (16 Nov 2026, kit 0.5.9, which sets its registry entry live; branch `relief-layer`, not pushed): IFRC GO
  emergency appeals and DREF allocations, and WHO Disease Outbreak News notices (Relief `relief-0.1`; 203 items on the
  first read, all `routine`, kind `observation`). Points at the country's label point, precision `country` (WHO notices
  can be MultiPoint for several countries or have no geometry and list under Off the map). **Ids are
  `RL-IFRC-<key>` and `RL-WHO-<key>`, not `relief:`**, so `tools/gate.py` reads them through `ID_PREFIXES` as program
  `relief`, lane as kind (`ifrc`, `who`), as it does Undertow's. Its own preset **"Society"** (new; Temper, the other
  Society program, will share it), `mark` "halos", `country_mark` "halo", `routine_in_preset` with `routine_label`
  "IFRC appeals and WHO outbreak notices, ringed on their countries", `source_health` on (two lanes), `freshness`
  "feed" (registry 12 h fresh, 36 h stale). No `strength` rules: nothing is an anomaly, and an appeal's amount or
  `place_context.tail_probability` ranks size or rarity, not need. `ended`:
  `https://relief.theintelligence.company/data/ended.geojson`. Colour: the Society family colour, earth brown
  `#8C510A` (outlines `#3F2405`, `#E8CFA0`; CIE76 32.0 from the nearest fill, Watchfloor `#D55E00`); Temper reuses it.
  Sources `ifrc-go-appeals` and `who-don` in `config/sources.json`, both `commercial_ok` false (IFRC: non-commercial
  with credit, no bulk copying of the data bank; WHO: research extracts with acknowledgement, no commercial use, title,
  date and link only, never notice text). MCP instructions rule 11: the publishers' own records, tier always routine,
  `place_context`'s Poisson figure is a measurement against the country's own ten-year rate and not a warning, never
  quote WHO notice text. `tests/fixtures/relief.geojson`: 11 items cut from the live feed of 04:45 UTC 16 Nov (7 IFRC
  of different appeal types and hazards, 4 WHO: Point, MultiPoint, no geometry), full timestamps moved back 15 days
  8 h 16 min (day-precision ones by whole days, so they stay at midnight). Built locally from the fixtures: 11 items,
  10 placed, none dropped, lint clean. If the address is not deployed when this ships, `snapshot.py` logs "relief
  ended items: fetch failed" until it is.
- **Items shown in a second preset (`also_in`, 14 Nov 2026, Tom's "Yes, add the filter"):** Cordon's NGA
  warnings that name rocket launching, space debris, a space vehicle or spacecraft, or reentry (kind `navwarn`,
  title or summary, Orbit's `navwarn` pattern) also show under "Spectrum and space", in Cordon's blue dashed style,
  whether or not the routine switch is on. `snapshot.py` tags them `cop_also: ",spectrum,"` (a string, because
  MapLibre's `in` tests strings; it must stay in `MAP_KEYS` in `cop.js`, or the map's copy loses it and nothing
  draws); `baseFilter()` adds them in that preset and the panel names them. Why not Orbit's feed: Cordon already
  carries the same NGA warnings (19 of 19 on 14 Nov), so Orbit items would draw each area twice. TFRs are not
  tagged: Launch draws the space-operations ones in that preset (Cordon's copies show under Everything and
  Notices, so those seven TFRs appear twice in Everything). The gate is unchanged: no join reads `cop_also`, and
  Cordon's `navwarn` kind stayed on the MILITARY list (retired 15 Nov 2026). Checked 14 Nov on a local live build: 19 tagged, drawn
  and clickable under Spectrum and space, no console errors.
- **How COP scores (`site/scoring.html`, 18 Nov 2026, brief A of the 17 Nov competitor scan):** one public page of every rule behind the picks, strength, freshness and expiry, outage triage and the three-signal join, in plain words with every threshold, modelled on OSINT Swarm's `/scoring` page but under the voice rule (readings descriptive, probabilities only in Verdict, base rates beside every reading). `scoring.js` fills the strength table, the freshness table and the base rates from `data/health.json`, `data/triage.json` and `data/three-signal.json`, so they match the map. `test_scoring_page_states_the_thresholds_the_code_uses` checks every threshold the page writes against the constant in `triage.py`, `three_signal.py` and `config/programs.json`: change a window there and the test names the sentence to change. Linked from the top bar ("How it scores") and from "How COP reads".
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
- **Kit:** (0.5.8 since 16 Nov 2026) vendored in `doctrine/kit/` with `python [local path] [local path]`, then copy
  `doctrine/kit/js/tic-doctrine.js` to `site/vendor/` (a test checks they match).

## MCP server (`/mcp`, built 6 Nov 2026; nine tools since 15 Nov 2026)
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
  calendar shape) wins over the text. Every launch is `verified: false` (an aggregator's date). Why the slices: the free plan allowed 10 ms of CPU per request (Workers Paid since 17 Nov 2026: 30 s) and
  parsing the 1.5 MB bus takes about that. Measured 6 Nov on the real bus (956 items, 278 KB index): a cold request
  costs about 2.1 ms (parse 1.5, two searches 0.6). The build annotates a warning past 2 MB (`INDEX_WARN_BYTES`; 600 KB until the move to Workers Paid, 17 Nov 2026).
  Since 15 Nov the six repeated-label columns (`CODED`: program, kind, tier, status_class, us_person_data, stale) are
  stored as numbers into the index's `codes` lists; `mcp/tools.js` compares numbers and decodes only the rows it
  returns, so tool output is unchanged (checked on the live bus: 8 searches and all 2,123 rows identical). That took
  the live index from 632 KB to 536 KB. Timing on this laptop showed no difference beyond noise; the gain is size.
  `config/sources.json` (`commercial_ok`) is never read; licences reach agents per item from `sources[].licence`.
- **Code:** `functions/mcp.js` (the only route that runs code; static files stay free and unlimited) calls
  `mcp/server.js` (official MCP TypeScript SDK v2, `@modelcontextprotocol/server` `createMcpHandler`: serves protocol
  2026-07-28 and, statelessly, 2025-era clients from the same tools), `mcp/tools.js` (the tool logic, plain
  functions) and `mcp/data.js` (reads the slices through `env.ASSETS`, parsed once per isolate). Packages pinned
  exactly in `package-lock.json`, each at least two weeks old when chosen: `@modelcontextprotocol/server` 2.3.1,
  `zod` 4.6.7; dev only `@modelcontextprotocol/client` 2.3.1. `wrangler.toml` only names the output folder and keeps
  the compatibility date the project already had (2026-10-31; no bindings or env vars, checked through the API).
- **States (15 Nov 2026, kit 0.5.7; pushed 16 Nov with kit 0.5.8):** index rows carry `subdivisions` (ISO 3166-2, from
  Cordon's FAA items and Elements' US hazards) and `search_items` takes `state` ("US-ND"); an item for the whole
  country does not match a state. Triage is unchanged (its state matching keeps its own approved rules).
- **Tools** (nine, fixed order, all `readOnlyHint`): `suite_status`, `search_items` (program, country, kind, tier,
  text, since, near; 20 rows by default, 50 at most, `offset` for the next page since 17 Nov 2026; warnings, then anomalies, then routine), `get_item` (the full
  item with sources), `top_picks`, `outage_triage` (reads `data/triage.json` directly; a missing file is a tool
  error that says triage was not built, not that there are no outages), `list_forecasts`, `forecast_record` (says plainly when nothing has resolved),
  `upcoming_events` (Verdict's calendar plus Orbit's scheduled launches, `origin` "orbit"; a launch whose stated
  time has passed is left out; either half answers without the other and lists the absent one under `missing`;
  Orbit's attribution only when a launch is returned), `three_signal` (15 Nov 2026: reads `data/three-signal.json`; optional `area`; a missing file is a tool error). Every result carries `as_of`, `age_hours`, stale flags and
  the attribution lines to credit.
  The server's instructions tell agents to cite ids and publishers, never call a measurement a warning, quote ICD 203
  terms as given and check freshness.
- **Limits:** on Workers Paid since 17 Nov 2026 (Tom, $5 a month for the account): no daily request cap (10 million a
  month included), 30 s CPU per request. Before that, Pages Functions shared the free 100,000 requests a day with
  Workers and `/mcp` would stop answering at the quota while the map kept serving. A browser opening `/mcp` gets a short
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
  tests/test_triage.py` (42), `test_triage_us_power.py` (30), `test_triage_ended.py` (9), `test_rebuild_ended.py`
  (8), `test_gate.py` (19), `test_three_signal.py` (21).
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
- **Malformed items** (15 Nov 2026, Tom's "complete items 1-5"): it also reads `health.json` beside the snapshot
  (`--health` to point elsewhere) and exits 1 when a program has malformed items left out of the picture, naming
  the program, the count and up to three reasons. A `health.json` it cannot read is a problem too, never a quiet
  pass.
- Tests: `python tests/test_suite_watch.py` (17).

## Not in v0
Lifeline layer (the general convergence glyph is dropped from the plan, Tom 15 Nov 2026, D3), likelihood and confidence display (the kit's `judgmentChips` is
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
  snapshots, one step a day, every line marked `backfill`. Tests: `python tests/test_ledger.py` (27).

## Review fixes of 15 Nov 2026 (live on `main` at `6b18dd4` since 15:14 UTC 15 Nov; build 43728656941)

Tom accepted the three open calls as built (15 Nov): expired items stop standing out even while their program is not
yet stale, the "Measured or reported, not a judgment" badge on Watchfloor's and Context's own measurements, and the
new page elements (Warnings block, "Today's picks" heading, the two "How COP reads" bullets, "Also at this spot", the
malformed and expired counts).

Six faults from the suite review of 15 Nov 2026 (`findings.md`, units `cop-pipeline` and `cop-frontend`), each
confirmed by its second reviewer. Built in worktree `[local path]`, branch
`fix/review-2026-11-15`, cut from `main` at `a790f20`. Tom's recorded decisions are untouched: the gate's lists, one
pick per program in config order, the triage rules and wording, and that Vis's forecasts are never picks.

1. **One malformed item no longer stops the build.** `snapshot.sift()` reads every feature before anything uses it
   (the feeds, the last good copy and Elements' ended list). It leaves out a wrong *shape*: a feature or its
   `properties` that is not an object, an `id` that is not text, a `geometry` that is not GeoJSON with numeric
   positions, `countries` or `sources` that are not lists of strings or objects, any NaN or Infinity (Python reads and
   writes them, a browser refuses the whole file), and anything the kit's contract check itself cannot read. A wrong
   or missing *value* is still a lint error and the item stays. Each one left out is counted in `health.json`
   (`dropped: {count, examples}` per program), listed in `today.geojson` under `dropped` (`{id, program, index,
   reason}`; the kit's own name for items held back, which the ledger reads), named in an Actions warning and shown on
   the Sources board. The same fault had two more doors, now shut: `card.py` (a pick whose title or time it could not
   print; a failed card page now falls back to the generic card) and `agent_index.py` (an Orbit launch whose title was
   not text; a Verdict file of the wrong shape now counts as a failed read and keeps the last copy). Measured with
   `out/review-case/fuzz.py`: 113 malformed shapes in each of six programs' fixtures, 678 builds. On `a790f20`, 273
   raised in a step that fails the build (the kit's contract check 149, `snapshot.py` 70, `card.py` 48,
   `agent_index.py` 6) and 30 wrote NaN or Infinity into the snapshot; here none does either. The 12 live feeds of
   14:30 UTC 15 Nov (2,029 items) lose nothing.
2. **Warnings are listed first**, under "Today" and above the picks, placed or not, each row saying whose judgment it
   is and until when it stands. They are still never picks. (Their marks keep the routine size; not asked, not
   changed.)
3. **`valid_until` is read.** The page works out `cop_expired` once, as it loads. An expired item no longer stands
   out: no mark in the default view, no row, no pin; it shows where routine items show, drawn as one, and its card
   says "Expired" with the time. A pick that has expired is named in one line under the picks ("Pick 5 has passed its
   valid-until time...") and the lists say how many items in the view have expired. Every card with a `valid_until`
   shows it. **Consequence to know:** Blackout sets `valid_until` to its run time plus 12 hours on every item, and
   Launch and Headwind do the same with about 48 hours, so if one of them misses its runs for longer than that, its
   marks leave the default view before the program reads stale (Blackout: stale at 48 h). The Sources board and the
   expired count still say so.
4. **One click handler** (`HIT_LAYERS`, `idsAt`): the top mark under the cursor opens, and its card lists whatever
   else is drawn at that spot ("Also at this spot"), each one press away. Checked in a browser: Kilauea's dot inside
   the US shaded for a Blackout route hijack opens Kilauea, with the hijack listed.
5. **Three sentences now come from the snapshot.** "How COP reads" counts findings by kind (`renderHow`). The line
   under "Today" when a view has no pick says what the view holds (placed findings under Also watching, unplaced
   under Off the map, warnings above). The anomaly badge is "Measured, not a warning" only when the item's
   `status_class` is `assessed`; otherwise "Measured or reported, not a judgment", the contract's own words for an
   observation. The feeds carry no field that says "official notice", so the page cannot say more than that, and
   Watchfloor's and Context's own measurements now read "Measured or reported" too, because their feeds mark them
   `reported`.
6. **The ledger counts a sighting only on a read.** `ledger.step()` hands the kit only the items of programs read on
   that build, so a failed feed's last good copies (`cop_stale`) no longer move `last_seen` or log anything, and an
   end is bracketed by the last real read. An item named in the snapshot's `dropped` list stays open, and a program
   with a malformed item that had no readable id ends nothing on that build (`unnamed_drops`). Reproduced with the
   real tools on the saved live feeds, Watchfloor's fetch failed for three builds: `last_seen` moved to 20:30 on
   `a790f20` and stays at 14:30 here.

Tests: `test_snapshot.py` 36 (was 25), `test_ledger.py` 27 (22), `test_agent_index.py` 19 (16). Of the 19 new
ones, 18 fail on `a790f20`; the other records that the kit already honours `dropped`. The page has no browser test, so its four new tests check that `cop.js` still reads what it must; the
page itself was checked by hand in the browser pane (desktop and phone width, light and dark) on a picture built from
the live feeds plus crafted items (`out/review-case/mkcase.py`: an expired Amber, two Blackout judgments, two
malformed items, an expired pick). `npm test`: 26 pass, and the CPU timing check fails on this laptop with or
without these changes (5 to 14 ms against its 5 ms line, measured on an untouched copy of `a790f20` too).

**Follow-ups done the same day** (`8a80996`, Tom's "complete items 1-5"): the watchdog reads `health.json` and
alerts on a dropped item (see the watchdog section); the lede, og:description, image alt and share-card footer say
"measurement or a report" / "Measured or reported, not warnings", since picks include notices; the Sources board
counts items "unusual against its own baseline" and warnings, from the picture as the page reads it (expired items
not counted); warnings draw at the standing-out size, as anomalies do, until they expire, and keep their shaded
country ahead of a routine item. The click inside a shaded country was checked live (Kilauea inside the US shaded
for a Blackout hijack opens Kilauea, with the hijack under "Also at this spot").

Sightings at source level (item 7, built 15 Nov 2026 on Tom's "complete the other open items"): the rule is that a
program which keeps a failed source's last good items in its feed publishes the kit's per-source `health` block,
keyed by the source id its items carry, and marks a kept copy not read. COP's ledger (`step`, `sighted`) now leaves
out an item whose own source lane is named and not read, as it already did for a whole program not read, so a kept
copy neither ends nor moves last_seen; an item whose source the block does not name falls back to its program.
Orbit (branch `feed/source-health`: `orbit/feed.py` `source_health`, from the snapshot's lane records) and Elements
(branch `feed/source-health`: `read_this_run` in `tools/elements.py`, where a kept copy used to count as read, plus
the block in the feed) are the first two after Headwind, Cordon and Blackout. Not done: greying their kept items
(`source_health` in config/programs.json, as Headwind has), and a partial read (one FIRMS satellite, some MeteoAlarm
countries) still counts as read for the whole source. Item 8 closed with no change (Tom, 15 Nov 2026):
Blackout's items expire 12 hours after its run, which is also when the registry calls it late (fresh_hours 12, stale
48), so its items stop standing out exactly when its Sources pill turns Late, still show with the routine ones and are
counted; Headwind and Launch keep the same order (late 24 h, expired 48 h, stale 72 h). The agent `index.json` passed the
600 KB warning line (601 KB at 15:13 UTC 15 Nov, 632 KB by 19:40 UTC); coding its repeated labels (15 Nov, above) brings
it to 536 KB, about 260 bytes a row, so it passes 600 KB again near 2,370 items (2,123 on 15 Nov). The next lever is
Cordon's routine notices, 45% of the index.
**Measured 18 Nov 2026** (live build of 14:20 UTC, after Wake's six kinds): 748,140 bytes (731 KB), 2,881 rows, about
260 bytes a row, 142 KB gzipped. By program: Cordon 950 rows (260 KB, 36%), Elements 465 (123 KB), Undertow 439
(85 KB), Vigil 264 (62 KB), Relief 206 (35 KB), Wake 189 (53 KB: 61 ais-listed, 59 port, 33 ukmto, 28 chokepoint, 7
ais-gap, 1 ais-slow), Headwind 149 (38 KB), Noisefloor 71, Watchfloor 42, Orbit 33, Vis 32, Context 21, Blackout 20.
The 600 KB line is retired: `INDEX_WARN_BYTES` is 2 MB since the move to Workers Paid (`0745c9a`), so no slimming is
needed; at 260 bytes a row the 2 MB line sits near 8,000 rows.
