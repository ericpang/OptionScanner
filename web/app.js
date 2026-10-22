"use strict";
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const na = (x) => x === null || x === undefined || Number.isNaN(x);
const pct = (x, d = 1) => (na(x) ? "n/a" : (x * 100).toFixed(d) + "%");
const fx = (x, d = 2) => (na(x) ? "n/a" : Number(x).toFixed(d));
const big = (x) => {
  if (na(x)) return "n/a";
  const a = Math.abs(x), s = x < 0 ? "-" : "";
  return a >= 1e12 ? s + (a / 1e12).toFixed(2) + "T" : a >= 1e9 ? s + (a / 1e9).toFixed(2) + "B" : a >= 1e6 ? s + (a / 1e6).toFixed(1) + "M" : a >= 1e3 ? s + (a / 1e3).toFixed(1) + "K" : s + a.toFixed(0);
};
const safeUrl = (u) => (/^https?:\/\//i.test(u || "") ? u : "");
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();

let current = { symbol: null, range: "1y", chart: null, flows: null, cache: null };
let avwapDays = null;   // the AVWAP anchor chosen on this page; null leaves it to the server's default (AVWAP_DAYS)
let forcePeers = false; // set by "Refresh data": the next peer comparison measures the peers again instead of reusing them
let chartSeq = 0;       // numbers chart requests, so a slow one that was superseded cannot overwrite a newer one

// A cached or older index.html may lack a section this script renders into. Create it rather than failing,
// so one missing element can never make a panel (or the whole run) disappear.
function ensureSections() {
  const order = ["header", "recon", "quality", "valuation", "thesis", "metrics", "peers", "chartsec", "flows", "options", "news", "sources"];
  order.forEach((id, i) => {
    if ($(id)) return;
    const s = document.createElement("section");
    s.id = id;
    const next = order.slice(i + 1).map($).find(Boolean) || document.querySelector("footer");
    next.parentNode.insertBefore(s, next);
  });
  if (!$("mode")) { const m = document.createElement("div"); m.id = "mode"; $("status").after(m); }
  if (!$("cachenote")) { const m = document.createElement("div"); m.id = "cachenote"; $("mode").after(m); }
}

async function api(path) {
  const r = await fetch(path);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || j.detail?.[0]?.msg || `Request failed (${r.status})`);
  return j;
}

function setStatus(msg, err = false) {
  const s = $("status");
  s.textContent = msg;
  s.className = "status" + (err ? " err" : "");
  s.hidden = !msg;
}

// good / warn / bad tag from thresholds
function tag(v, good, bad, higherBetter = true) {
  if (na(v)) return "";
  if (higherBetter) return v >= good ? "good" : v < bad ? "bad" : "warn";
  return v <= good ? "good" : v > bad ? "bad" : "warn";
}
const card = (label, value, sub = "", cls = "") =>
  `<div class="m ${cls}"><div class="l">${esc(label)}</div><div class="v">${value}</div><div class="s">${sub}</div></div>`;
const lines = (...xs) => xs.filter(Boolean).join("<br>");   // a card's sub-text, one line per non-empty part
// The industry and peer lines of a card: blue when the stock is better than the median, red when worse, grey when it is
// equal or there is nothing to compare (`better` is true, false or null, from the API). The arrow is inside the line, so
// it takes the line's colour.
const benchmark = (html, better) => (html ? `<span class="bench${better === true ? " better" : better === false ? " worse" : ""}">${html}</span>` : "");

document.addEventListener("DOMContentLoaded", () => {
  $("form").addEventListener("submit", (e) => { e.preventDefault(); run($("symbol").value); });
  $("refresh").addEventListener("click", refreshData);
  $("cachenote").addEventListener("click", (e) => { if (e.target.closest("#refresh2")) refreshData(); });
  $("ranges").addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b || !current.symbol) return;
    current.range = b.dataset.r;
    [...$("ranges").children].forEach((x) => x.classList.toggle("on", x === b));
    reloadChart();
  });
  $("avwapdays").addEventListener("change", (e) => {
    const el = e.target, shown = current.chart && current.chart.avwap ? current.chart.avwap.days : "";
    const v = Math.round(Number(el.value));
    if (el.value.trim() === "" || !Number.isFinite(v)) { el.value = shown; return; }   // not a number: put back what is drawn
    avwapDays = Math.min(+el.max || 500, Math.max(+el.min || 5, v));                    // the server's limits arrive with each chart
    el.value = avwapDays;
    if (current.symbol) reloadChart();
  });
  const q = new URLSearchParams(location.search).get("symbol");
  if (q) { $("symbol").value = q; run(q); }
});

// Discard the ticker's cached data (server memory and disk), then search again so everything is fetched live.
async function refreshData() {
  const symbol = current.symbol;
  if (!symbol) return;
  $("refresh").disabled = true;
  try {
    const r = await fetch(`/api/refresh?symbol=${encodeURIComponent(symbol)}`, { method: "POST" });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || `Refresh failed (${r.status})`);
  } catch (err) { setStatus(err.message, true); $("refresh").disabled = false; return; }
  forcePeers = true;
  run(symbol);
}

async function run(raw) {
  const symbol = (raw || "").trim().toUpperCase();
  if (!symbol) return;
  $("refresh").disabled = false;
  ensureSections();
  ["header", "recon", "quality", "valuation", "thesis", "metrics", "peers", "flows", "options", "news", "sources", "mode", "cachenote"].forEach((i) => ($(i).innerHTML = ""));
  $("chartsec").hidden = true;
  setStatus(`Analyzing ${symbol}...`);
  current = { symbol, range: "1y", chart: null, flows: null, cache: null, filing: null, analysis: null, peers: null };
  [...$("ranges").children].forEach((x) => x.classList.toggle("on", x.dataset.r === "1y"));
  history.replaceState(null, "", `?symbol=${encodeURIComponent(symbol)}`);

  initOptions("Waiting for the analysis panels to finish, then scanning option chains (10-30 seconds)...");
  // Only these four requests say where the data came from. Later ones (a chart range, "Show more", the option scan)
  // read the cache file these just wrote, which would make a live search look like a cached one.
  const seen = (r) => { noteCache(r.cache); return r; };
  const jobs = [
    // The peer comparison starts as soon as the analysis is up, beside the other panels, not after them. The braces
    // matter: returning loadPeers' promise would hold the whole search (the status line, filings, options) for a minute.
    api(`/api/analyze?symbol=${symbol}`).then(seen).then(renderAnalysis).then(() => { loadPeers(symbol); }),
    api(chartUrl(symbol)).then(seen).then(showChart),
    api(`/api/flows?symbol=${symbol}`).then(seen).then((f) => { current.flows = f; renderFlows(f); drawChart(); }),
    api(`/api/news?symbol=${symbol}&limit=10`).then(seen).then((n) => renderNews(n, 10)),
  ];
  const res = await Promise.allSettled(jobs);
  const errs = res.filter((r) => r.status === "rejected").map((r) => r.reason.message);
  setStatus(errs.length ? [...new Set(errs)].join(" | ") : "", errs.length > 0);
  // After the other panels: the insider-trades lookup also queries SEC EDGAR, and the SEC rate-limits per address.
  api(`/api/filings?symbol=${symbol}`).then(renderFilingText).catch(() => {});
  loadOptions();  // after the other panels, so the option scan does not compete with them for data calls
}

// ---------------------------------------------------------------- cache note
// A response built from the on-disk cache carries `cache`: when that data was fetched, and how many items the cache
// did not hold and had to be fetched live. Show the oldest time any panel used.
function noteCache(c) {
  if (!c || c.symbol !== current.symbol || !c.from_cache) return;
  const o = current.cache;
  const base = o && o.as_of <= c.as_of ? o : c;   // ISO times in one format compare in time order
  current.cache = { as_of: base.as_of, age_seconds: base.age_seconds, ttl_seconds: c.ttl_seconds, live: (o ? o.live : 0) + c.live_calls };
  renderCacheNote();
}

