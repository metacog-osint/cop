# COP layer history: how rebuilt historical data is labelled (`cop-layer-history/1`)

*15 Nov 2026, for the STOP replay (so that all data is labelled for future use). Every
dataset that rebuilds a layer's past (Elements' hazards, Cordon's notices, Watchfloor's attention, Blackout's
outages, labelled shutdowns), and every replay run made from them, follows this contract. The point: anyone
picking a file up later can tell what it is, where every row came from, how far to trust it, what's missing, and
what they may do with it, without asking the session that made it.*

## Layout

```
history/<layer>/<dataset_id>/manifest.json     what the dataset is (below); always committed
history/<layer>/<dataset_id>/rows.jsonl[.gz]   one JSON object per row; committed only when small (< 5 MB)
                                               and its licence allows it; otherwise kept in out/history/... or a
                                               release asset, with manifest.files saying where
history/replays/<run_id>/manifest.json         a replay run: its inputs (dataset ids and sha256), code and results
```

`<layer>` is the COP program id (`elements`, `cordon`, `watchfloor`, `blackout`) or `labels` (outages of known
cause, such as Access Now's STOP rows). `<dataset_id>` is `<layer>-<what>-<first>-<last>`, for example
`watchfloor-country-attention-2024-12-01-2025-12-31`. A rebuilt dataset never overwrites another: a new method or
range gets a new id, and `supersedes` names the old one.

## manifest.json

| Field | Meaning |
|---|---|
| `contract` | `"cop-layer-history/1"` |
| `dataset_id`, `layer`, `title` | as above; the title says what it is in one line |
| `description` | what a row is and what it is for, in plain words |
| `sources[]` | `{id, name, url, licence, attribution, how}`. `how` is `api` (queried by date), `archive-file` (the publisher's own past files), `wayback` (Internet Archive copies; each row names its capture), or `derived` (built from another dataset here, named in `derived_from`) |
| `derived_from[]` | dataset ids this was built from, with their sha256 |
| `method` | `{code, commit, params, matches_live, differences[]}`. `code` is the path of the script, `commit` its git sha (or `"uncommitted"` plus the file's sha256). `matches_live` is true only when the live program's own code and thresholds were used; `differences` lists every way it departs from what the live layer would have shown |
| `coverage` | `{start, end, time_precision, places, place_code}`. Dates are UTC. `time_precision` is `minute`, `hour` or `day`. `place_code` is `ISO 3166-1 alpha-2` for countries (FIPS 10-4 from GDELT converted, with the mapping named) or `US state USPS`. `places` is `"all"` or the list |
| `gaps[]` | `{from, to, places, reason}`: every stretch or place with no data or partial data, and why |
| `biases[]` | what the data over- or under-counts, in plain words (for example "embassy alerts only from embassies whose archive pages survive") |
| `rows` | `{count, places, days}` |
| `row_schema` | each field: name, type, meaning, unit |
| `files[]` | `{path_or_url, sha256, bytes}` for every rows file, wherever it is kept |
| `redistribution` | `rows` (rows may be published), `aggregates` (counts and rates only), or `none`, from the strictest source licence |
| `built_at`, `built_by` | UTC time; the tool and the session that built it |
| `uses`, `not_for` | what it is fit for, and what it is not (for example "not a record of every advisory change: Wayback captures are irregular") |
| `supersedes` | an older dataset id, or null |

## Rows

Every row carries:

| Field | Meaning |
|---|---|
| `place` | ISO alpha-2 (or USPS state) |
| `at` | the time the row describes, ISO 8601 UTC, cut to `precision` (a day row is `2025-03-04`) |
| `precision` | `minute`, `hour` or `day` |
| `source_id` | one of the manifest's `sources[].id` |
| `source_ref` | where exactly it came from: the API URL with its query, the GDELT file stamp, the Wayback capture URL (`https://web.archive.org/web/<14-digit stamp>/<url>`), or the publisher's archive page |
| `retrieved_at` | when this session fetched it, UTC |
| `quality` | a list, empty when nothing is wrong: `partial-day`, `estimated-date`, `capture-gap` (no capture within the window, so the value is carried from an earlier one), `parsed-best-effort`, `level-unknown` |

plus the layer's own fields, named as the live program names them (`tier`, `alert_level`, `z`, `ratio`, `level`,
`kind`), so a row can be turned back into the item the live feed would have carried.

## Replay runs

`history/replays/<run_id>/manifest.json` adds `inputs[]` (dataset id and sha256 of each dataset read), `labels`
(the labelled outages and their source), `controls` (how the control days were drawn, with the random seed),
`layers_replayed` and `layers_not_replayed`, and `results` (counts and rates per reading, per country, with the
hit rate and the false-alarm rate side by side). It also carries the caveats a reader must see beside the
numbers, word for word as the page will print them.

## Rules

- Publicly available information only, every source credited. A source with no data licence (Access Now's STOP)
  is `aggregates`: counts and rates, never its rows.
- No probabilities and no cause in COP's voice. A replay says how often a documented outage would have read each
  way, and how often any day would have.
- Large raw downloads (GDELT's event files) stay outside git, on C: (`~/cop-history\`), because
  the D: drive drops on sleep. The manifest says so and names the derived rows that are kept.
