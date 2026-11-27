// Refresh times: draws the `refresh` entries of site/data/health.json (tools/refresh.py). Text goes in as text.
const T = window.TicTime;
const $ = (id) => document.getElementById(id);

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null) continue;
    if (k === "text") el.textContent = v; else el.setAttribute(k, v);
  }
  for (const k of kids.flat()) if (k != null) el.append(k);
  return el;
}
const getJSON = async (u) => { const r = await fetch(u, { cache: "no-cache" }); if (!r.ok) throw new Error(r.status); return r.json(); };
const timeEl = (iso) => (iso ? h("time", { datetime: iso, "data-tic": "datetime" }, iso) : h("span", { text: "unknown" }));
const STATUS = { on_time: "On time", late: "Late", not_read: "Not read", unknown: "No time given" };
const LANE = { fresh: "Fresh", late: "Late", stale: "Stale", failing: "Failing", never: "Never read", up: "Up" };
const BASIS = {
  lanes: "the newest check of any of its sources",
  generated: "the feed's own publication time",
  data: "the end of its newest whole UTC day of data",
};

function every(hours) {
  if (hours < 1) return `every ${Math.round(hours * 60)} minutes`;
  if (hours === 1) return "every hour";
  if (hours === 24) return "once a day";
  return `every ${hours} hours`;
}
function age(hours) {
  if (hours == null) return "";
  if (hours < 1) return `${Math.round(hours * 60)} min before the picture`;
  if (hours < 48) return `${hours.toFixed(1)} h before the picture`;
  return `${(hours / 24).toFixed(1)} days before the picture`;
}

// COP's own picture is judged against the viewer's clock: the others are judged at the picture's time.
function copStatus(cop) {
  const hours = (Date.now() - Date.parse(cop.last_update)) / 3.6e6;
  const late = Number.isFinite(hours) && hours > cop.late_after_hours;
  return h("td", { class: `rf-status rf-${late ? "late" : "on_time"}`, text: late ? "Late now" : "On time now",
    title: `Late when older than ${cop.late_after_hours} h by your clock` });
}
function programTable(health) {
  const names = Object.fromEntries((health.config.programs || []).map((p) => [p.id, p.name]));
  const cop = health.refresh.cop;
  const rows = [h("tr", {},
    h("th", { scope: "row", text: "COP (this picture)" }),
    h("td", { text: `${every(cop.every_hours)}: ${cop.runs} (UTC)` }), h("td", { text: cop.scheduler }),
    h("td", {}, timeEl(cop.last_update)), copStatus(cop))];
  for (const p of health.programs) {
    const r = p.refresh;
    if (!r) continue;
    rows.push(h("tr", {},
      h("th", { scope: "row" }, h("a", { href: `#rf-${p.id}`, text: names[p.id] || p.name || p.id })),
      h("td", { text: `${every(r.every_hours)}: ${r.runs} (UTC)` }), h("td", { text: r.scheduler }),
      h("td", {}, timeEl(r.last_update), r.age_hours != null ? h("span", { class: "rf-meta", text: ` · ${age(r.age_hours)}` }) : null),
      h("td", { class: `rf-status rf-${r.status}`, text: STATUS[r.status] || r.status,
        title: r.status === "late" ? `Older than ${r.late_after_hours} h` : r.status === "on_time" ? `Within ${r.late_after_hours} h` : null })));
  }
  return h("div", { class: "doc-table-wrap" }, h("table", { class: "doc-table rf-table" },
    h("caption", { text: "Each program: its schedule, its last update and whether it is late" }),
    h("thead", {}, h("tr", {}, ["Program", "Expected", "Started by", "Last update", "On its schedule"].map((c) => h("th", { scope: "col", text: c })))),
    h("tbody", {}, rows)));
}