function ageText(sec) {
  const m = Math.floor(sec / 60);
  return m < 1 ? "less than a minute" : m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${m % 60} min`;
}

function renderCacheNote() {
  const c = current.cache;
  if (!c) { $("cachenote").innerHTML = ""; return; }
  const at = (ms) => (Number.isNaN(ms) ? "" : new Date(ms).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }));
  const t0 = Date.parse(c.as_of), expires = at(t0 + c.ttl_seconds * 1000);
  $("cachenote").innerHTML = `<div class="banner info"><b>Data fetched from cache</b> as of ${esc(at(t0) || c.as_of)} (${esc(ageText(c.age_seconds))} ago).`
    + (c.live ? ` ${c.live} item${c.live === 1 ? " was" : "s were"} not in the cache and fetched live.` : "")
    + (expires ? ` Searching again after ${esc(expires)} fetches fresh data, or ` : " ")
    + `<button type="button" id="refresh2">refresh now</button></div>`;
}

// ---------------------------------------------------------------- analysis
function renderAnalysis(a) {
  current.analysis = a;
  if (a.mode === "demo") $("mode").innerHTML = `<div class="banner">DEMO DATA: synthetic numbers for trying the app. Not real market data.</div>`;
  const s = a.snapshot;
  $("header").innerHTML = `<div class="hdr">
    <div><span class="nm">${esc(s.name)}</span><span class="k">${esc(s.symbol)} ${esc(s.industry || "")}</span></div>
    <div><span class="px">${fx(s.price)}</span>${s.extended ? `<span class="ext" title="${esc(s.extended.session)} price">(${fx(s.extended.price)})</span>` : ""}<span class="k">${esc(s.currency || "")} last</span></div>
    <div><b>${big(s.market_cap)}</b><span class="k">market cap</span></div>
    <div><b>${fx(s.pe, 1)} / ${fx(s.pb, 2)}</b><span class="k">P/E, P/B (quoted)</span></div>
    <div><b>${fx(s.low52)} - ${fx(s.high52)}</b><span class="k">52-week range</span></div></div>`;
  const rec = a.reconciliation;
  $("recon").innerHTML = rec.status === "fail"
    ? `<div class="card" style="margin-top:14px;border-color:var(--bad)"><h3>Reconciliation failed: no scores shown</h3><p>${esc(a.message)}</p>${reconTable(rec)}</div>`
    : "";
  if (a.halted) { return renderSources(a, true); }
  renderQuality(a.quality);
  renderValuation(a.valuation, a.snapshot);
  renderThesis(a);
  renderMetrics(a.metrics, a.snapshot, a.benchmarks, current.peers);
  renderSources(a);
}

function reconTable(rec) {
  return `<table><tr><th>Check</th><th>Tolerance</th><th>Gap</th><th>Result</th></tr>${rec.checks.map((c) =>
    `<tr><td>${esc(c.name)}${c.note ? `<div class="s">${esc(c.note)}</div>` : ""}</td><td>${pct(c.tolerance, 0)}</td><td>${c.gap === null ? "n/a" : pct(c.gap, 2)}</td>
     <td><span class="tag ${c.status === "pass" ? "good" : c.status === "fail" ? "bad" : "warn"}">${esc(c.status)}</span></td></tr>`).join("")}</table>`;
}

function stars(n) { return na(n) ? "n/a" : "★".repeat(n) + "☆".repeat(5 - n); }

function renderQuality(q) {
  const p = q.piotroski, z = q.altman_z, b = q.beneish_m, s = q.sloan_accruals, m = q.magic_formula, g = q.graham, bf = q.buffett, w = q.roic_vs_wacc;
  const row = (name, val, rule, ok, note = "") =>
    `<tr><td><b>${name}</b></td><td>${val}</td><td>${rule}</td><td>${ok === null ? '<span class="tag">n/a</span>' : `<span class="tag ${ok ? "good" : "bad"}">${ok ? "pass" : "no"}</span>`}${note ? `<div class="s">${esc(note)}</div>` : ""}</td></tr>`;
  const chk = (n) => (q.checks.find((c) => c.name === n) || {});
  const st = (n) => (chk(n).applicable ? chk(n).pass : null);
  $("quality").innerHTML = `<h2>Quality scorecard <span class="tag ${q.passing / Math.max(q.applicable, 1) >= 0.6 ? "good" : "warn"}">${q.passing} of ${q.applicable} pass</span></h2>
  <div class="card"><table><tr><th>Scorecard</th><th>Result</th><th>Published threshold</th><th>Status</th></tr>
  ${row("Piotroski F-Score", p.score === null ? "n/a" : `${p.score} / ${p.of ?? 9}${p.band === "n/a" ? "" : " (" + p.band + ")"}`, "8-9 strong, 0-2 weak; app passes at 7+", st("Piotroski F"),
    `Basis: ${p.basis || "fiscal year"}.` + (p.annual && p.annual.score !== null && p.basis !== p.annual.basis ? ` Fiscal-year version: ${p.annual.score} / ${p.annual.of}.` : "") + (p.note ? " " + p.note : "") + " GuruFocus uses trailing twelve months vs the year before, which Longbridge's 5 quarters cannot build, so scores can differ.")}
  ${row("Altman Z-Score", z.score === null ? "n/a" : `${z.score} (${z.zone})`, "&gt;2.99 safe, 1.81-2.99 grey, &lt;1.81 distress", st("Altman Z"), z.note)}
  ${row("Beneish M-Score", b.score === null ? "n/a" : `${b.score}${b.flag ? " (flag)" : ""}`, "above -1.78 flags possible manipulation", st("Beneish M"), (b.basis ? "Basis: " + b.basis + ". " : "") + (b.note || "") + (b.drivers && b.drivers.length ? "; main drivers: " + b.drivers.map((d_) => `${d_.label} (${fx(d_.value, 2)})`).join(", ") : ""))}
  ${row("Sloan accruals", na(s.ratio) ? "n/a" : pct(s.ratio), "within +/-10% fine, above 25% red flag", st("Sloan accruals"), s.basis ? "Basis: " + s.basis : "")}
  ${row("Magic Formula", `EY ${pct(m.earnings_yield)}, ROC ${m.return_on_capital > 5 ? "&gt;500% (tiny capital base)" : pct(m.return_on_capital)}`, m.peers ? `peer percentile ${m.rank_ey ?? "n/a"} / ${m.rank_roc ?? "n/a"}` : "needs peer data for ranks", st("Magic Formula"))}
  ${row("Graham score", g.applicable ? `${g.adjusted_score} / 100 (${g.tier})` : "n/a", "app rubric: pass at 60+ and no value trap", st("Graham"), g.note || "")}
  ${row("Buffett moat", `<span class="stars">${stars(bf.stars.moat)}</span> moat`, "app rubric: moat and financials 4+ stars", st("Buffett"), bf.note)}
  ${row("ROIC vs WACC", na(w.spread) ? "n/a" : `${w.spread > 1 ? ">100%" : pct(w.spread)} spread (WACC ${pct(w.wacc)})`, "positive = creates value", st("ROIC vs WACC"), w.note)}
  </table><div class="note">${esc(q.pass_rules)} A score is a screening aid, not a prediction.</div></div>
  <div class="two" style="margin-top:12px">
   <div class="card"><h3>Buffett five dimensions <span class="tag">${esc(bf.tag)}</span></h3>
    ${["moat", "financials", "management", "valuation", "visibility"].map((k) => `<div><b>${k}</b> <span class="stars">${stars(bf.stars[k])}</span><ul class="plain">${(bf.evidence[k] || []).map((e) => `<li>${esc(e)}</li>`).join("")}</ul></div>`).join("")}
    <div class="note">Suggested minimum holding period: ${esc(bf.holding_period)}. This framework is not the right lens for a horizon under 3 years.</div></div>
   <div class="card"><h3>Graham cigar-butt analysis</h3>${graham(g)}</div></div>`;
}

// Morningstar's Fair Value Estimate is proprietary and has no public API, so it is typed in from morningstar.com and
// kept in this browser only, per symbol.
const msKey = (sym) => `msfve:${sym}`;
const msGet = (sym) => { try { const x = parseFloat(localStorage.getItem(msKey(sym))); return x > 0 ? x : null; } catch { return null; } };
const msSet = (sym, x) => { try { x > 0 ? localStorage.setItem(msKey(sym), String(x)) : localStorage.removeItem(msKey(sym)); } catch { /* storage blocked: the value just is not remembered */ } };

function renderMorningstar(snap) {
  if (!snap) return;
  const sym = snap.symbol, px = snap.price, fve = msGet(sym);
  const ratio = fve && px ? px / fve : null;
  const out = fve && px ? `<div class="hdr" style="margin-top:10px"><div><span class="px">${fx(fve)}</span><span class="k">Morningstar fair value estimate (entered by you)</span></div>
      <div><b>${fx(ratio, 2)}</b><span class="k">price / fair value</span></div>
      <div><span class="tag ${ratio <= 0.95 ? "good" : ratio <= 1.15 ? "warn" : "bad"}">${ratio <= 0.95 ? "below the estimate" : ratio <= 1.15 ? "near the estimate" : "above the estimate"}</span><span class="k">${ratio <= 1 ? "margin of safety " + ((1 - ratio) * 100).toFixed(0) + "%" : "price is " + ((ratio - 1) * 100).toFixed(0) + "% above the estimate"}</span></div></div>` : "";
  $("valuation").insertAdjacentHTML("beforeend", `<div class="card" id="msfve" style="margin-top:12px"><h3>Morningstar fair value estimate</h3>
    <form id="msform"><input id="msval" type="number" min="0" step="0.01" placeholder="e.g. 185.00" value="${fve ?? ""}" aria-label="Morningstar fair value estimate" style="width:140px">
    <button type="submit">Save</button>${fve ? ` <button type="button" id="msclear">Clear</button>` : ""}</form>${out}
    <div class="note">Morningstar's fair value estimate is its own analysts' number and is not available from any free data source, so this app cannot fetch it. Look the ticker up on morningstar.com, type the figure here (in ${esc(snap.currency || "the listing currency")}), and it is compared with the price. It is saved in this browser only and is not part of the app's scoring.</div></div>`);
  $("msform").addEventListener("submit", (e) => { e.preventDefault(); msSet(sym, parseFloat($("msval").value)); $("msfve").remove(); renderMorningstar(snap); });
  const clr = $("msclear");
  if (clr) clr.addEventListener("click", () => { msSet(sym, null); $("msfve").remove(); renderMorningstar(snap); });
}

function renderValuation(v, snap) {
  renderValuationModel(v, snap);
  renderMorningstar(snap);
}

function renderValuationModel(v, snap) {
  if (!v || !v.applicable) { $("valuation").innerHTML = `<h2>Fair value estimate</h2><div class="card"><p>${esc((v && v.note) || "Not available.")}</p></div>`; return; }
  const hl = v.headline, px = v.price;
  const vsTag = (x) => (x === null || x === undefined ? "" : x > 0 ? "good" : "bad");
  const rows = v.methods.map((m) => `<tr><td><b>${esc(m.name)}</b></td><td>${na(m.value) ? "n/a" : fx(m.value)}</td>
    <td>${na(m.vs_price) ? "" : `<span class="tag ${vsTag(m.vs_price)}">${m.vs_price > 0 ? "+" : ""}${(m.vs_price * 100).toFixed(0)}% vs price</span>`}</td><td class="s">${esc(m.note || "")}</td></tr>`).join("");
  const dcf = v.methods.find((m) => m.key === "dcf"), inp = dcf && dcf.inputs;
  const grid = dcf && dcf.grid ? `<table style="max-width:460px"><tr><th>Discount rate \\ stage-1 growth</th>${[0, 1, 2].map((i) => `<th>${pct(dcf.grid[i].growth, 0)}</th>`).join("")}</tr>
    ${[0, 1, 2].map((r_) => `<tr><td>${pct(dcf.grid[r_ * 3].ke, 1)}</td>${[0, 1, 2].map((i) => `<td${r_ === 1 && i === 1 ? ' style="font-weight:700"' : ""}>${fx(dcf.grid[r_ * 3 + i].fair_value)}</td>`).join("")}</tr>`).join("")}</table>` : "";
  const head = hl ? `<div class="hdr" style="margin-top:0"><div><span class="px">${fx(hl.value)}</span><span class="k">DCF fair value per share (range ${fx(hl.low)} to ${fx(hl.high)})</span></div>
      <div><b>${fx(hl.price_to_fair, 2)}</b><span class="k">price / fair value</span></div>
      <div><span class="tag ${hl.price_to_fair <= 0.95 ? "good" : hl.price_to_fair <= 1.15 ? "warn" : "bad"}">${esc(hl.rating)}</span><span class="k">${hl.margin_of_safety >= 0 ? "margin of safety " + (hl.margin_of_safety * 100).toFixed(0) + "%" : "price is " + (-hl.margin_of_safety * 100).toFixed(0) + "% above the estimate"}</span></div></div>` : `<p>A DCF could not be built (see the table), so there is no headline value.</p>`;
  $("valuation").innerHTML = `<h2>Fair value estimate <span class="tag warn">app model, not GF Value</span></h2>
  <div class="card">${head}
   <table style="margin-top:12px"><tr><th>Method</th><th>Per share</th><th>Vs price ${fx(px)}</th><th>How it is built</th></tr>${rows}</table>
   ${v.reverse_dcf ? `<p style="margin-top:12px"><b>What the price implies.</b> ${esc(v.reverse_dcf.text)}</p>` : ""}
   ${inp ? `<div class="two" style="margin-top:8px"><div><h3>DCF sensitivity (fair value per share)</h3>${grid}</div>
     <div><h3>DCF inputs</h3><ul class="plain"><li>Free cash flow (TTM): ${big(inp.fcf_ttm)}</li><li>Stage-1 growth used: <b>${pct(inp.stage1_growth, 1)}</b> for ${inp.stage1_years} years, fading to ${pct(inp.terminal_growth, 1)} over ${inp.fade_years} more</li>
     <li>Median of: ${Object.entries(inp.growth_candidates).map(([k, x]) => `${esc(k)} ${na(x) ? "n/a" : pct(x, 1)}`).join(", ")} (capped at ${pct(inp.growth_cap, 0)})</li>
     <li>Discount rate ${pct(inp.discount_rate, 1)}${inp.beta_assumed ? " (beta assumed 1.0)" : ""}</li></ul></div></div>` : ""}
   <div class="note">${esc(v.disclaimer)} GF Value comes from GuruFocus's own model and needs a paid GuruFocus API token, so it is not used here. Rating bands (price / fair value): up to 0.80 undervalued, 0.80-0.95 modestly undervalued, 0.95-1.15 roughly fair, 1.15-1.40 modestly overvalued, above 1.40 overvalued; these cut-offs are this app's own. A model, not a forecast: change an input and the answer moves, which is what the grid shows.</div></div>`;
}

