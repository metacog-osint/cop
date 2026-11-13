// Test helpers (not a test file): build the server's data files from COP's fixtures with the real Python builders,
// serve them like Cloudflare's asset binding, and talk to the handler with no network.
import { execFileSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync } from "node:fs";
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