function laneBlock(p, names, states) {
  const r = p.refresh;
  const lanes = Object.entries(r.lanes || {});
  const name = names[p.id] || p.name || p.id;
  const kids = [h("summary", {}, h("strong", { text: name }),
    h("span", { class: "rf-meta", text: lanes.length ? ` · ${lanes.length} source${lanes.length === 1 ? "" : "s"}` : " · its feed lists no sources" }))];
  kids.push(h("p", { class: "rr-meta", text: `${name}'s last update is ${BASIS[r.last_update_basis] || "unknown"}.`
    + (r.status === "not_read" ? " COP could not read its feed in this picture, so no time is shown." : "") }));
  if (lanes.length) {
    kids.push(h("div", { class: "doc-table-wrap" }, h("table", { class: "doc-table rf-lanes" },
      h("thead", {}, h("tr", {}, ["Source", "State", "Checked", "Last read well", "Newest data", "Detail"].map((c) => h("th", { scope: "col", text: c })))),
      h("tbody", {}, lanes.map(([id, l]) => h("tr", {},
        h("th", { scope: "row", text: id }),
        h("td", { class: `rf-lane rf-lane-${l.state}`, text: LANE[l.state] || l.state || "?", title: states[l.state] || null }),
        h("td", {}, timeEl(l.checked_at)), h("td", {}, l.last_ok ? timeEl(l.last_ok) : h("span", { text: "never" })),
        h("td", {}, l.data_as_of ? timeEl(l.data_as_of) : h("span", { text: "not given" })),
        h("td", { text: l.detail || "" })))))));
  }
  return h("details", { class: "rf-program", id: `rf-${p.id}` }, kids);
}

function method(m) {
  const states = m.lane_states || {};
  return h("section", { class: "doc-box", "aria-labelledby": "rf-how-h" },
    h("h2", { id: "rf-how-h", text: "How to read it" }),
    h("ul", {},
      h("li", {}, h("strong", { text: "Expected. " }), m.basis),
      h("li", {}, h("strong", { text: "Last update. " }), "Read from each program's own feed: the newest time it checked any of its sources where the feed lists them (some feeds are served live, so their own time is the moment COP asked), else the feed's publication time, else, for Watchfloor, the end of its newest whole UTC day."),
      h("li", {}, h("strong", { text: "Late. " }), m.late_rule, " On time is the opposite. Not read: COP could not fetch the feed for this picture."),
      h("li", {}, h("strong", { text: "Each source's state " }), "is the program's own, judged against that source's own window; COP restates it and judges nothing more:",
        h("ul", {}, Object.entries(states).map(([k, v]) => h("li", {}, h("strong", { text: `${LANE[k] || k}: ` }), v))))),
    h("p", { class: "rr-meta", text: "A late program or source is a statement about timing only: it says nothing about what is happening in the world, and a quiet layer is never an all-clear." }));
}

async function main() {
  const health = await getJSON("data/health.json");
  if (!health.refresh) {
    $("body").replaceChildren(h("p", { class: "note", text: "This picture was built before COP kept refresh times. That says nothing about any program." }));
    return;
  }
  const names = Object.fromEntries((health.config.programs || []).map((p) => [p.id, p.name]));
  const states = health.refresh.method.lane_states || {};
  $("body").replaceChildren(
    h("p", { class: "rr-meta" }, "COP's picture built ", timeEl(health.generated_at), "."),
    programTable(health),
    h("h2", { text: "Sources, program by program" }),
    ...health.programs.filter((p) => p.refresh).map((p) => laneBlock(p, names, states)),
    method(health.refresh.method));
  $("bar-times").replaceChildren("Picture built ", timeEl(health.generated_at));
}

if (T) { T.mountSettings($("time-settings")); T.onChange(() => T.renderAll(document)); }
main().catch(() => {
  $("body").replaceChildren(h("p", { class: "note", text: "COP's health file could not be read, so no refresh times are shown. That says nothing about any program." }));
}).finally(() => { if (T) T.renderAll(document); });
