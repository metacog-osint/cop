// The Red River Valley view: draws site/data/red-river.json (tools/red_river.py). Text goes in as text, never as HTML.
(function () {
  const T = window.TicTime;
  const $ = (id) => document.getElementById(id);
  const STATE = { fresh: "Fresh", late: "Late", stale: "No current data", missing: "No data" };

  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null) continue;
      if (k === "text") el.textContent = v; else el.setAttribute(k, v);
    }
    for (const k of kids.flat()) if (k != null) el.append(k);
    return el;
  }
  const timeEl = (iso) => (iso ? h("time", { datetime: iso, "data-tic": "datetime" }, iso) : h("span", { text: "unknown" }));
  // A day- or month-precision time is a UTC date and is never moved into the viewer's zone.
  const dataTime = (it) => (it.data_as_of_precision === "day" ? h("span", { text: `${it.data_as_of.slice(0, 10)} (UTC date)` })
    : it.data_as_of_precision === "month" ? h("span", { text: `${it.data_as_of.slice(0, 7)} (UTC month)` }) : timeEl(it.data_as_of));
  const safeLink = (u) => (typeof u === "string" && /^https:\/\//.test(u) ? u : null);
  const expired = (it, now) => { const t = Date.parse(it.valid_until); return Number.isFinite(t) && t < now; };

  function itemRow(it) {
    const link = safeLink(it.link);
    return h("li", {}, h("span", { text: it.title }),
      h("span", { class: "rr-item-meta" },
        `${(it.publishers || []).join(", ") || "Source not named"} · ${it.status_class || it.kind || "report"} · data `, it.data_as_of ? dataTime(it) : "time unknown",
        it.valid_until ? " · valid until " : null, it.valid_until ? timeEl(it.valid_until) : null,
        it.stale ? " · last good copy, not current" : null),
      h("span", { class: "rr-item-meta" },
        h("a", { href: `./#r=1&i=${encodeURIComponent(it.id)}`, text: "On the map" }),
        link ? h("a", { href: link, rel: "noopener", text: "Source" }) : null));
  }

  function layerBox(l, now) {
    const current = l.items.filter((it) => !expired(it, now));
    const past = l.items.length - current.length;
    const notes = [];
    if (l.not_read.length) notes.push(`Not read on the last run: ${l.not_read.join(", ")}.`);
    else if (!l.lanes_known) notes.push("This feed did not report which sources it read.");
    if (past) notes.push(`${past} past its valid-until time, not listed.`);
    return h("section", { class: "rr-layer" },
      h("h3", { text: `${l.label} (${l.name})` }),
      h("p", { class: "rr-meta" }, h("span", { class: `st st-${l.state}`, text: STATE[l.state] || l.state }),
        " · data as of ", timeEl(l.data_as_of), ` · sources: ${l.sources.join(", ")}`),
      notes.length ? h("p", { class: "rr-meta", text: notes.join(" ") }) : null,
      current.length ? h("ul", { class: "rr-items" }, current.map(itemRow))
        : h("p", { class: "rr-empty", text: past && !l.not_read.length && l.state !== "stale" ? `No current entry in this list. ${l.empty_text.replace(/^Nothing placed in [^.]*\. /, "")}` : l.empty_text }));
  }

  async function main() {
    let doc = null;
    try {
      const r = await fetch("data/red-river.json", { cache: "no-cache" });
      if (r.ok) doc = await r.json();
    } catch (e) { doc = null; }
    if (!doc || !Array.isArray(doc.states)) {
      $("states").replaceChildren(h("p", { text: "This view was not built in the current picture, so it shows nothing about either state. That says nothing about conditions there." }));
      $("bar-times").textContent = "The view could not be read.";
      return;
    }
    const now = Date.now();
    $("states").replaceChildren(...doc.states.map((s) => h("section", { class: "rr-state", "aria-labelledby": `st-${s.code}` },
      h("h2", { id: `st-${s.code}`, text: s.name }), s.layers.map((l) => layerBox(l, now)))));
    $("bar-times").replaceChildren("Picture built ", timeEl(doc.snapshot_generated_at));
    $("credits").replaceChildren(h("p", { text: "Cordon, Elements and Blackout, The Intelligence Company. Each entry names its publisher; full credits are on the map page." }));
    if (T) T.renderAll(document);
  }

  if (T) { T.mountSettings($("time-settings")); T.onChange(() => T.renderAll(document)); }
  main();
})();
