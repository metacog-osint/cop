// Cloudflare Pages Function for https://cop.theintelligence.company/mcp: COP's MCP server (logic in ../mcp/).
// Only this route runs code; every other COP address is a static file.
import { handle } from "../mcp/server.js";

export function onRequest(context) {
  return handle(context.request, context.env);
}