function graham(g) {
  if (!g.applicable) return `<p>${esc(g.note)}</p>`;
  return `<p><b>${g.adjusted_score} / 100</b> (static ${g.static_score}, adjustment ${g.adjustment >= 0 ? "+" : ""}${g.adjustment}) ${g.value_trap ? '<span class="tag bad">value trap</span>' : ""}</p>
  <table>${g.breakdown.map((b) => `<tr><td>${esc(b.name)}</td><td>${b.points}</td><td>${esc(b.detail)}</td></tr>`).join("")}</table>
  <p class="note">NCAV/share ${fx(g.ncav_per_share)} (haircut-adjusted ${fx(g.ncav_adjusted_per_share)}); Graham buy line ${fx(g.buy_line)}</p>
  <ul class="plain">${g.dynamic_notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>
  <div><b>Value-trap rules</b> (2 of 5 = trap)<ul class="plain">${g.trap_rules.map((r) => `<li>${r.tripped ? "TRIPPED" : "clear"}: ${esc(r.rule)}</li>`).join("")}</ul></div>
  <p>${esc(g.holding_view)}</p><p class="note">${g.summary.map(esc).join(" ")}</p>`;
}

// The bear case: every concern the data shows, grouped by theme, each with a rating and the figures behind it.
function renderBear(r, fallback) {
  const sevTag = { high: "bad", medium: "warn", low: "" };
  if (!r || !r.items.length) return `<div class="card col bear"><h3 class="r">Bear case (0)</h3><p class="note">${fallback}</p></div>`;
  const cats = [...new Set(r.items.map((x) => x.category))];
  const body = cats.map((c) => `<h4 style="margin:12px 0 2px">${esc(c)}</h4>` + r.items.filter((x) => x.category === c).map((x) =>
    `<div class="pt"><b>${esc(x.title)} <span class="tag ${sevTag[x.severity]}">${esc(x.severity)}</span></b>
      <ul class="plain">${x.facts.map((f) => `<li>${esc(f)}</li>`).join("")}</ul><span><em>[${esc(x.source)}]</em></span></div>`).join("")).join("");
  return `<div class="card col bear"><h3 class="r">Bear case (${r.items.length})</h3>
    <p class="note">${r.counts.high} high, ${r.counts.medium} medium, ${r.counts.low} low. ${esc(r.note)}</p>${body}</div>`;
}

// What the data cannot show: the risks that live in the company's own filings.
function renderNotMeasured(r) {
  if (!r) return "";
  const filings = r.filings.length ? ` Latest filings: ${r.filings.map((f) => `<a href="${esc(f.url)}" target="_blank" rel="noopener">${esc(f.type)} ${esc(f.filed_at)}</a>`).join(", ")}.` : "";
  return `<div class="card" style="margin-top:12px"><h3>Not measured by this app</h3>
    <p class="note">These risks come from the company's own disclosures and are not in the data sources, so none is scored or guessed at. Check the filings' risk-factor section and the notes to the financial statements.${filings}</p>
    <ul class="plain">${r.not_measured.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>
    <div id="filingtext"><p class="note">Reading the latest filings from SEC EDGAR...</p></div></div>`;
}

// Verbatim text from the latest 10-K and 10-Q: the risk-factor summary and any sentence that puts a percentage on customers.
function renderFilingText(got) {
  if (got && got.symbol === current.symbol) current.filing = got;   // the filings can arrive before the thesis section they sit in, so keep them
  const f = current.filing, box = $("filingtext");
  if (!f || !box) return;
  if (f.status !== "ok") { box.innerHTML = `<p class="note">${esc(f.message)}</p>`; return; }
  const links = f.filings.map((x) => `<a href="${esc(x.url)}" target="_blank" rel="noopener">${esc(x.form)} filed ${esc(x.filed)}</a>`).join(", ");
  const conc = f.concentration.length ? f.concentration.map((c) => `<li>${esc(c.text)} <em>[${esc(c.form)} ${esc(c.filed)}]</em></li>`).join("")
    : `<li class="note">No sentence giving a customer's share of revenue was found by the text search. Read the filing's risk factors and its concentration note.</li>`;
  const summ = f.risk_summary.length ? `<h4 style="margin:12px 0 2px">The company's own summary of its risk factors</h4><ul class="plain">${f.risk_summary.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>`
    : `<p class="note">No "Summary of Risk Factors" section was found in the annual report; read Item 1A directly.</p>`;
  box.innerHTML = `<h4 style="margin:12px 0 2px">From the filings: ${links}</h4>
    <p class="note">Sentences copied from the filings by a text search, not scored or paraphrased. The search can miss a disclosure or catch an unrelated sentence, so confirm in the filing.</p>
    <h4 style="margin:12px 0 2px">Customer concentration</h4><ul class="plain">${conc}</ul>${summ}`;
}

function renderThesis(a) {
  const pts = (arr, none) => arr.length ? arr.map((p) => `<div class="pt"><b>${esc(p.title)}</b><span>${esc(p.evidence)} <em>[${esc(p.source)}]</em></span></div>`).join("") : `<p class="note">${none}</p>`;
  $("thesis").innerHTML = `<h2>Bull and bear case</h2>
  <div class="two">
   <div class="card col bull"><h3 class="b">Bull case (${a.bull.length})</h3>${pts(a.bull, "No supporting points met the rules.")}</div>
   ${renderBear(a.risks, "No cautionary points met the rules.")}</div>
  ${renderNotMeasured(a.risks)}
  <div class="card" style="margin-top:12px"><h3>Verdict: <span class="tag warn">${esc(a.verdict.tag)}</span></h3>
   <p>${esc(a.verdict.summary)}</p>
   ${a.narrative ? `<p class="narr">${esc(a.narrative)}</p>` : ""}
   <h3>Reference price levels</h3>
   <table>${a.levels.map((l) => `<tr><td>${esc(l.label)}</td><td>${fx(l.price)}</td><td>${pct(l.price / a.snapshot.price - 1)} vs price</td></tr>`).join("")}</table>
   <div class="note">Levels are reference points from analyst targets, the volume profile, Graham and the 52-week range. They are not forecasts.</div></div>`;
  renderFilingText();
}

// The industry lines under a valuation multiple: the industry median, how many times it the company is, and where the
// company ranks. All of it comes from the API (app/benchmarks.py); no block means the industry feed had nothing for
// this stock. The arrow says above or below the median; the colour says better or worse, and for a multiple a lower
// one is better (cheaper), which is the app's simplification: a low multiple can reflect trouble.
function industryLine(b) { return benchmark(industryText(b), b ? b.better : null); }
function industryText(b) {
  if (!b) return "";
  const head = `Industry median ${fx(b.median, b.median < 10 ? 2 : 1)}${na(b.n) ? "" : ` (${b.n} companies)`}`
    + (b.thin ? ` <span class="tag warn" title="Few companies, so the median is only a rough guide">thin sample</span>` : "");
  if (na(b.times_median)) return `${head}<br>no comparison without a positive figure`;
  const t = b.times_median;
  const where = b.outside ? `${esc(b.outside)} the industry's range` : na(b.percentile) ? "" : `percentile ${Math.round(b.percentile * 100)}`;
  const verdict = b.better === null || b.better === undefined ? "" : `${b.better ? "Better" : "Worse"} than the industry median (a lower multiple is better).`;
  const rank = na(b.percentile) || na(b.rank) ? "" : `Above ${Math.round(b.percentile * 100)}% of the other companies in the industry (rank ${b.rank} of ${b.n}, lowest first).`;
  const tip = [verdict, rank].filter(Boolean).join(" ");
  return `${head}<br><span${tip ? ` title="${esc(tip)}"` : ""}>${t >= 1 ? "▲" : "▼"} ${fx(t, t < 1 ? 2 : 1)}x the median${where ? ", " + where : ""}</span>`;
}

// How free cash flow to equity is made up, as a sum that adds up to the card's figure: operating cash flow less capex,
// plus the debt the company borrowed less the debt it repaid. The API's parts are signed, so they are shown as amounts
// after a plus or minus. A company that repaid more than it borrowed has paid its debt down, and that comes off the cash
// left for shareholders; one that borrowed more has had it added.
function fcfeBuild(f) {
  if (na(f.ttm)) return esc(f.note || "n/a");
  const nb = f.net_borrowing, over = f.basis === "fy" ? "last fiscal year" : "trailing 12 months";
  const debt = nb === 0 ? "no net borrowing" : `${nb > 0 ? "+ net borrowing" : "− debt paid down"} ${big(Math.abs(nb))}`;
  return `${over}: operating CF ${big(f.ocf)} − capex ${big(Math.abs(f.capex))} ${debt}`;
}

