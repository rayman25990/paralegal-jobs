"""Render docs/index.html from the job store.

The page is a single static file: job data is embedded as JSON and a small
script handles sorting and the (all off by default) filters.
"""

from __future__ import annotations

import json

PAGE_FIELDS = ("id", "title", "company", "location", "salary", "contract_type", "hours", "practice_area",
               "url", "links", "sources", "posted", "first_seen", "score", "score_reason", "policy_focus")


def _embed_json(value) -> str:
    # Safe inside <script type="application/json">: no "</script>" or HTML comment openers.
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))


def render_dashboard(store: dict) -> str:
    meta = store.get("meta", {})
    payload = {
        "meta": {k: meta.get(k) for k in ("last_run", "previous_run", "new_this_run", "total", "sources", "search")},
        "jobs": [{k: job.get(k) for k in PAGE_FIELDS} for job in store.get("jobs", [])],
    }
    return TEMPLATE.replace("__DATA__", _embed_json(payload))


TEMPLATE = r"""<!doctype html>
<html lang="en-GB">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>London Paralegal Jobs</title>
<meta name="description" content="Every paralegal role within 10 miles of London from Reed and Adzuna, refreshed every 3 hours.">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>⚖️</text></svg>">
<style>
:root {
  --bg: #f6f5f1; --surface: #ffffff; --text: #1d1d1f; --muted: #5f6368; --border: #e2e0da;
  --accent: #1f4e79; --accent-soft: #e6eef6; --new: #0b7a3e; --new-soft: #e3f4ea;
  --policy: #7a3e9d; --policy-soft: #f2e8f8; --star: #b7791f;
}
@media (prefers-color-scheme: dark) {
  :root { --bg: #121315; --surface: #1c1d20; --text: #ececec; --muted: #a0a4ab; --border: #2e3035;
    --accent: #8cb8e8; --accent-soft: #1f2a36; --new: #5fd394; --new-soft: #17301f;
    --policy: #d3a6f0; --policy-soft: #2e2236; --star: #f2c14e; }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text);
  font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
.wrap { max-width: 960px; margin: 0 auto; padding: 16px; }
header h1 { font-size: 1.5rem; margin: 8px 0 4px; }
.sub { color: var(--muted); font-size: .9rem; margin: 0; }
.stats { display: flex; flex-wrap: wrap; gap: 8px; margin: 12px 0 0; }
.stat { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 6px 12px; font-size: .9rem; }
.stat b { font-size: 1.05rem; }
.controls { position: sticky; top: 0; z-index: 2; background: var(--bg); padding: 12px 0;
  display: flex; flex-wrap: wrap; gap: 8px 14px; align-items: center; border-bottom: 1px solid var(--border); margin-top: 12px; }
.controls label { display: inline-flex; align-items: center; gap: 6px; font-size: .9rem; color: var(--muted); }
select, button { font: inherit; color: var(--text); background: var(--surface); border: 1px solid var(--border);
  border-radius: 8px; padding: 6px 8px; min-height: 36px; }
.seg { display: inline-flex; border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
.seg button { border: 0; border-radius: 0; background: var(--surface); padding: 6px 12px; cursor: pointer; }
.seg button[aria-pressed="true"] { background: var(--accent); color: var(--surface); }
input[type=checkbox] { width: 18px; height: 18px; accent-color: var(--accent); }
#reset { cursor: pointer; }
.count { color: var(--muted); font-size: .85rem; margin-left: auto; }
section h2 { font-size: 1.1rem; margin: 22px 0 10px; display: flex; align-items: baseline; gap: 8px; }
section h2 small { color: var(--muted); font-weight: normal; font-size: .85rem; }
.list { display: grid; gap: 10px; }
.card { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 14px 16px; }
.card.is-new { border-left: 4px solid var(--new); }
.card h3 { font-size: 1.02rem; margin: 0 0 2px; line-height: 1.35; }
.card h3 a { color: var(--accent); text-decoration: none; }
.card h3 a:hover { text-decoration: underline; }
.company { color: var(--muted); font-size: .9rem; margin: 0 0 8px; }
.badges { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 8px; }
.badge { font-size: .75rem; font-weight: 600; padding: 2px 8px; border-radius: 999px; background: var(--accent-soft); color: var(--accent); }
.badge.new { background: var(--new-soft); color: var(--new); }
.badge.policy { background: var(--policy-soft); color: var(--policy); }
.score { color: var(--star); letter-spacing: 1px; }
dl.facts { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 4px 16px; margin: 0 0 8px; font-size: .9rem; }
dl.facts div { min-width: 0; }
dl.facts dt { color: var(--muted); font-size: .75rem; text-transform: uppercase; letter-spacing: .04em; }
dl.facts dd { margin: 0; overflow-wrap: anywhere; }
.reason { font-size: .85rem; color: var(--muted); margin: 0 0 8px; }
.foot { display: flex; flex-wrap: wrap; gap: 6px 14px; align-items: center; font-size: .85rem; color: var(--muted); }
.apply { display: inline-block; background: var(--accent); color: var(--surface); text-decoration: none; padding: 6px 12px;
  border-radius: 8px; font-weight: 600; }
.foot a.alt { color: var(--accent); }
.empty { color: var(--muted); background: var(--surface); border: 1px dashed var(--border); border-radius: 12px; padding: 14px 16px; margin: 0; }
footer { color: var(--muted); font-size: .8rem; margin: 28px 0 12px; }
footer code { font-size: .8rem; }
@media (max-width: 560px) {
  .count { margin-left: 0; width: 100%; }
  dl.facts { grid-template-columns: 1fr 1fr; }
}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>London Paralegal Jobs</h1>
    <p class="sub">Every paralegal role within 10 miles of London from Reed and Adzuna, all practice areas. Refreshed every 3 hours.</p>
    <div class="stats">
      <span class="stat">Last updated <b id="updated">–</b></span>
      <span class="stat"><b id="total">0</b> jobs</span>
      <span class="stat"><b id="newcount">0</b> new since last run</span>
    </div>
  </header>

  <div class="controls" role="group" aria-label="Sort and filter">
    <span class="seg" role="group" aria-label="Sort">
      <button type="button" data-sort="newest" aria-pressed="true">Newest</button>
      <button type="button" data-sort="fit" aria-pressed="false">Best fit</button>
    </span>
    <label><input type="checkbox" id="f-policy"> Policy focus only</label>
    <label>Contract <select id="f-contract"><option value="">Any</option></select></label>
    <label>Min score <select id="f-score">
      <option value="0">Any</option><option value="2">2+</option><option value="3">3+</option>
      <option value="4">4+</option><option value="5">5</option></select></label>
    <button type="button" id="reset" hidden>Clear filters</button>
    <span class="count" id="count"></span>
  </div>

  <section aria-labelledby="h-new">
    <h2 id="h-new">New since last run <small id="new-note"></small></h2>
    <div class="list" id="new-list"></div>
  </section>

  <section aria-labelledby="h-all">
    <h2 id="h-all">All jobs <small>last 30 days</small></h2>
    <div class="list" id="all-list"></div>
  </section>

  <footer>
    <p>Scores (1–5) are a preference signal only; no job is hidden by default. Every job starts at 3: up to +2 for a
    public-sector or policy angle, +1 for career-friendly signals (future trainee, SQE, graduate, part-time, temporary,
    hybrid), −1 if it asks for 2+ years' experience.</p>
    <p id="sources"></p>
  </footer>
</div>

<script type="application/json" id="data">__DATA__</script>
<script>
(function () {
  "use strict";
  var DATA = JSON.parse(document.getElementById("data").textContent);
  var meta = DATA.meta || {};
  var jobs = DATA.jobs || [];
  var state = { sort: "newest", policy: false, contract: "", minScore: 0 };

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === "text") node.textContent = attrs[k];
      else if (k === "class") node.className = attrs[k];
      else node.setAttribute(k, attrs[k]);
    });
    (children || []).forEach(function (c) { if (c) node.appendChild(c); });
    return node;
  }

  function fmtDate(iso, withTime) {
    if (!iso) return "–";
    var d = new Date(iso.length === 10 ? iso + "T12:00:00Z" : iso);
    if (isNaN(d)) return iso;
    var opts = { day: "numeric", month: "short", timeZone: "Europe/London" };
    if (withTime) { opts.hour = "2-digit"; opts.minute = "2-digit"; }
    return d.toLocaleString("en-GB", opts);
  }

  function ago(iso) {
    var mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
    if (isNaN(mins)) return "";
    if (mins < 1) return "just now";
    if (mins < 60) return mins + " min ago";
    var hrs = Math.round(mins / 60);
    if (hrs < 48) return hrs + " hr" + (hrs === 1 ? "" : "s") + " ago";
    return Math.round(hrs / 24) + " days ago";
  }

  function safeUrl(url) { return /^https?:\/\//i.test(url || "") ? url : "#"; }

  function isNew(job) { return meta.previous_run && job.first_seen === meta.last_run; }

  function postedKey(job) { return (job.posted || job.first_seen.slice(0, 10)) + "|" + job.first_seen; }

  function compare(a, b) {
    if (state.sort === "fit" && b.score !== a.score) return b.score - a.score;
    var ka = postedKey(a), kb = postedKey(b);
    return ka < kb ? 1 : ka > kb ? -1 : 0;
  }

  function matches(job) {
    if (state.policy && !job.policy_focus) return false;
    if (state.contract && job.contract_type !== state.contract) return false;
    if (job.score < state.minScore) return false;
    return true;
  }

  function card(job) {
    var badges = [];
    if (isNew(job)) badges.push(el("span", { class: "badge new", text: "New" }));
    if (job.policy_focus) badges.push(el("span", { class: "badge policy", text: "Policy focus" }));
    badges.push(el("span", { class: "badge", text: job.practice_area }));

    var stars = "★★★★★".slice(0, job.score) + "☆☆☆☆☆".slice(0, 5 - job.score);
    var contract = job.contract_type + (job.hours ? " · " + job.hours : "");
    function fact(label, value, extraClass) {
      var dd = el("dd", { text: value });
      if (extraClass) dd.className = extraClass;
      return el("div", {}, [el("dt", { text: label }), dd]);
    }

    var foot = [el("a", { class: "apply", href: safeUrl(job.url), target: "_blank", rel: "noopener", text: "View job →" })];
    Object.keys(job.links || {}).forEach(function (src) {
      if (job.links[src] && job.links[src] !== job.url) {
        foot.push(el("a", { class: "alt", href: safeUrl(job.links[src]), target: "_blank", rel: "noopener", text: "Also on " + src }));
      }
    });
    foot.push(el("span", { text: "Posted " + fmtDate(job.posted || job.first_seen) + " · via " + (job.sources || []).join(" + ") }));

    return el("article", { class: "card" + (isNew(job) ? " is-new" : "") }, [
      el("h3", {}, [el("a", { href: safeUrl(job.url), target: "_blank", rel: "noopener", text: job.title || "Untitled role" })]),
      el("p", { class: "company", text: job.company || "Employer not stated" }),
      el("div", { class: "badges" }, badges),
      el("dl", { class: "facts" }, [
        fact("Salary", job.salary),
        fact("Location", job.location || "London"),
        fact("Contract", contract),
        fact("Fit score", stars + " " + job.score + "/5", "score")
      ]),
      el("p", { class: "reason", text: job.score_reason }),
      el("div", { class: "foot" }, foot)
    ]);
  }

  function fill(container, list, emptyText) {
    container.replaceChildren();
    if (!list.length) { container.appendChild(el("p", { class: "empty", text: emptyText })); return; }
    var frag = document.createDocumentFragment();
    list.forEach(function (job) { frag.appendChild(card(job)); });
    container.appendChild(frag);
  }

  function filtersActive() { return state.policy || state.contract || state.minScore > 0; }

  function render() {
    var visible = jobs.filter(matches).sort(compare);
    var fresh = visible.filter(isNew);
    var newNote = document.getElementById("new-note");
    if (!meta.previous_run) {
      newNote.textContent = "";
      fill(document.getElementById("new-list"), [], meta.last_run
        ? "This was the first run, so every job is new. Fresh postings will appear here after the next run."
        : "Waiting for the first run.");
    } else {
      newNote.textContent = "since " + fmtDate(meta.previous_run, true);
      fill(document.getElementById("new-list"), fresh,
        filtersActive() ? "No new jobs match these filters." : "No new jobs since the last run.");
    }
    fill(document.getElementById("all-list"), visible,
      jobs.length ? "No jobs match these filters." : "No jobs yet. Run the workflow to fetch the first batch.");
    document.getElementById("count").textContent = "Showing " + visible.length + " of " + jobs.length;
    document.getElementById("reset").hidden = !filtersActive();
    document.querySelectorAll("[data-sort]").forEach(function (b) {
      b.setAttribute("aria-pressed", String(b.getAttribute("data-sort") === state.sort));
    });
  }

  // Header
  document.getElementById("total").textContent = jobs.length;
  document.getElementById("newcount").textContent = meta.previous_run ? jobs.filter(isNew).length : jobs.length;
  if (meta.last_run) {
    var upd = document.getElementById("updated");
    upd.textContent = fmtDate(meta.last_run, true) + " (" + ago(meta.last_run) + ")";
    upd.title = meta.last_run;
  }
  var src = meta.sources || {};
  document.getElementById("sources").textContent = Object.keys(src).length
    ? "Last run: " + Object.keys(src).map(function (k) { return k + " " + src[k]; }).join(" · ")
    : "";

  // Contract options from the data
  var contracts = {};
  jobs.forEach(function (j) { contracts[j.contract_type] = (contracts[j.contract_type] || 0) + 1; });
  var sel = document.getElementById("f-contract");
  Object.keys(contracts).sort().forEach(function (c) {
    sel.appendChild(el("option", { value: c, text: c + " (" + contracts[c] + ")" }));
  });

  // Controls
  document.querySelectorAll("[data-sort]").forEach(function (b) {
    b.addEventListener("click", function () { state.sort = b.getAttribute("data-sort"); render(); });
  });
  document.getElementById("f-policy").addEventListener("change", function (e) { state.policy = e.target.checked; render(); });
  sel.addEventListener("change", function (e) { state.contract = e.target.value; render(); });
  document.getElementById("f-score").addEventListener("change", function (e) { state.minScore = Number(e.target.value); render(); });
  document.getElementById("reset").addEventListener("click", function () {
    state.policy = false; state.contract = ""; state.minScore = 0;
    document.getElementById("f-policy").checked = false; sel.value = ""; document.getElementById("f-score").value = "0";
    render();
  });

  render();
})();
</script>
</body>
</html>
"""
