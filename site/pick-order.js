// The order of today's picks, the same rule as the MCP server's top_picks (mcp/tools.js topPicks): a pick in a place
// read as Rising comes first, the rest keep the build's order, and no other direction word moves a pick.
// direction is data/direction.json, or null when it could not be read: then the build's order stands.
export function orderPicks(picks, direction) {
  const places = new Map(((direction && direction.picks) || []).filter((x) => x.rising).map((x) => [x.pick, x.places]));
  return picks
    .map((p) => ({ ...p, picked: p.n, risingIn: (places.get(p.id) || []).filter((x) => x.direction === "rising") }))
    .sort((a, b) => Number(places.has(b.id)) - Number(places.has(a.id)) || a.picked - b.picked)
    .map((p, i) => ({ ...p, n: i + 1, reason: p.risingIn.length ? `${p.reason}. ${risingNote(p)}` : p.reason }));
}

// The line a pick in a Rising place carries, or an empty string.
export function risingNote(pick) {
  if (!pick.risingIn || !pick.risingIn.length) return "";
  const names = pick.risingIn.map((x) => `${x.name} (${x.up.join(", ")} up)`).join("; ");
  return `Listed first: it falls in a place COP reads as Rising: ${names}`;
}