function renderMetrics(m, snap, bench, peers) {
  const x = m.extra, bm = bench || {}, pr = peers, fe = x.fcfe;
  const peg = m.peg, d = m.debt_to_ebitda, ar = m.analyst_ratings;
  const A = [
    card("P/E (TTM)", fx(m.pe_ttm.value, 1), lines(m.pe_ttm.basis ? "basis: " + m.pe_ttm.basis : "", industryLine(bm.pe))),
    card("PEG", fx(peg.value), peg.growth_basis ? `growth ${fx(peg.growth_pct, 1)}% (${peg.growth_basis.replace(/_/g, " ")}); ${esc(peg.pe_basis || "P/E")} ${fx(peg.pe_used, 1)}${peg.note ? ". " + esc(peg.note) : ""}` : "n/a (growth not positive)", tag(peg.value, 1, 2, false)),
    card("FCF growth", pct(m.fcf_growth.yoy), lines(`3y CAGR ${pct(m.fcf_growth.cagr_3y)}; FCF ${big(m.fcf_growth.fcf)}`, peerLine(pr, "fcf_growth")), tag(m.fcf_growth.yoy, 0.0001, 0)),
    card("Debt / EBITDA", d.net_cash ? "net cash" : fx(d.gross, 1) + "x", lines(`net ${fx(d.net, 1)}x; EBITDA ${big(d.ebitda)}`, peerLine(pr, "debt_to_ebitda")), d.net_cash ? "good" : tag(d.gross, 2, 4, false)),
    card("Analyst ratings", esc(ar.consensus || "n/a"), `${ar.buy ?? "-"} Buy / ${ar.hold ?? "-"} Hold / ${ar.sell ?? "-"} Sell; target ${fx(ar.target_mean)} (${pct(ar.upside_pct)})`, ar.consensus === "Buy" ? "good" : ar.consensus === "Sell" ? "bad" : ""),
    card("Operating margin", pct(m.operating_margin.ttm), lines("5y: " + m.operating_margin.history.map((v) => pct(v, 0)).join(" "), peerLine(pr, "operating_margin"))),
    card("ROIC", m.roic > 1 ? ">100%" : pct(m.roic), lines(m.roic > 1 ? "invested capital is mostly cash, so the exact figure is not meaningful" : "NOPAT / avg invested capital", peerLine(pr, "roic")), tag(m.roic, 0.12, 0.08)),
    card("ROE", pct(m.roe.latest), lines("history: " + m.roe.history.map((v) => pct(v, 0)).join(" "), peerLine(pr, "roe")), tag(m.roe.latest, 0.15, 0.10)),
    card("Net income growth", pct(m.net_income_growth.yoy), lines(`3y ${pct(m.net_income_growth.cagr_3y)}, 5y ${pct(m.net_income_growth.cagr_5y)}, last qtr YoY ${pct(m.net_income_growth.latest_quarter_yoy)}`, peerLine(pr, "net_income_growth")), tag(m.net_income_growth.cagr_3y, 0.10, 0)),
  ];
  const B = [
    card("Revenue growth", pct(x.revenue_growth.yoy), lines(`3y ${pct(x.revenue_growth.cagr_3y)}, 5y ${pct(x.revenue_growth.cagr_5y)}`, peerLine(pr, "revenue_growth"))),
    card("EPS revisions / surprises", pct(x.eps_revisions.forward_vs_90d), "last 4 surprises: " + (x.eps_revisions.surprises_last4.map((v) => pct(v, 0)).join(" ") || "n/a")),
    card("Gross margin", pct(x.gross_margin.latest), lines("5y: " + x.gross_margin.history.map((v) => pct(v, 0)).join(" "), peerLine(pr, "gross_margin")), tag(x.gross_margin.latest, 0.30, 0.20)),
    card("Non-operating income", na(x.non_operating_share) ? "n/a" : pct(x.non_operating_share, 0), "of pre-tax income (TTM): investment gains and interest, which flatter net income", na(x.non_operating_share) ? "" : x.non_operating_share >= 0.25 ? "bad" : x.non_operating_share >= 0.10 ? "warn" : ""),
    card("Net margin", pct(x.net_margin), peerLine(pr, "net_margin")), card("FCF margin", pct(x.fcf_margin), peerLine(pr, "fcf_margin")),
    card("Cash conversion", fx(x.cash_conversion) + "x", lines("FCF / net income", peerLine(pr, "cash_conversion")), tag(x.cash_conversion, 0.8, 0.8)),
    card("EV / EBITDA", fx(x.ev_ebitda, 1), peerLine(pr, "ev_ebitda")), card("P/S", fx(x.ps, 1), industryLine(bm.ps)), card("P/B", fx(x.pb, 1), industryLine(bm.pb)),
    card("FCF yield", pct(x.fcf_yield), peerLine(pr, "fcf_yield")),
    card("Free cash flow to equity", big(fe.ttm), lines(fcfeBuild(fe), na(fe.ttm) ? "" : `debt issued ${big(fe.debt_issued)}, repaid ${big(-fe.debt_repaid)}`,
      fe.history.length ? "history: " + fe.history.map((v) => big(v)).join(" ") : ""), tag(fe.ttm, 0.0001, 0)),
    card("FCFE margin", pct(fe.margin), lines("FCFE / revenue", peerLine(pr, "fcfe_margin"))),
    card("FCFE yield", pct(fe.yield), lines("FCFE / market cap", peerLine(pr, "fcfe_yield"))),
    card("Interest coverage", na(x.interest_coverage) ? "n/a" : fx(x.interest_coverage, 1) + "x", peerLine(pr, "interest_coverage"), tag(x.interest_coverage, 5, 3)),
    card("Current ratio", fx(x.current_ratio), peerLine(pr, "current_ratio"), tag(x.current_ratio, 1.5, 1)), card("Quick ratio", fx(x.quick_ratio), peerLine(pr, "quick_ratio")),
    card("Net debt / equity", fx(x.net_debt_equity), peerLine(pr, "net_debt_equity")),
    card("Dividend", pct(x.dividend.yield), `payout ${pct(x.dividend.payout, 0)}; years of growth ${x.dividend.years_growth ?? "n/a"}`),
    card("Share count, 3y", pct(x.share_count_change_3y), "buyback yield " + pct(x.buyback_yield), tag(x.share_count_change_3y, -0.0001, 0.03, false)),
    card("Insider net (3m / 6m / 12m)", [x.insider_net.m3, x.insider_net.m6, x.insider_net.m12].map(big).join(" / "), x.insider_net.m12_partial_from ? `12m is partial: data only from ${esc(x.insider_net.m12_partial_from)}` : "open-market buys and sells (Form 4)"),
    card("Short interest", na(x.short_interest_pct) ? "n/a" : x.short_interest_pct + "%", na(x.short_interest_pct) ? "no data source wired in" : ""),
    card("Beta, HV20 / HV60", `${fx(x.beta)}, ${pct(x.hv20, 0)} / ${pct(x.hv60, 0)}`, "max drawdown 1y " + pct(x.max_drawdown_1y, 0)),
    card("Returns 3m / 6m / 12m", [x.returns.m3, x.returns.m6, x.returns.m12].map((v) => pct(v, 0)).join(" / "), "absolute; no index data wired in"),
    card("From 52w high / low", `${pct(x.from_52w_high, 0)} / ${pct(x.from_52w_low, 0)}`, "vs EMA 200: " + pct(x.from_ema200)),
  ];
  $("metrics").innerHTML = `<h2>Key metrics</h2><div class="grid">${A.join("")}</div><h2>Additional metrics</h2><div class="grid">${B.join("")}</div>
   <div class="note">n/a means the input was not in the data; the app never estimates a missing figure. Tags: green favourable, amber neutral, red unfavourable (thresholds in app/config.py).
   Industry lines (P/E, P/S, P/B): the median over the companies in the stock's industry that have a positive value, from Longbridge. The industry is Longbridge's own grouping, so it is only as like-for-like as that group is.
   Peer lines (everything else): the median over the companies in that industry closest to the stock in market cap, each measured by this app's own formulas. They appear when the peer comparison below has loaded; see Peer group.
   Blue means better than the median and red worse; grey means equal, or nothing to compare. A lower figure is better for P/E, P/S, P/B, EV / EBITDA, Debt / EBITDA and net debt / equity, and a higher one for the rest. That is a simplification (a high ROE can come from debt, a low multiple from trouble), and the arrow still says above or below.</div>`;
}

// ---------------------------------------------------------------- peers
// The figures compared with peers, in the order of the peer table: [label, how a value is shown, how the stock is set
// against the median, the unit of a gap]. "pts" is a gap in percentage points, "diff" a gap in the figure's own unit and
// "ratio" is times the median. The keys are the API's (PEER_METRICS in app/benchmarks.py).
const PEER_FIG = {
  operating_margin: ["Operating margin", (v) => pct(v), "pts"],
  roic: ["ROIC", (v) => pct(v), "pts"],
  roe: ["ROE", (v) => pct(v), "pts"],
  debt_to_ebitda: ["Debt / EBITDA", (v) => (na(v) ? "n/a" : fx(v, 1) + "x"), "diff", "x"],
  fcf_growth: ["FCF growth", (v) => pct(v), "pts"],
  net_income_growth: ["Net income growth", (v) => pct(v), "pts"],
  revenue_growth: ["Revenue growth", (v) => pct(v), "pts"],
  gross_margin: ["Gross margin", (v) => pct(v), "pts"],
  net_margin: ["Net margin", (v) => pct(v), "pts"],
  fcf_margin: ["FCF margin", (v) => pct(v), "pts"],
  fcfe_margin: ["FCFE margin", (v) => pct(v), "pts"],
  cash_conversion: ["Cash conversion", (v) => (na(v) ? "n/a" : fx(v) + "x"), "diff", "x"],
  ev_ebitda: ["EV / EBITDA", (v) => fx(v, 1), "ratio"],
  fcf_yield: ["FCF yield", (v) => pct(v), "pts"],
  fcfe_yield: ["FCFE yield", (v) => pct(v), "pts"],
  interest_coverage: ["Interest coverage", (v) => (na(v) ? "n/a" : fx(v, 1) + "x"), "diff", "x"],
  current_ratio: ["Current ratio", (v) => fx(v), "diff"],
  quick_ratio: ["Quick ratio", (v) => fx(v), "diff"],
  net_debt_equity: ["Net debt / equity", (v) => fx(v), "diff"],
};

// The peer lines under a metric: the peers' median, and how the stock stands against it. Nothing until the comparison
// has loaded. The arrow says above or below the median; the colour says better or worse, which way round depending on the
// figure (the API's `higher_is_better`: a lower Debt / EBITDA is better, a higher margin is).
function peerLine(p, key) {
  const b = p && p.status === "ok" && p.metrics[key];
  return benchmark(peerText(p, key), b ? b.better : null);
}
function peerText(p, key) {
  const b = p && p.status === "ok" && p.metrics[key];
  if (!b) return "";
  const [, f, how, unit = ""] = PEER_FIG[key];
  if (na(b.median)) return `Peer median n/a: only ${b.n} peer${b.n === 1 ? " has" : "s have"} this figure`;
  const head = `Peer median ${f(b.median)} (${b.n} peers)`;
  if (na(b.diff)) return `${head}<br>no figure for ${esc(p.symbol)} to compare`;
  const amount = how === "pts" ? `${(Math.abs(b.diff) * 100).toFixed(1)} pts` : `${fx(Math.abs(b.diff), Math.abs(b.diff) >= 10 ? 1 : 2)}${unit}`;
  const gap = b.diff === 0 ? "equal to the median"
    : how === "ratio" && !na(b.ratio) ? `${fx(b.ratio, b.ratio < 1 ? 2 : 1)}x the median`
    : `${amount} ${b.diff > 0 ? "above" : "below"} the median`;
  const verdict = b.better === null || b.better === undefined ? "" : `${b.better ? "Better" : "Worse"} than the peer median (${b.higher_is_better ? "higher" : "lower"} is better for this figure). `;
  const tip = `${verdict}Middle half of the peers: ${f(b.q1)} to ${f(b.q3)}`;
  return `${head}<br><span title="${esc(tip)}">${b.diff > 0 ? "▲" : b.diff < 0 ? "▼" : "="} ${gap}, above ${b.above} of ${b.n}</span>`;
}

