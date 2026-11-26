// Protocol tests for COP's MCP server: the official v2 client (protocol 2026-07-28), hand-written 2025-era requests,
// and the Cloudflare wrapper (CORS, the help page). Run with `node --test mcp/` from COP's root. No network.
import { test } from "node:test";
import assert from "node:assert/strict";
import { Client, StreamableHTTPClientTransport } from "@modelcontextprotocol/client";
import { makeHandler, handle, INSTRUCTIONS, VERSION } from "./server.js";
import { makeData } from "./data.js";
import { NOW, diskAssets, fakeEnv, fixtureData, legacyCall } from "./testkit.js";

const dir = fixtureData();
const clock = () => NOW;
const handler = makeHandler(makeData(diskAssets(dir)), clock);
const ORDER = [
  "suite_status",
  "search_items",
  "get_item",
  "top_picks",
  "outage_triage",
  "list_forecasts",
  "forecast_record",
  "upcoming_events",
  "three_signal",
  "direction",
  "lane_record",
  "history",
];

async function client() {
  const transport = new StreamableHTTPClientTransport(new URL("https://cop.test/mcp"), {
    fetch: (url, init) => handler.fetch(new Request(url, init)),
  });
  const c = new Client({ name: "cop-test", version: "0" });
  await c.connect(transport);
  return c;
}

const body = (r) => r.structuredContent ?? JSON.parse(r.content[0].text);

test("a 2026-07-28 client lists the twelve tools in a fixed order, all read-only", async () => {
  const c = await client();
  const { tools } = await c.listTools();
  assert.deepEqual(tools.map((t) => t.name), ORDER);
  for (const t of tools) {
    assert.equal(t.annotations.readOnlyHint, true, t.name);
    assert.equal(t.annotations.destructiveHint, false, t.name);
    assert.ok(t.description.length > 40, t.name);
    assert.equal(t.inputSchema.type, "object", t.name);
  }
  const again = await c.listTools();
  assert.deepEqual(again.tools.map((t) => t.name), ORDER);
  await c.close();
});

test("the server names itself and gives its instructions", async () => {
  const c = await client();
  const info = c.getServerVersion();
  assert.equal(info.name, "cop");
  assert.equal(info.version, VERSION);
  assert.equal(c.getInstructions(), INSTRUCTIONS);
  await c.close();
});

test("every tool answers through the client", async () => {
  const c = await client();
  const status = body(await c.callTool({ name: "suite_status", arguments: {} }));
  assert.ok(status.programs.some((p) => p.status === "planned"));

  const search = body(await c.callTool({ name: "search_items", arguments: { program: ["cordon"], limit: 3 } }));
  assert.equal(search.returned, 3);
  assert.ok(search.items.every((r) => r.program === "cordon"));

  const item = body(await c.callTool({ name: "get_item", arguments: { id: search.items[0].id } }));
  assert.equal(item.item.properties.id, search.items[0].id);

  const picks = body(await c.callTool({ name: "top_picks", arguments: {} }));
  assert.ok(picks.picks.length > 0);

  const triage = body(await c.callTool({ name: "outage_triage", arguments: {} }));
  assert.equal(triage.join, "outage-triage");
  assert.equal(triage.count, 2);
  assert.match(triage.note, /not a warning, not a forecast/);
  const one = body(await c.callTool({ name: "outage_triage", arguments: { country: "tn" } }));
  assert.deepEqual(one.records.map((r) => r.country), ["TN"]);

  const ts = body(await c.callTool({ name: "three_signal", arguments: {} }));
  assert.equal(ts.join, "three-signal");
  assert.deepEqual(ts.records.map((r) => r.area), ["hormuz"]);
  assert.match(ts.note, /not a warning, a forecast or a finding of cause/);

  const dir_ = body(await c.callTool({ name: "direction", arguments: { area: "gulf" } }));
  assert.equal(dir_.join, "direction");
  assert.equal(dir_.problems[0].word, "Rising");
  assert.ok(picks.picks[0].in_rising_place, "top_picks reads direction.json");

  const lanes = body(await c.callTool({ name: "lane_record", arguments: {} }));
  assert.equal(lanes.min_scored_for_rate, 30);
  assert.match(lanes.note, /not a warning and gives no likelihood/);
  const tri = lanes.lanes.find((x) => x.id === "blackout-outage-triage");
  assert.ok(tri.flagged > 0, "the fixtures' outages are logged");
  assert.equal(tri.scored, 0);
  assert.match(tri.replay.text, /^Replay, not live/);
  assert.equal(lanes.lanes.find((x) => x.id === "elements").flagged, null);
  for (const p of picks.picks) assert.ok(p.lane_record && p.lane_record.lane, `pick ${p.id} carries its lane`);
  const one_ = body(await c.callTool({ name: "lane_record", arguments: { lane: "cop-direction" } }));
  assert.deepEqual(one_.lanes.map((x) => x.id), ["cop-direction"]);
  const bad = await c.callTool({ name: "lane_record", arguments: { lane: "no-such-lane" } });
  assert.equal(bad.isError, true);

  const forecasts = body(await c.callTool({ name: "list_forecasts", arguments: { status: "open" } }));
  assert.equal(forecasts.count, 8);

  const record = body(await c.callTool({ name: "forecast_record", arguments: {} }));
  assert.equal(record.scores.resolved, 0);

  const events = body(await c.callTool({ name: "upcoming_events", arguments: { days: 60 } }));
  assert.ok(events.count > 0);
  const launches = body(await c.callTool({ name: "upcoming_events", arguments: { origin: "orbit" } }));
  assert.ok(launches.count > 0 && launches.events.every((e) => e.origin === "orbit"));
  await c.close();
});

