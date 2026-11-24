// Test helpers (not a test file): build the server's data files from COP's fixtures with the real Python builders,
// serve them like Cloudflare's asset binding, and talk to the handler with no network.
import { execFileSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
export const NOW_ISO = "2026-10-31T20:30:00Z"; // the fixtures' own time, as in tests/test_snapshot.py
export const NOW = Date.parse(NOW_ISO);

let built = null;

// tools/snapshot.py over tests/fixtures, then tools/agent_index.py, into a temp folder laid out like site/data/.
export function fixtureData() {
  if (built) return built;
  const dir = mkdtempSync(path.join(tmpdir(), "cop-agent-"));
  const py = process.env.PYTHON || "python";
  const run = (args) => execFileSync(py, args, { cwd: ROOT, stdio: ["ignore", "ignore", "inherit"] });
  run(["tools/snapshot.py", "--fixtures", "tests/fixtures", "--out", dir, "--now", NOW_ISO]);
  run(["tools/agent_index.py", "--data", dir, "--verdict", "tests/fixtures/verdict", "--now", NOW_ISO]);
  // Outage triage, as the build runs it. No base rates here: there is no archive beside the fixtures.
  const out = path.join(dir, "triage-work");
  run(["tools/triage.py", "--snapshot", path.join(dir, "today.geojson"), "--out", out,
    "--rates", path.join(out, "none.json"), "--publish", dir]);
  // The three-signal join, as the build runs it (no archive beside the fixtures, so no base rate).
  run(["tools/three_signal.py", "--snapshot", path.join(dir, "today.geojson"), "--out", path.join(dir, "ts-work"),
    "--publish", dir]);
  // A small direction.json (the real one needs COP's memory): the last pick falls in a Rising place.
  const picks = JSON.parse(readFileSync(path.join(dir, "picks.json"), "utf8")).picks;
  const place = { area: "gulf", name: "Strait of Hormuz and the Gulf", direction: "rising", word: "Rising",
    up: ["GPS interference events", "Security notices"], down: [] };
  writeFileSync(path.join(dir, "direction.json"), JSON.stringify({
    join: { id: "direction", version: "0.1", published: true }, generated_at: NOW_ISO,
    what: "Which places are getting more or less concerning.", not_a_warning: "It is not a warning.",
    windows: { recent_days: 7, prior_days: 14 },
    problems: [{ ...place, press_zone: "11_Iran_Red_Sea", layers: [] }],
    areas: [{ area: "ukraine", name: "Ukraine", direction: "steady", word: "Steady", up: [], down: [], layers: [] }],
    rising: [place.name], easing: [], summary: "Rising: Strait of Hormuz and the Gulf.", record: null,
    picks: picks.length ? [{ pick: picks[picks.length - 1].id, places: [place], rising: true }] : [],
  }));
  built = dir;
  return dir;
}

// fetchAsset for data.js: "/data/agent/index.json" -> <dir>/agent/index.json.
export function diskAssets(dir) {
  return (p) => {
    const file = path.join(dir, p.replace(/^\/data\//, ""));
    if (!existsSync(file)) return new Response("not found", { status: 404 });
    return new Response(readFileSync(file), { headers: { "Content-Type": "application/json" } });
  };
}

export function readJson(dir, rel) {
  return JSON.parse(readFileSync(path.join(dir, rel), "utf8"));
}

// Cloudflare's env, with only the asset binding.
export function fakeEnv(dir) {
  const assets = diskAssets(dir);
  return { ASSETS: { fetch: (req) => assets(new URL(typeof req === "string" ? req : req.url).pathname) } };
}

// A 2025-era request (no envelope): JSON-RPC over POST, answered as JSON or as one SSE message.
export async function legacyCall(fetcher, body, protocolVersion = "2025-11-25") {
  const headers = { "Content-Type": "application/json", Accept: "application/json, text/event-stream" };
  if (body.method !== "initialize") headers["MCP-Protocol-Version"] = protocolVersion;
  const res = await fetcher(new Request("https://cop.test/mcp", { method: "POST", headers, body: JSON.stringify(body) }));
  const text = await res.text();
  if (!text) return { status: res.status, headers: res.headers, message: null };
  const json = (res.headers.get("content-type") || "").includes("text/event-stream")
    ? JSON.parse(text.split("\n").find((l) => l.startsWith("data:")).slice(5))
    : JSON.parse(text);
  return { status: res.status, headers: res.headers, message: json };
}