// Measuring the peers takes about 45 seconds the first time (the statements endpoint allows about two calls a second),
// so it is its own request, started when the analysis is up. The metric cards are drawn again when it arrives.
async function loadPeers(sym) {
  const a = current.analysis;
  if (!a || a.halted || current.symbol !== sym) return;
  const fresh = forcePeers;
  forcePeers = false;
  $("peers").innerHTML = `<h2>Peer group</h2><div class="card"><p class="note">Comparing ${esc(sym)} with the companies in its industry closest to it in size. The first time takes about 45 seconds, because the data service allows only about two statement downloads a second; the results are then reused for days.</p></div>`;
  try {
    const p = await api(`/api/peers?symbol=${encodeURIComponent(sym)}${fresh ? "&refresh=true" : ""}`);
    if (current.symbol !== sym) return;   // the user searched for something else meanwhile
    current.peers = p;
    renderMetrics(a.metrics, a.snapshot, a.benchmarks, p);
    renderPeers(p);
  } catch (e) {
    if (current.symbol === sym) $("peers").innerHTML = `<h2>Peer group</h2><div class="card"><p class="note">The peer comparison failed: ${esc(e.message)}</p></div>`;
  }
}

function renderPeers(p) {
  const left = (p.excluded || []).map((e) => `<span title="${esc(e.name)}">${esc(e.symbol)}</span>: ${esc(e.reason)}`);
  if (p.status !== "ok") {
    $("peers").innerHTML = `<h2>Peer group</h2><div class="card"><p>${esc(p.message)}</p>${left.length ? `<p class="note"><b>Tried and left out:</b> ${left.join("; ")}.</p>` : ""}</div>`;
    return;
  }
  const when = p.as_of ? new Date(p.as_of).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "";
  const names = p.peers.map((c) => `<span title="${esc(c.name)}, market cap ${big(c.market_cap)}">${esc(c.symbol)}</span>`).join(", ");
  const rows = Object.entries(PEER_FIG).map(([k, [label, f]]) => {
    const b = p.metrics[k];
    return `<tr><td>${esc(label)}</td><td><b>${f(b.company)}</b></td><td>${f(b.median)}</td><td>${na(b.q1) ? "n/a" : `${f(b.q1)} to ${f(b.q3)}`}</td>${p.peers.map((c) => `<td>${f(c.values[k])}</td>`).join("")}</tr>`;
  }).join("");
  $("peers").innerHTML = `<h2>Peer group <span class="tag">${p.peers.length} peers</span></h2>
  <div class="card peers">
   <p>The ${p.peers.length} companies in ${esc(p.symbol)}'s industry closest to it in market cap: ${names}. Each is measured by this app's own formulas, the same ones that measure ${esc(p.symbol)}, and passes the same reconciliation checks.</p>
   <details><summary>Show every figure for every peer</summary>
    <div class="ptable"><table><tr><th>Figure</th><th>${esc(p.symbol)}</th><th>Peer median</th><th>Middle half</th>${p.peers.map((c) => `<th title="${esc(c.name)}, market cap ${big(c.market_cap)}">${esc(c.symbol)}</th>`).join("")}</tr>${rows}</table></div>
   </details>
   <div class="note">${when ? `The peers' figures were fetched ${esc(when)} (the oldest of them) and are reused for ${p.ttl_days} days; <b>Refresh data</b> measures them again. ` : ""}${left.length ? `<b>Left out:</b> ${left.join("; ")}. Each was replaced by the next closest company. ` : ""}A median needs ${p.min_peers} peers with the figure. ROE and net debt / equity skip a peer whose equity is zero or negative, and EV / EBITDA, FCF yield and FCFE yield skip a peer whose statements are in another currency than its shares. Periods follow each company's own latest figures, so fiscal years differ between peers. The comparison is only as like-for-like as Longbridge's industry grouping.</div>
  </div>`;
}

// ---------------------------------------------------------------- chart
const chartUrl =(symbol) => `/api/chart?symbol=${encodeURIComponent(symbol)}&range=${current.range}${avwapDays ? `&days=${avwapDays}` : ""}`;

function showChart(c) {
  current.chart = c;
  $("chartsec").hidden = false;
  drawChart();
  renderAvwap(c.avwap);
  renderObv(c.obv);
}

// A new range or AVWAP anchor: fetch the chart again. Only the newest request may draw.
async function reloadChart() {
  const sym = current.symbol, seq = ++chartSeq;
  try {
    const c = await api(chartUrl(sym));
    if (seq === chartSeq && sym === current.symbol) showChart(c);
  } catch (err) { if (seq === chartSeq) setStatus(err.message, true); }
}

function drawChart() {
  const c = current.chart;
  if (!c || !window.Plotly) return;
  const up = css("--up"), down = css("--down"), ink = css("--ink"), muted = css("--muted"), line = css("--line");
  const d = c.candles.map((k) => k.t);
  const vp = c.volume_profile;
  const traces = [
    { type: "candlestick", x: d, open: c.candles.map((k) => k.o), high: c.candles.map((k) => k.h), low: c.candles.map((k) => k.l), close: c.candles.map((k) => k.c),
      name: "Price", increasing: { line: { color: up } }, decreasing: { line: { color: down } }, xaxis: "x", yaxis: "y" },
    ...[["20", css("--ema20")], ["50", css("--ema50")], ["200", css("--ema200")]].map(([p, col]) => ({
      type: "scatter", mode: "lines", x: d, y: c.ema[p], name: "EMA " + p, line: { color: col, width: 1.6 }, xaxis: "x", yaxis: "y", connectgaps: false })),
    { type: "bar", x: d, y: c.candles.map((k) => k.v), name: "Volume", marker: { color: c.candles.map((k) => (k.c >= k.o ? up : down)), opacity: 0.6 }, xaxis: "x", yaxis: "y2", showlegend: false },
    { type: "bar", orientation: "h", x: vp.bins.map((b) => b.volume), y: vp.bins.map((b) => (b.price_low + b.price_high) / 2),
      marker: { color: vp.bins.map((b) => (b.in_value_area ? css("--accent") : muted)), opacity: 0.55 }, name: "Volume profile",
      xaxis: "x2", yaxis: "y", hovertemplate: "%{y:.2f}: %{x:,.0f}<extra>profile</extra>" },
  ];
  // OBV and its moving average get a panel of their own between the price and the volume bars, on the same dates.
  const ob = c.obv && c.obv.series && c.obv.series.obv.length === d.length ? c.obv.series : null;
  if (ob) traces.push(
    { type: "scatter", mode: "lines", x: d, y: ob.obv, name: "OBV", line: { color: css("--accent"), width: 1.4 }, xaxis: "x", yaxis: "y3" },
    { type: "scatter", mode: "lines", x: d, y: ob.ma, name: `OBV ${ob.ma_days}-day average`, line: { color: css("--ema20"), width: 1.3, dash: "dot" }, xaxis: "x", yaxis: "y3", connectgaps: false });
  const f = current.flows;
  if (f && f.flow.high_volume_days.length) {
    const byDate = Object.fromEntries(c.candles.map((k) => [k.t, k]));
    for (const [type, col, sym, field] of [["accumulation", up, "triangle-up", "l"], ["distribution", down, "triangle-down", "h"]]) {
      const days = f.flow.high_volume_days.filter((h) => h.type === type && byDate[h.date]);
      if (days.length) traces.push({ type: "scatter", mode: "markers", x: days.map((h) => h.date), y: days.map((h) => byDate[h.date][field] * (field === "l" ? 0.99 : 1.01)),
        marker: { color: col, symbol: sym, size: 9 }, name: "High-volume " + type, xaxis: "x", yaxis: "y" });
    }
  }
  const lvl = (y, label, dash) => ({ type: "line", xref: "paper", x0: 0, x1: 0.85, yref: "y", y0: y, y1: y, line: { color: muted, width: 1, dash }, name: label });
  const shapes = [], ann = [];
  [["poc", "POC", "solid"], ["vah", "VAH", "dot"], ["val", "VAL", "dot"]].forEach(([k, l, dash]) => {
    if (vp[k]) { shapes.push(lvl(vp[k], l, dash)); ann.push({ xref: "paper", x: 0.85, y: vp[k], yref: "y", text: l, showarrow: false, xanchor: "left", font: { size: 10, color: muted } }); }
  });
  const layout = {
    paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)", font: { color: ink, size: 12 }, margin: { l: 60, r: 10, t: 10, b: 30 },
    xaxis: { domain: [0, 0.85], rangeslider: { visible: false }, rangebreaks: [{ bounds: ["sat", "mon"] }], gridcolor: line, anchor: "y2" },
    xaxis2: { domain: [0.87, 1], showticklabels: false, anchor: "y", gridcolor: line },
    yaxis: { domain: ob ? [0.4, 1] : [0.24, 1], gridcolor: line, side: "left" }, yaxis2: { domain: [0, 0.18], gridcolor: line, side: "left" },
    ...(ob ? { yaxis3: { domain: [0.22, 0.36], gridcolor: line, side: "left", tickformat: "~s", title: { text: "OBV", font: { size: 10 }, standoff: 4 } } } : {}),
    legend: { orientation: "h", y: 1.06 }, shapes, annotations: ann, hovermode: "x unified", bargap: 0.05,
  };
  Plotly.react("chart", traces, layout, { responsive: true, displaylogo: false });
  const s = c.signals;
  $("chartsig").innerHTML = `${esc(c.approx_profile)}. Price vs EMA 20/50/200: ${["20", "50", "200"].map((p) => `${p}: ${s.above_ema[p] === null ? "n/a" : s.above_ema[p] ? "above" : "below"}`).join(", ")}.
    50/200 cross (last ~60 sessions): ${esc(s.cross_50_200)}. Price vs value area: ${esc(s.price_vs_value_area || "n/a")}. POC ${fx(vp.poc)}, VAL ${fx(vp.val)}, VAH ${fx(vp.vah)}.`;
}
window.matchMedia("(prefers-color-scheme: dark)").addEventListener?.("change", drawChart);

// ---------------------------------------------------------------- anchored VWAP insights
// The matrix conclusion for the latest closed session. The wording, the colour and the rule table all come from the
// API (app/avwap.py), so the framework is defined in one place.
function renderAvwap(a) {
  let box = $("avwapinsight");
  if (!box) { box = document.createElement("div"); box.id = "avwapinsight"; $("chartsig").after(box); }
  if (!a) { box.innerHTML = ""; return; }
  const field = $("avwapdays");
  if (field) { field.value = a.days; field.min = a.min_days; field.max = a.max_days; }
  const title = `Technical insights: ${a.days}-day anchored VWAP`;
  if (a.status !== "ok") { box.innerHTML = `<h2>${title}</h2><div class="card"><p>${esc(a.message)}</p></div>`; return; }
  const c = a.conclusion, l = a.latest, r = a.rules;
  const open = box.querySelector("details[open]") ? " open" : "";   // keep it open across a range or anchor change
  const dist = l.side === "at" ? "at the AVWAP" : `${pct(Math.abs(l.distance), 2)} ${esc(l.side)}`;
  const cross = l.crossed ? `crossed ${l.crossed === "up" ? "above" : "below"} the AVWAP this session` : "no cross this session";
  const forming = a.last_bar_final ? "" : `<p class="note"><b>The newest bar (${esc(a.newest_bar)}) was still forming when this data was fetched.</b> Its volume is a fraction of a full day's, so the read is on the ${esc(a.as_of)} close.</p>`;
  const rows = a.matrix.map((m, i) => `<tr class="${m.id === c.id ? "on" : ""}"><td>${i + 1}</td><td>${esc(m.when)}</td><td>${esc(m.text)}</td></tr>`).join("");
  box.innerHTML = `<h2>${title} <span class="tag ${c.tone}${c.strong ? " solid" : ""}">${esc(c.bias)}</span></h2>
  <div class="card avwap ${c.tone}">
    <div class="hl">${esc(c.text)}</div>
    <p>${esc(c.summary)}</p>
    <div class="grid">
      ${card("Close", fx(l.close), `${esc(a.as_of)}, latest closed session`)}
      ${card(`AVWAP ${a.days}d`, fx(l.avwap), `anchored at the ${esc(a.anchor.date)} session`)}
      ${card("Close vs AVWAP", dist, esc(cross), l.side === "above" ? "good" : l.side === "below" ? "bad" : "")}
      ${card("Volume vs average", `${fx(l.rel_volume, 2)}x`, `${big(l.volume)} vs ${big(l.avg_volume)}, the ${a.days}-day average`)}
    </div>
    ${forming}
    <details${open}><summary>How this is decided</summary>
      <table><tr><th>#</th><th>Condition on the latest closed session</th><th>Conclusion</th></tr>${rows}</table>
      <div class="note">Checked in this order: 6, then 5, then 1 to 4, because 5 and 6 are special cases of the four quadrants. A cross is a close on the other side of the AVWAP from the session before. Volume is compared with the average of the same ${a.days} sessions the AVWAP covers. Flat volume is within ${pct(r.flat_volume, 0)} of that average; consolidating is the last ${r.chop_bars} closes spanning no more than ${r.chop_atr} x ATR(${r.atr_days}), with the latest within that distance of the AVWAP (ATR now ${fx(l.atr)}). All of these are set in app/config.py.</div>
    </details>
    <div class="note">"Institutional" is an inference from price against the volume-weighted average and from volume; the data does not show who traded. The matrix is a rule-of-thumb read of two numbers, not a forecast or advice.</div>
  </div>`;
}