test("a miss or a bad filter comes back as a tool error, not a crash", async () => {
  const c = await client();
  const miss = await c.callTool({ name: "get_item", arguments: { id: "nope:1" } });
  assert.equal(miss.isError, true);
  assert.match(miss.content[0].text, /No item "nope:1"/);
  const unknown = await c.callTool({ name: "search_items", arguments: { program: ["tempo"] } });
  assert.equal(unknown.isError, true);
  assert.match(unknown.content[0].text, /Unknown program/);
  const tooMany = await c.callTool({ name: "search_items", arguments: { limit: 500 } }).catch((e) => ({ thrown: e }));
  assert.ok(tooMany.thrown || tooMany.isError, "a limit over 50 is refused");
  await c.close();
});

test("a 2025-era client is served statelessly from the same tools", async () => {
  const f = (req) => handler.fetch(req);
  const init = await legacyCall(f, {
    jsonrpc: "2.0",
    id: 1,
    method: "initialize",
    params: { protocolVersion: "2025-11-25", capabilities: {}, clientInfo: { name: "old-client", version: "1" } },
  });
  assert.equal(init.status, 200);
  assert.equal(init.message.result.protocolVersion, "2025-11-25");
  assert.equal(init.message.result.serverInfo.name, "cop");
  assert.equal(init.message.result.instructions, INSTRUCTIONS);
  assert.equal(init.headers.get("mcp-session-id"), null, "stateless: no session");

  const list = await legacyCall(f, { jsonrpc: "2.0", id: 2, method: "tools/list" });
  assert.deepEqual(list.message.result.tools.map((t) => t.name), ORDER);

  const call = await legacyCall(f, {
    jsonrpc: "2.0",
    id: 3,
    method: "tools/call",
    params: { name: "search_items", arguments: { tier: "anomaly", limit: 2 } },
  });
  const res = call.message.result;
  assert.notEqual(res.isError, true);
  assert.ok(body(res).items.every((r) => r.tier === "anomaly"));
});

test("the Cloudflare wrapper adds CORS, answers preflight and shows people a help page", async () => {
  const env = fakeEnv(dir);
  const pre = await handle(new Request("https://cop.test/mcp", { method: "OPTIONS" }), env, clock);
  assert.equal(pre.status, 204);
  assert.equal(pre.headers.get("access-control-allow-origin"), "*");

  const page = await handle(new Request("https://cop.test/mcp", { headers: { Accept: "text/html" } }), env, clock);
  assert.equal(page.status, 200);
  assert.match(await page.text(), /claude mcp add --transport http cop https:\/\/cop\.theintelligence\.company\/mcp/);

  const res = await handle(
    new Request("https://cop.test/mcp", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json, text/event-stream" },
      body: JSON.stringify({
        jsonrpc: "2.0",
        id: 1,
        method: "initialize",
        params: { protocolVersion: "2025-11-25", capabilities: {}, clientInfo: { name: "x", version: "1" } },
      }),
    }),
    env,
    clock,
  );
  assert.equal(res.status, 200);
  assert.equal(res.headers.get("access-control-allow-origin"), "*");
  assert.equal(res.headers.get("x-robots-tag"), "noindex");
});
