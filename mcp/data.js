// Reads the MCP server's data files through Cloudflare's static-asset binding (env.ASSETS), parsing each file once
// per isolate. The files are built by tools/agent_index.py and ship in the same deployment as this code, so a cached
// copy can never be older than the code serving it.
//
// fetchAsset(path) must resolve to a fetch Response; tests pass one that reads files from disk.

export const PATHS = {
  index: "/data/agent/index.json",
  programs: "/data/agent/programs.json",
  verdict: "/data/agent/verdict.json",
  events: "/data/agent/events.json", // the programs' own dated events (Orbit's scheduled launches)
  picks: "/data/picks.json",
  triage: "/data/triage.json", // tools/triage.py --publish; absent when that step failed
  threeSignal: "/data/three-signal.json", // tools/three_signal.py --publish; absent when that step failed
  direction: "/data/direction.json", // tools/direction.py --publish; absent when that step failed
  lanes: "/data/lanes.json", // tools/lane_log.py --publish; absent when that step failed
};

export function shardPath(n) {
  return `/data/agent/items/${String(n).padStart(2, "0")}.json`;
}

export function makeData(fetchAsset) {
  const cache = new Map();
  const get = (path) => {
    let p = cache.get(path);
    if (!p) {
      p = Promise.resolve(fetchAsset(path)).then((r) => {
        if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
        return r.json();
      });
      cache.set(path, p);
      p.catch(() => cache.delete(path)); // a failed read is retried on the next request
    }
    return p;
  };
  return {
    index: () => get(PATHS.index),
    programs: () => get(PATHS.programs),
    verdict: () => get(PATHS.verdict),
    events: () => get(PATHS.events).catch(() => null), // Verdict's calendar stands without it
    picks: () => get(PATHS.picks),
    triage: () => get(PATHS.triage).catch(() => null), // the picture stands without it
    threeSignal: () => get(PATHS.threeSignal).catch(() => null), // so does the three-signal join
    direction: () => get(PATHS.direction).catch(() => null), // and the direction of concern
    lanes: () => get(PATHS.lanes).catch(() => null), // and the lane records
    shard: (n) => get(shardPath(n)),
  };
}