// ---------------------------------------------------------------- OBV insights
// Price, volume and OBV direction against the 27-row table, and the divergence test. The wording, the colours and
// the table come from the API (app/obv.py), so the framework is defined in one place.
function renderObv(o) {
  let box = $("obvinsight");
  if (!box) { box = document.createElement("div"); box.id = "obvinsight"; ($("avwapinsight") || $("chartsig")).after(box); }
  if (!o) { box.innerHTML = ""; return; }
  const title = "Technical insights: On-Balance Volume (OBV)";
  const dv = o.divergence || {};
  const divHtml = dv.status === "insufficient_history" ? `<p class="note">${esc(dv.message)}</p>` :
    `<h3>Divergence <span class="tag ${dv.kind === "bearish" ? "bad solid" : dv.kind === "bullish" ? "good solid" : ""}">${dv.kind ? esc(dv.kind) + " divergence" : "none"}</span></h3><p>${esc(dv.summary)}</p>`;
  if (o.status !== "ok") { box.innerHTML = `<h2>${title}</h2><div class="card avwap"><p>${esc(o.message)}</p>${divHtml}</div>`; return; }
  const c = o.conclusion, l = o.latest, r = o.rules;
  const open = box.querySelector("details[open]") ? " open" : "";   // keep it open across a range change
  const forming = o.last_bar_final ? "" : `<p class="note"><b>The newest bar (${esc(o.newest_bar)}) was still forming when this data was fetched.</b> Its volume is a fraction of a full day's, so the read is on the ${esc(o.as_of)} close.</p>`;
  const toneOf = { rising: "good", falling: "bad", flat: "warn", increasing: "", decreasing: "" };
  const rows = o.matrix.map((m, i) => `<tr class="${m.id === c.id ? "on" : ""}"><td>${i + 1}</td><td>${esc(m.price)}</td><td>${esc(m.volume)}</td><td>${esc(m.obv)}</td><td>${esc(m.label)}${m.framework ? "" : " &dagger;"}</td><td>${esc(m.dynamic)}</td><td>${esc(m.action)}</td></tr>`).join("");
  box.innerHTML = `<h2>${title} <span class="tag ${c.tone}${c.strong ? " solid" : ""}">${esc(c.label)}</span></h2>
  <div class="card avwap ${c.tone}">
    <div class="hl">${esc(c.text)}</div>
    <p>${esc(c.summary)}</p>
    <div class="grid">
      ${card(`Price, ${o.days} sessions`, `${l.price_change >= 0 ? "+" : ""}${pct(l.price_change)}`, `${esc(l.price_state)}; flat is within ${pct(l.flat_band)} (${r.flat_atr} x ATR ${r.atr_days})`, toneOf[l.price_state])}
      ${card("Volume vs the period before", `${fx(l.volume_ratio, 2)}x`, `${esc(l.volume_state)}; ${big(l.avg_volume)} vs ${big(l.prior_avg_volume)} a day`)}
      ${card(`OBV, ${o.days} sessions`, `${l.obv_share >= 0 ? "+" : ""}${pct(l.obv_share, 0)}`, `${esc(l.obv_state)}; net change as a share of the volume traded`, toneOf[l.obv_state])}
      ${card(`OBV vs its ${r.ma_days}-day average`, esc(l.obv_vs_ma || "n/a"), "drawn on the chart", l.obv_vs_ma === "above" ? "good" : l.obv_vs_ma === "below" ? "bad" : "")}
    </div>
    ${forming}
    ${divHtml}
    <details${open}><summary>How this is decided</summary>
      <table><tr><th>#</th><th>Price</th><th>Volume</th><th>OBV</th><th>Market condition</th><th>Underlying dynamic</th><th>Trading action</th></tr>${rows}</table>
      <div class="note">The last ${o.days} closed sessions are read as three things. <b>Price</b> is rising or falling when the close moved by more than ${r.flat_atr} x ATR(${r.atr_days}) and flat otherwise. <b>Volume</b> is increasing or decreasing when its average over those sessions is more than ${pct(r.flat_volume, 0)} above or below the average of the ${o.days} before them, and flat otherwise. <b>OBV</b> is rising or falling when its net change is more than ${pct(r.flat_share, 0)} of the volume traded over them and flat otherwise. Divergence is separate: price closes at a new ${dv.window}-session high (low) in the last ${dv.recent} sessions while OBV's recent peak (trough) stays more than ${pct(r.divergence_gap, 0)} of the window's volume below (above) its earlier one. All of these are set in app/config.py.</div>
    </details>
    <div class="note">OBV adds a session's whole volume on an up close and subtracts it on a down close, so it infers buying and selling pressure from price and volume; the data does not show who traded. The table is a rule-of-thumb read, not a forecast or advice.</div>
    ${o.matrix.some((m) => !m.framework) ? `<div class="note">&dagger; The framework's table does not list this combination of price, volume and OBV; the wording and the action are this app's own reading, not the framework's.</div>` : ""}
  </div>`;
}

// ---------------------------------------------------------------- flows
function renderFlows(f) {
  const n = f.net_read, r = f.reported, fl = f.flow;
  const tagCls = n.label === "accumulation" ? "good" : n.label === "distribution" ? "bad" : "warn";
  $("flows").innerHTML = `<h2>Institutional buying and selling <span class="tag ${tagCls}">${esc(n.label)}</span></h2>
  <div class="two"><div class="card"><h3>Reported activity (delayed)</h3>
    <p>Institutional ownership: <b>${pct(r.institutional_ownership_pct)}</b>${na(r.change_qoq_pct) ? "" : ` (change QoQ ${pct(r.change_qoq_pct, 2)})`} &middot; latest 13F report ${esc(r.as_of || "n/a")}</p>
    <p>Insider net value: 3m ${big(r.insider.net_3m)}, 6m ${big(r.insider.net_6m)}, 12m ${big(r.insider.net_12m)}${r.insider.net_12m_partial_from ? ` (partial: data from ${esc(r.insider.net_12m_partial_from)})` : ""} ${r.insider.cluster_buy ? '<span class="tag good">cluster buy</span>' : ""}</p>
    ${r.holder_breadth && r.holder_breadth.listed && (r.holder_breadth.increased + r.holder_breadth.decreased) > 0 ? `<p class="note">Among the ${r.holder_breadth.listed} largest holders: <b>${r.holder_breadth.increased}</b> added, <b>${r.holder_breadth.decreased}</b> trimmed, net ${r.holder_breadth.net_shares >= 0 ? "+" : ""}${big(r.holder_breadth.net_shares)} shares. Table shows the 10 largest by position, not the biggest changes.</p>` : ""}
    ${r.top_holders.length ? `<table><tr><th>Holder</th><th>% of shares</th><th>Change</th></tr>${r.top_holders.map((h) => `<tr><td>${esc(h.name)}</td><td>${na(h.pct) ? (na(h.shares) ? "n/a" : big(h.shares)) : fx(h.pct, 2) + "%"}</td><td>${!na(h.change_shares) ? (h.change_shares >= 0 ? "+" : "") + big(h.change_shares) + " sh" : pct(h.change_pct)}</td></tr>`).join("")}</table>` : "<p class='note'>No holder data.</p>"}
    ${f.coverage_note ? `<div class="note">${esc(f.coverage_note)}</div>` : ""}</div>
   <div class="card"><h3>Flow indicators (estimates)</h3>
    <table><tr><td>Large-order net flow (${esc(fl.large_order_window || "20d")})</td><td>${big(fl.large_order_net_20d)}</td></tr>
    <tr><td>Chaikin Money Flow (20)</td><td>${fx(fl.cmf_20)}</td></tr>
    <tr><td>Up/down volume ratio 20d / 50d</td><td>${fx(fl.updown_volume_20d)} / ${fx(fl.updown_volume_50d)}</td></tr>
    <tr><td>High-volume days (60d)</td><td>${fl.high_volume_days.length} (${fl.high_volume_days.filter((h) => h.type === "accumulation").length} accumulation, ${fl.high_volume_days.filter((h) => h.type === "distribution").length} distribution)</td></tr></table>
    <div id="obvchart" style="height:170px"></div>
    <p class="note"><b>Accumulation signals (${n.positive}):</b> ${esc(n.signals_for.join("; ") || "none")}<br><b>Distribution signals (${n.negative}):</b> ${esc(n.signals_against.join("; ") || "none")}<br><b>Neutral:</b> ${esc((n.signals_neutral || []).join("; ") || "none")}<br><b>Missing:</b> ${esc(n.signals_missing.join("; ") || "none")}</p>
    <div class="note">The label needs at least 3 signals one way and more than the other way. The holder data is quarterly and up to ~45 days old; the price and volume signals are current, so the label can disagree with the holder table.</div>
    <div class="note">"Large orders" is a size-based proxy; Longbridge data does not identify who is trading. OBV and CMF infer buying pressure from price and volume.</div></div></div>`;
  if (window.Plotly) {
    Plotly.newPlot("obvchart", [{ type: "scatter", mode: "lines", x: fl.dates, y: fl.obv, name: "OBV", line: { color: css("--accent"), width: 1.5 } }],
      { margin: { l: 40, r: 6, t: 4, b: 20 }, paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)", font: { color: css("--muted"), size: 10 }, xaxis: { gridcolor: css("--line") }, yaxis: { gridcolor: css("--line") }, showlegend: false },
      { displayModeBar: false, responsive: true });
  }
}

// ---------------------------------------------------------------- news
function renderNews(n, limit) {
  const groups = {};
  n.news.forEach((i) => (groups[i.group] = groups[i.group] || []).push(i));
  const mood = n.mood, ar = mood.analyst_consensus, cm = mood.community || {};
  const body = ["Today", "Yesterday", "This week", "Earlier"].filter((g) => groups[g]).map((g) =>
    `<div class="grp">${g}</div>` + groups[g].map((i) => {
      const u = safeUrl(i.url);
      return `<div class="news-item"><div>${u ? `<a href="${esc(u)}" target="_blank" rel="noopener noreferrer">${esc(i.title)}</a>` : esc(i.title)} ${i.stale ? '<span class="tag warn">stale</span>' : ""}</div>
        <div class="meta">${esc(i.publisher)} &middot; ${esc(i.published_at)} &middot; ${esc(i.source)}${i.language && i.language !== "en" ? " &middot; " + esc(i.language) : ""}</div>${i.summary ? `<p>${esc(i.summary)}</p>` : ""}</div>`;
    }).join("")).join("");
  $("news").innerHTML = `<h2>Latest news</h2>
  <div class="two"><div class="card">${body || `<p class="note">${esc(n.note || "No news.")}</p>`}
    ${n.can_show_more && n.news.length >= limit ? `<p><button id="more">Show more</button></p>` : ""}
    ${n.filings.length ? `<div class="grp">Recent filings</div>${n.filings.map((f) => `<div class="news-item">${safeUrl(f.url) ? `<a href="${esc(f.url)}" target="_blank" rel="noopener noreferrer">${esc(f.type)}</a>` : esc(f.type)} <span class="meta">${esc(f.filed_at)}</span></div>`).join("")}` : ""}</div>
   <div class="card"><h3>Market mood (reported facts, no scoring)</h3>
    <table><tr><td>Analyst consensus</td><td>${esc(ar.consensus || "n/a")}: ${ar.buy ?? "-"} Buy / ${ar.hold ?? "-"} Hold / ${ar.sell ?? "-"} Sell, target ${fx(ar.target_mean)}</td></tr>
    <tr><td>Market temperature</td><td>${fx(mood.market_temperature, 0)} / 100</td></tr>
    <tr><td>Community posts</td><td>${cm.posts ?? "n/a"}${cm.volume_vs_30d ? ` (${fx(cm.volume_vs_30d, 1)}x the 30-day average)` : ""}</td></tr>
    <tr><td>Short interest</td><td>${na(mood.short_interest_pct) ? "n/a" : mood.short_interest_pct + "%"}</td></tr></table>
    ${(cm.latest || []).map((p) => `<div class="meta">${safeUrl(p.url) ? `<a href="${esc(p.url)}" target="_blank" rel="noopener noreferrer">${esc(p.title)}</a>` : esc(p.title)}</div>`).join("")}
    <div class="note">Headlines are not classified as positive or negative; automated text sentiment is unreliable. Read the items and judge for yourself.</div></div></div>`;
  const more = $("more");
  if (more) more.addEventListener("click", async () => {
    more.disabled = true;
    try { renderNews(await api(`/api/news?symbol=${current.symbol}&limit=25`), 25); } catch (e) { setStatus(e.message, true); }
  });
}

function renderSources(a, haltedOnly = false) {
  const warn = a.warnings && a.warnings.length ? `<div class="banner">Some data could not be fetched: ${a.warnings.map(esc).join("; ")}</div>` : "";
  const rows = (a.sources || []).map((s) => `<tr><td>${esc(s.field)}</td><td>${esc(s.formula)}</td><td>${esc(s.source)}</td><td>${esc(s.period)}</td><td>${esc(s.fetched_at)}</td></tr>`).join("");
  $("sources").innerHTML = `${warn}<h2>Data sources</h2><div class="card">${rows ? `<table><tr><th>Field</th><th>Definition</th><th>Source</th><th>Period</th><th>Fetched</th></tr>${rows}</table>` : ""}
   <p class="note"><b>Reconciliation:</b> ${esc(a.reconciliation.summary)}</p>${haltedOnly ? "" : reconTable(a.reconciliation)}</div>`;
}

// ---------------------------------------------------------------- options
let optState = { data: null, sort: { key: "pop_model", dir: -1 }, selected: 0 };

function initOptions(waiting = "Loading option chains...") {
  $("options").innerHTML = `<h2>Option trades</h2>
  <div class="card optctl">
    <label>View <select id="o_view"><option value="auto">Auto (from the analysis)</option><option value="bullish">Bullish</option><option value="neutral">Neutral</option><option value="bearish">Bearish</option></select></label>
    <label>Days to expiry <input id="o_dmin" type="number" min="7" max="400" value="21" style="width:70px"> to <input id="o_dmax" type="number" min="7" max="400" value="60" style="width:70px"></label>
    <label><input id="o_def" type="checkbox" checked> Defined risk only</label>
    <label title="The strategy matrix sells premium only when IV is rich. Tick this to also see credit spreads and condors when IV is fair or cheap; they are flagged as having no IV edge."><input id="o_cr" type="checkbox" checked> Include credit spreads in any IV</label>
    <label title="A credit spread must collect at least this share of its wing width. Lower it to see thinner-paying spreads (more risk per dollar collected).">Min credit <input id="o_mc" type="number" min="0" max="60" step="5" value="25" style="width:60px">% of width</label>
    <label>Shares owned <input id="o_sh" type="number" min="0" step="100" value="0" style="width:90px"></label>
    <button id="o_go" type="button">Scan</button>
  </div>
  <div id="optres"><p class="note">${esc(waiting)}</p></div>`;
  $("o_go").addEventListener("click", loadOptions);
}

async function loadOptions() {
  if (!current.symbol) return;
  const q = new URLSearchParams({ symbol: current.symbol, view: $("o_view").value, dte_min: $("o_dmin").value || 21, dte_max: $("o_dmax").value || 60,
    defined_risk: $("o_def").checked, shares: $("o_sh").value || 0, include_credit: $("o_cr").checked, min_credit: (($("o_mc").value || 25) / 100) });
  const sym = current.symbol;
  $("o_go").disabled = true;
  $("optres").innerHTML = `<p class="note">Scanning option chains for ${esc(sym)}... (this makes several data calls and can take 10-30 seconds)</p>`;
  try {
    const data = await api(`/api/options?${q}`);
    if (current.symbol !== sym) return;  // the user searched another symbol meanwhile
    optState.data = data;
    optState.selected = 0;
    renderOptions();
  } catch (e) {
    if (current.symbol === sym) $("optres").innerHTML = `<div class="banner">Option scan failed: ${esc(e.message)}</div>`;
  } finally { $("o_go").disabled = false; }
}

function money(x) { return na(x) ? "n/a" : (x < 0 ? "-$" : "$") + Math.abs(x).toLocaleString(undefined, { maximumFractionDigits: 0 }); }

function renderOptions() {
  const o = optState.data;
  if (!o.supported || (!o.candidates.length && !o.iv)) {
    $("optres").innerHTML = `<div class="card"><p>${esc(o.message || "No option data.")}</p></div>`;
    return;
  }
  const iv = o.iv, v = o.view, pc = iv.put_call.latest;
  const regTag = iv.regime === "rich" ? "bad" : iv.regime === "cheap" ? "good" : "warn";
  const vTag = v.value === "bullish" ? "good" : v.value === "bearish" ? "bad" : "warn";
  const cards = [
    card("ATM IV", pct(iv.atm_iv), `expiry ${esc(iv.focus_expiry || "n/a")}`),
    card("HV20 / HV60", `${pct(iv.hv20, 0)} / ${pct(iv.hv60, 0)}`, "realized, from daily bars"),
    card("IV / HV60", na(iv.iv_hv) ? "n/a" : fx(iv.iv_hv, 2), `<span class="tag ${regTag}">${esc(iv.regime)}</span> rich &gt;${iv.thresholds.rich}, cheap &lt;${iv.thresholds.cheap}`),
    card("IV percentile", na(iv.percentile) ? "n/a" : iv.percentile + "th", esc(iv.percentile_note || `${iv.observations} stored observations`)),
    card("Put/call volume", pc ? fx(pc.pc_volume, 2) : "n/a", pc ? `open interest ratio ${fx(pc.pc_oi, 2)} (${esc(pc.date)})` : ""),
    card("Skew (25-delta)", iv.skew && !na(iv.skew.put_25d_minus_call_25d) ? (iv.skew.put_25d_minus_call_25d * 100).toFixed(1) + " pts" : "n/a", iv.skew && !na(iv.skew.otm_put_minus_atm) ? `OTM put minus ATM ${(iv.skew.otm_put_minus_atm * 100).toFixed(1)} pts` : "put IV minus call IV"),
    card("Next earnings", esc(o.earnings_date || "unknown"), o.earnings_date ? "" : "check before trading"),
  ];
  const tally = v.inputs.map((i) => `<li>${esc(i.name)}: <b>${i.points > 0 ? "+" : ""}${i.points}</b> (${esc(i.detail)})</li>`).join("");
  const dropped = Object.entries(o.dropped || {}).map(([k, n]) => `${n} ${k.replace(/_/g, " ")}`).join(", ");
  $("optres").innerHTML = `
  <div class="two"><div class="card"><h3>Market view: <span class="tag ${vTag}">${esc(v.value)}</span> ${v.mode === "override" ? `<span class="note">(override; auto says ${esc(v.auto_value)})</span>` : ""}</h3>
    <p class="note">Automatic score ${v.score > 0 ? "+" : ""}${v.score}. ${esc(v.rule)}.</p><ul class="plain">${tally}</ul></div>
   <div class="grid" style="grid-template-columns:repeat(2,1fr)">${cards.join("")}</div></div>
  <div class="two" style="margin-top:12px"><div class="card"><h3>Term structure (ATM IV by expiry)</h3><div id="o_term" style="height:200px"></div></div>
   <div class="card"><h3>Skew: IV by strike, ${esc(iv.focus_expiry || "")}</h3><div id="o_skew" style="height:200px"></div></div></div>
  <div class="card" style="margin-top:12px"><h3>Put/call volume ratio, daily</h3><div id="o_pc" style="height:150px"></div></div>
  <div class="card" style="margin-top:12px"><h3>Candidate trades (${o.candidates.length}) ${o.defined_risk_only ? '<span class="tag">defined risk only</span>' : '<span class="tag bad">undefined risk allowed</span>'}</h3>
    <div style="overflow-x:auto" id="o_table"></div>
    <p class="note">Sorted by probability of profit by default; click a column to sort. No composite score. Filtered out: ${esc(dropped || "nothing")}. ${o.notes.map(esc).join(" ")}</p>
    ${(o.dropped || {}).credit_too_small ? `<p class="note"><b>${o.dropped.credit_too_small} credit spread(s) are hidden</b> because they pay less than ${Math.round((o.min_credit ?? 0.25) * 100)}% of their width. Lower "Min credit" and press Scan to see them; they risk more for each dollar collected.</p>` : ""}
    ${o.include_credit === false ? `<p class="note">Credit spreads are off outside rich IV. Tick "Include credit spreads in any IV" to add them.</p>` : ""}</div>
  <div id="o_detail"></div>`;
  drawOptionCharts();
  renderOptionTable();
}

const COLS = [
  ["strategy", "Strategy", (c) => esc(c.strategy) + (c.net.type === "credit" ? ' <span class="tag good">credit</span>' : ' <span class="tag">debit</span>')],
  ["dte", "Expiry", (c) => `${esc(c.expiry)}${c.far_expiry ? " / " + esc(c.far_expiry) : ""}<div class="s">${c.dte} days</div>`],
  ["net", "Net", (c) => `${c.net.type === "credit" ? "+" : "-"}${money(c.net.dollars)}<div class="s">${c.net.type}</div>`],
  ["max_profit", "Max profit", (c) => c.unlimited_profit ? "unlimited" : money(c.max_profit)],
  ["max_loss", "Max loss", (c) => c.unlimited_loss ? '<span class="tag bad">unlimited</span>' : money(c.max_loss)],
  ["be", "Breakeven", (c) => c.breakevens.map((b) => fx(b)).join(" / ") || "n/a"],
  ["pop_model", "POP (model)", (c) => na(c.pop_model) ? "n/a" : (c.pop_model * 100).toFixed(0) + "%"],
  ["return_on_risk", "Return / risk", (c) => na(c.return_on_risk) ? "n/a" : fx(c.return_on_risk, 2)],
  ["delta", "Delta (sh)", (c) => fx(c.greeks.delta, 1)],
  ["theta", "Theta ($/day)", (c) => fx(c.greeks.theta, 2)],
  ["flags", "Flags", (c) => `<span class="tag ${c.flags.length > 2 || !c.defined_risk ? "warn" : ""}">${c.flags.length}</span>`],
];
const SORTV = {
  strategy: (c) => c.strategy, dte: (c) => c.dte, net: (c) => (c.net.type === "credit" ? 1 : -1) * c.net.dollars,
  max_profit: (c) => (c.unlimited_profit ? 1e12 : c.max_profit ?? -1e12), max_loss: (c) => (c.unlimited_loss ? 1e12 : c.max_loss ?? 1e12),
  be: (c) => c.breakevens[0] ?? 0, pop_model: (c) => c.pop_model ?? -1, return_on_risk: (c) => c.return_on_risk ?? -1,
  delta: (c) => c.greeks.delta, theta: (c) => c.greeks.theta, flags: (c) => c.flags.length,
};

function renderOptionTable() {
  const o = optState.data, { key, dir } = optState.sort;
  const idx = o.candidates.map((c, i) => [c, i]).sort((a, b) => {
    const x = SORTV[key](a[0]), y = SORTV[key](b[0]);
    return (x < y ? -1 : x > y ? 1 : 0) * dir;
  });
  if (!idx.length) { $("o_table").innerHTML = `<p class="note">No candidates passed the filters for this view and IV level.</p>`; $("o_detail").innerHTML = ""; return; }
  $("o_table").innerHTML = `<table><tr>${COLS.map(([k, l]) => `<th data-k="${k}" style="cursor:pointer">${l}${key === k ? (dir > 0 ? " ▲" : " ▼") : ""}</th>`).join("")}</tr>
    ${idx.map(([c, i]) => `<tr data-i="${i}" style="cursor:pointer;${i === optState.selected ? "background:var(--warn-bg)" : ""}">${COLS.map(([, , f]) => `<td>${f(c)}</td>`).join("")}</tr>`).join("")}</table>`;
  $("o_table").querySelectorAll("th").forEach((th) => th.addEventListener("click", () => {
    const k = th.dataset.k;
    optState.sort = { key: k, dir: optState.sort.key === k ? -optState.sort.dir : -1 };
    renderOptionTable();
  }));
  $("o_table").querySelectorAll("tr[data-i]").forEach((tr) => tr.addEventListener("click", () => { optState.selected = +tr.dataset.i; renderOptionTable(); }));
  renderOptionDetail(o.candidates[optState.selected] || idx[0][0]);
}

function renderOptionDetail(c) {
  const legs = c.legs.map((l) => l.kind === "S"
    ? `<tr><td>BUY</td><td>${l.qty * 100} shares</td><td>stock</td><td></td><td>${fx(l.last)}</td><td></td><td></td></tr>`
    : `<tr><td>${l.side.toUpperCase()}</td><td>${l.qty} x ${l.kind === "C" ? "Call" : "Put"} ${fx(l.strike)}</td><td>${esc(l.expiry)}</td><td><code>${esc(l.occ)}</code></td><td>${fx(l.last)}</td><td>${na(l.iv) ? "" : pct(l.iv, 0)}</td><td>${na(l.delta) ? "" : fx(l.delta, 2)}</td></tr>`).join("");
  $("o_detail").innerHTML = `<div class="two" style="margin-top:12px"><div class="card"><h3>${esc(c.strategy)}, ${esc(c.expiry)}</h3>
    <p>${esc(c.rationale)}</p>
    <table><tr><th>Side</th><th>Contract</th><th>Expiry</th><th>Symbol</th><th>Last</th><th>IV</th><th>Delta</th></tr>${legs}</table>
    <p class="note">Net ${c.net.type}: $${fx(c.net.per_share)} per share ($${fx(c.net.dollars)} per set). Expected move to expiry about &plusmn;$${fx(c.expected_move)}${c.short_strikes_outside_expected_move === null ? "" : c.short_strikes_outside_expected_move ? "; short strikes are outside it" : "; a short strike is inside it"}.
    Net Greeks (model): delta ${fx(c.greeks.delta, 1)} shares, gamma ${fx(c.greeks.gamma, 3)}, theta ${fx(c.greeks.theta, 2)} $/day, vega ${fx(c.greeks.vega, 2)} $ per IV point.${c.credit_pct_width !== null && c.credit_pct_width !== undefined ? ` Credit is ${(c.credit_pct_width * 100).toFixed(0)}% of the widest wing.` : ""}</p>
    <ul class="plain">${c.flags.map((f) => `<li>${esc(f)}</li>`).join("")}</ul></div>
   <div class="card"><h3>Payoff</h3><div id="o_payoff" style="height:300px"></div></div></div>`;
  const p = c.payoff;
  if (window.Plotly && p) {
    const ink = css("--ink"), muted = css("--muted"), line = css("--line");
    Plotly.newPlot("o_payoff", [
      { x: p.prices, y: p.at_expiry, type: "scatter", mode: "lines", name: "At expiry", line: { color: css("--accent"), width: 2 } },
      { x: p.prices, y: p.today_model, type: "scatter", mode: "lines", name: "Today (model)", line: { color: muted, width: 1.5, dash: "dot" } },
      { x: c.breakevens, y: c.breakevens.map(() => 0), type: "scatter", mode: "markers", name: "Breakeven", marker: { color: css("--warn"), size: 9 } },
    ], { margin: { l: 55, r: 10, t: 6, b: 32 }, paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)", font: { color: ink, size: 11 },
      xaxis: { gridcolor: line, title: "Price at expiry" }, yaxis: { gridcolor: line, zeroline: true, zerolinecolor: muted, title: "Profit / loss ($)" },
      shapes: [{ type: "line", x0: optState.data.spot, x1: optState.data.spot, yref: "paper", y0: 0, y1: 1, line: { color: muted, width: 1, dash: "dash" } }],
      legend: { orientation: "h", y: 1.12 } }, { displayModeBar: false, responsive: true });
  }
}

function drawOptionCharts() {
  if (!window.Plotly) return;
  const o = optState.data, iv = o.iv, ink = css("--ink"), muted = css("--muted"), line = css("--line");
  const base = { margin: { l: 45, r: 8, t: 4, b: 28 }, paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)", font: { color: ink, size: 10 }, showlegend: false };
  const cfg = { displayModeBar: false, responsive: true };
  const t = iv.term_structure.filter((x) => x.atm_iv !== null);
  Plotly.newPlot("o_term", [{ x: t.map((x) => x.expiry), y: t.map((x) => x.atm_iv * 100), type: "scatter", mode: "lines+markers", line: { color: css("--accent") },
    text: t.map((x) => x.dte + " days"), hovertemplate: "%{x} (%{text}): %{y:.1f}%<extra></extra>" }],
    { ...base, xaxis: { gridcolor: line, type: "category" }, yaxis: { gridcolor: line, title: "ATM IV %" } }, cfg);
  const pts = (iv.skew && iv.skew.points) || [];
  const puts = pts.filter((p) => p.side === "put"), calls = pts.filter((p) => p.side === "call");
  Plotly.newPlot("o_skew", [
    { x: puts.map((p) => p.strike), y: puts.map((p) => p.iv * 100), type: "scatter", mode: "lines+markers", name: "OTM puts", line: { color: css("--down") }, marker: { size: 4 } },
    { x: calls.map((p) => p.strike), y: calls.map((p) => p.iv * 100), type: "scatter", mode: "lines+markers", name: "OTM calls", line: { color: css("--up") }, marker: { size: 4 } }],
    { ...base, showlegend: true, legend: { orientation: "h", y: 1.2 }, xaxis: { gridcolor: line, title: "Strike" }, yaxis: { gridcolor: line, title: "IV %" },
      shapes: [{ type: "line", x0: o.spot, x1: o.spot, yref: "paper", y0: 0, y1: 1, line: { color: muted, width: 1, dash: "dash" } }] }, cfg);
  const h = iv.put_call.history.filter((x) => x.pc_volume !== null);
  Plotly.newPlot("o_pc", [{ x: h.map((x) => x.date), y: h.map((x) => x.pc_volume), type: "scatter", mode: "lines", line: { color: css("--accent") } }],
    { ...base, xaxis: { gridcolor: line }, yaxis: { gridcolor: line, title: "puts / calls" },
      shapes: [{ type: "line", xref: "paper", x0: 0, x1: 1, y0: 1, y1: 1, line: { color: muted, width: 1, dash: "dot" } }] }, cfg);
}
