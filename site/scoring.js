// How COP scores: fills the page's live figures from the picture's own files (health.json, triage.json,
// three-signal.json), so the strength rules, freshness windows and base rates always match the map. The rules
// themselves are written in scoring.html; tests/test_snapshot.py checks its thresholds against the code.
const T = window.TicTime;
const $ = (id) => document.getElementById(id);

function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null) continue;
    if (k === "text") el.textContent = v; else el.setAttribute(k, v);
  }
  for (const k of kids.flat()) if (k != null) el.append(k);
  return el;
}
const timeEl = (iso) => (iso ? h("time", { datetime: iso, "data-tic": "datetime" }, iso) : h("span", { text: "unknown" }));
const pct = (x) => (typeof x === "number" ? `${(x * 100).toFixed(1)}%` : "n/a");
const hours = (n) => (typeof n !== "number" ? "n/a" : n % 24 === 0 && n >= 48 ? `${n / 24} days` : `${n} hours`);
const get = (name) => fetch(`data/${name}`, { cache: "no-cache" }).then((r) => (r.ok ? r.json() : null)).catch(() => null);
const STATE = { fresh: "Fresh", late: "Late", stale: "Stale", missing: "No data" };

function fill(tbody, rows) {
  tbody.replaceChildren(...rows);
}

function strengthTable(health) {
  const tbody = $("strength-table").tBodies[0];
  if (!health || !health.config) return fill(tbody, [h("tr", {}, h("td", { colspan: "2", text: "The picture's configuration could not be read." }))]);
  fill(tbody, health.config.programs.map((p) => {
    const labels = (p.strength_labels || []).map((l) => l.replace("{v}", "x"));
    const admits = (p.pick_admits || []).map((w) => `${w}.`).join(" ");
    return h("tr", {}, h("th", { scope: "row", text: p.name }),
      h("td", { text: (labels.length ? labels.map((l, i) => (labels.length > 1 ? `${i + 1}. ${l}` : l)).join("; ")
        : "No rule: its feed's own order (it makes no picks if it never marks an anomaly).") + (admits ? ` ${admits}` : "") }));
  }));
  if (Number.isFinite(health.config.max_picks)) $("max-picks").textContent = String(health.config.max_picks);
}

function freshTable(health) {
  const tbody = $("fresh-table").tBodies[0];
  if (!health || !health.programs) return fill(tbody, [h("tr", {}, h("td", { colspan: "4", text: "The picture's health could not be read." }))]);
  fill(tbody, health.programs.map((p) => h("tr", {},
    h("th", { scope: "row", text: p.name }), h("td", { text: hours(p.fresh_hours) }), h("td", { text: hours(p.stale_hours) }),
    h("td", { class: `st st-${p.state}`, text: `${STATE[p.state] || p.state}${typeof p.age_hours === "number" ? `, ${p.age_hours} h old` : ""}` }))));
}

function shareList(shares) {
  return ["natural", "tension context", "open", "incomplete"].filter((k) => k in (shares || {}))
    .map((k) => `${k} ${pct(shares[k])}`).join(", ");
}

function triageRates(t) {
  const box = $("triage-rates");
  if (!t) return box.replaceChildren(h("p", { text: "Outage triage was not built in this picture, so its base rates are not shown." }));
  const out = [];
  const b = t.base_rates;
  if (b && b.would_read) {
    out.push(h("p", {}, `On COP's own archive (${b.counted_days} counted days, ${b.country_days.toLocaleString("en")} country-days across `
      + `${b.countries} countries), an outage on any day would have read: ${shareList(b.would_read)}. `
      + (b.signal_countries_would_read ? `On the days of the ${b.countries_with_signal} countries that showed any signal: ${shareList(b.signal_countries_would_read)}.` : "")));
    for (const c of b.cautions || []) out.push(h("p", { class: "note", text: c }));
  } else {
    out.push(h("p", { text: "No base rates in this picture: the archive could not be read." }));
  }
  const r = t.replay;
  if (r && r.two_layer) {
    const row = (k, x) => h("tr", {}, h("th", { scope: "row", text: k }),
      h("td", { text: `${shareList(x.shutdown_shares)} (${x.matched_shutdowns} shutdowns)` }),
      h("td", { text: `${shareList(x.ordinary_day_shares)} (${x.ordinary_days.toLocaleString("en")} days)` }));
    out.push(h("p", {}, `Replayed on documented 2025 shutdowns (${r.attribution}): how often a shutdown would have read each way, `
      + "beside the same countries' ordinary days. Triage does not detect shutdowns; this says how much its readings differ on them."),
    h("div", { class: "doc-table-wrap" }, h("table", { class: "doc-table" },
      h("thead", {}, h("tr", {}, h("th", { scope: "col", text: "Layers replayed" }), h("th", { scope: "col", text: "On shutdowns" }), h("th", { scope: "col", text: "On ordinary days" }))),
      h("tbody", {}, row("Hazards and attention", r.two_layer), r.three_layer ? row("Plus Cordon's notices (its 25 countries)", r.three_layer) : null))));
    if (r.caution && !(b && (b.cautions || []).includes(r.caution))) out.push(h("p", { class: "note", text: r.caution }));
  }
  out.push(h("p", { class: "note" }, "Windows as built: ",
    t.windows ? `hazard ${t.windows.hazard_before_hours} h before to ${t.windows.hazard_after_hours} h after, notice ${t.windows.notice_recent_hours} h, `
      + `episodic lag ${t.windows.episodic_lag_hours} h, attention baseline ${t.windows.attention_baseline}` + (t.windows.provisional ? " (provisional)." : ".") : "not stated.",
    " Snapshot ", timeEl(t.snapshot_generated_at), "."));
  box.replaceChildren(...out);
}

function threeRates(s) {
  const box = $("three-rates");
  const rec = s && s.records && s.records[0];
  if (!rec) return box.replaceChildren(h("p", { text: "The three-signal join was not built in this picture, so its base rate is not shown." }));
  const b = rec.base_rate;
  box.replaceChildren(
    h("p", { text: b ? b.text : "No archive was given, so there is no base rate yet." }),
    b && b.caution ? h("p", { class: "note", text: b.caution }) : null,
    h("p", { class: "note" }, `Now: ${rec.reading}. Snapshot `, timeEl(s.snapshot_generated_at), "."));
}

async function main() {
  const [health, triage, three] = await Promise.all([get("health.json"), get("triage.json"), get("three-signal.json")]);
  strengthTable(health);
  freshTable(health);
  triageRates(triage);
  threeRates(three);
  const bar = $("bar-times");
  bar.replaceChildren(...(health && health.generated_at ? ["Figures from the picture built ", timeEl(health.generated_at)] : ["The live figures could not be read."]));
  if (T) T.renderAll(document);
}

if (T) { T.mountSettings($("time-settings")); T.onChange(() => T.renderAll(document)); }
main();
