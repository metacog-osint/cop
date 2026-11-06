// Reads the MCP server's data files through Cloudflare's static-asset binding (env.ASSETS), parsing each file once
// per isolate. The files are built by tools/agent_index.py and ship in the same deployment as this code, so a cached
// copy can never be older than the code serving it.
//
// fetchAsset(path) must resolve to a fetch Response; tests pass one that reads files from disk.

export const PATHS = {
  index: "/data/agent/index.json",
  programs: "/data/agent/programs.json",
  verdict: "/data/agent/verdict.json",
  picks: "/data/picks.json",
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
    picks: () => get(PATHS.picks),
    shard: (n) => get(shardPath(n)),
  };
}
