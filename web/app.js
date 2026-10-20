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

// A cached or older index.html may lack a section this script renders into. Create it rather than failing,
// so one missing element can never make a panel (or the whole run) disappear.
function ensureSections() {
  const order = ["header", "recon", "quality", "valuation", "thesis", "metrics", "chartsec", "flows", "options", "news", "sources"];
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

document.addEventListener("DOMContentLoaded", () => {
  $("form").addEventListener("submit", (e) => { e.preventDefault(); run($("symbol").value); });
  $("ranges").addEventListener("click", async (e) => {
    const b = e.target.closest("button");
    if (!b || !current.symbol) return;
    current.range = b.dataset.r;
    [...$("ranges").children].forEach((x) => x.classList.toggle("on", x === b));
    try { current.chart = await api(`/api/chart?symbol=${current.symbol}&range=${current.range}`); drawChart(); } catch (err) { setStatus(err.message, true); }
  });
  const q = new URLSearchParams(location.search).get("symbol");
  if (q) { $("symbol").value = q; run(q); }
});

async function run(raw) {
  const symbol = (raw || "").trim().toUpperCase();
  if (!symbol) return;
  ensureSections();
  ["header", "recon", "quality", "valuation", "thesis", "metrics", "flows", "options", "news", "sources", "mode", "cachenote"].forEach((i) => ($(i).innerHTML = ""));
  $("chartsec").hidden = true;
  setStatus(`Analyzing ${symbol}...`);
  current = { symbol, range: "1y", chart: null, flows: null, cache: null };
  [...$("ranges").children].forEach((x) => x.classList.toggle("on", x.dataset.r === "1y"));
  history.replaceState(null, "", `?symbol=${encodeURIComponent(symbol)}`);

  initOptions("Waiting for the analysis panels to finish, then scanning option chains (10-30 seconds)...");
  // Only these four requests say where the data came from. Later ones (a chart range, "Show more", the option scan)
  // read the cache file these just wrote, which would make a live search look like a cached one.
  const seen = (r) => { noteCache(r.cache); return r; };
  const jobs = [
    api(`/api/analyze?symbol=${symbol}`).then(seen).then(renderAnalysis),
    api(`/api/chart?symbol=${symbol}&range=1y`).then(seen).then((c) => { current.chart = c; $("chartsec").hidden = false; drawChart(); }),
    api(`/api/flows?symbol=${symbol}`).then(seen).then((f) => { current.flows = f; renderFlows(f); drawChart(); }),
    api(`/api/news?symbol=${symbol}&limit=10`).then(seen).then((n) => renderNews(n, 10)),
  ];
  const res = await Promise.allSettled(jobs);
  const errs = res.filter((r) => r.status === "rejected").map((r) => r.reason.message);
  setStatus(errs.length ? [...new Set(errs)].join(" | ") : "", errs.length > 0);
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
    + (expires ? ` Searching again after ${esc(expires)} fetches fresh data.` : "") + `</div>`;
}

// ---------------------------------------------------------------- analysis
function renderAnalysis(a) {
  if (a.mode === "demo") $("mode").innerHTML = `<div class="banner">DEMO DATA: synthetic numbers for trying the app. Not real market data.</div>`;
  const s = a.snapshot;
  $("header").innerHTML = `<div class="hdr">
    <div><span class="nm">${esc(s.name)}</span><span class="k">${esc(s.symbol)} ${esc(s.industry || "")}</span></div>
    <div><span class="px">${fx(s.price)}</span><span class="k">${esc(s.currency || "")} last</span></div>
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
  renderMetrics(a.metrics, a.snapshot);
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

function renderValuation(v, snap) {
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

function renderThesis(a) {
  const pts = (arr, none) => arr.length ? arr.map((p) => `<div class="pt"><b>${esc(p.title)}</b><span>${esc(p.evidence)} <em>[${esc(p.source)}]</em></span></div>`).join("") : `<p class="note">${none}</p>`;
  $("thesis").innerHTML = `<h2>Bull and bear case</h2>
  <div class="two">
   <div class="card col bull"><h3 class="b">Bull case (${a.bull.length})</h3>${pts(a.bull, "No supporting points met the rules.")}</div>
   <div class="card col bear"><h3 class="r">Bear case (${a.bear.length})</h3>${pts(a.bear, "No cautionary points met the rules.")}</div></div>
  <div class="card" style="margin-top:12px"><h3>Verdict: <span class="tag warn">${esc(a.verdict.tag)}</span></h3>
   <p>${esc(a.verdict.summary)}</p>
   ${a.narrative ? `<p class="narr">${esc(a.narrative)}</p>` : ""}
   <h3>Reference price levels</h3>
   <table>${a.levels.map((l) => `<tr><td>${esc(l.label)}</td><td>${fx(l.price)}</td><td>${pct(l.price / a.snapshot.price - 1)} vs price</td></tr>`).join("")}</table>
   <div class="note">Levels are reference points from analyst targets, the volume profile, Graham and the 52-week range. They are not forecasts.</div></div>`;
}

function renderMetrics(m, snap) {
  const x = m.extra;
  const peg = m.peg, d = m.debt_to_ebitda, ar = m.analyst_ratings;
  const A = [
    card("P/E (TTM)", fx(m.pe_ttm.value, 1), m.pe_ttm.basis ? "basis: " + m.pe_ttm.basis : ""),
    card("PEG", fx(peg.value), peg.growth_basis ? `growth ${fx(peg.growth_pct, 1)}% (${peg.growth_basis.replace(/_/g, " ")}); ${esc(peg.pe_basis || "P/E")} ${fx(peg.pe_used, 1)}${peg.note ? ". " + esc(peg.note) : ""}` : "n/a (growth not positive)", tag(peg.value, 1, 2, false)),
    card("FCF growth", pct(m.fcf_growth.yoy), `3y CAGR ${pct(m.fcf_growth.cagr_3y)}; FCF ${big(m.fcf_growth.fcf)}`, tag(m.fcf_growth.yoy, 0.0001, 0)),
    card("Debt / EBITDA", d.net_cash ? "net cash" : fx(d.gross, 1) + "x", `net ${fx(d.net, 1)}x; EBITDA ${big(d.ebitda)}`, d.net_cash ? "good" : tag(d.gross, 2, 4, false)),
    card("Analyst ratings", esc(ar.consensus || "n/a"), `${ar.buy ?? "-"} Buy / ${ar.hold ?? "-"} Hold / ${ar.sell ?? "-"} Sell; target ${fx(ar.target_mean)} (${pct(ar.upside_pct)})`, ar.consensus === "Buy" ? "good" : ar.consensus === "Sell" ? "bad" : ""),
    card("Operating margin", pct(m.operating_margin.ttm), "5y: " + m.operating_margin.history.map((v) => pct(v, 0)).join(" ")),
    card("ROIC", m.roic > 1 ? ">100%" : pct(m.roic), m.roic > 1 ? "invested capital is mostly cash, so the exact figure is not meaningful" : "NOPAT / avg invested capital", tag(m.roic, 0.12, 0.08)),
    card("ROE", pct(m.roe.latest), "history: " + m.roe.history.map((v) => pct(v, 0)).join(" "), tag(m.roe.latest, 0.15, 0.10)),
    card("Net income growth", pct(m.net_income_growth.yoy), `3y ${pct(m.net_income_growth.cagr_3y)}, 5y ${pct(m.net_income_growth.cagr_5y)}, last qtr YoY ${pct(m.net_income_growth.latest_quarter_yoy)}`, tag(m.net_income_growth.cagr_3y, 0.10, 0)),
  ];
  const B = [
    card("Revenue growth", pct(x.revenue_growth.yoy), `3y ${pct(x.revenue_growth.cagr_3y)}, 5y ${pct(x.revenue_growth.cagr_5y)}`),
    card("EPS revisions / surprises", pct(x.eps_revisions.forward_vs_90d), "last 4 surprises: " + (x.eps_revisions.surprises_last4.map((v) => pct(v, 0)).join(" ") || "n/a")),
    card("Gross margin", pct(x.gross_margin.latest), "5y: " + x.gross_margin.history.map((v) => pct(v, 0)).join(" "), tag(x.gross_margin.latest, 0.30, 0.20)),
    card("Non-operating income", na(x.non_operating_share) ? "n/a" : pct(x.non_operating_share, 0), "of pre-tax income (TTM): investment gains and interest, which flatter net income", na(x.non_operating_share) ? "" : x.non_operating_share >= 0.25 ? "bad" : x.non_operating_share >= 0.10 ? "warn" : ""),
    card("Net / FCF margin", `${pct(x.net_margin)} / ${pct(x.fcf_margin)}`),
    card("Cash conversion", fx(x.cash_conversion) + "x", "FCF / net income", tag(x.cash_conversion, 0.8, 0.8)),
    card("EV / EBITDA", fx(x.ev_ebitda, 1)), card("P/S, P/B", `${fx(x.ps, 1)}, ${fx(x.pb, 1)}`), card("FCF yield", pct(x.fcf_yield)),
    card("Interest coverage", na(x.interest_coverage) ? "n/a" : fx(x.interest_coverage, 1) + "x", "", tag(x.interest_coverage, 5, 3)),
    card("Current / quick ratio", `${fx(x.current_ratio)} / ${fx(x.quick_ratio)}`, "", tag(x.current_ratio, 1.5, 1)),
    card("Net debt / equity", fx(x.net_debt_equity)),
    card("Dividend", pct(x.dividend.yield), `payout ${pct(x.dividend.payout, 0)}; years of growth ${x.dividend.years_growth ?? "n/a"}`),
    card("Share count, 3y", pct(x.share_count_change_3y), "buyback yield " + pct(x.buyback_yield), tag(x.share_count_change_3y, -0.0001, 0.03, false)),
    card("Insider net (3m / 6m / 12m)", [x.insider_net.m3, x.insider_net.m6, x.insider_net.m12].map(big).join(" / "), x.insider_net.m12_partial_from ? `12m is partial: data only from ${esc(x.insider_net.m12_partial_from)}` : "open-market buys and sells (Form 4)"),
    card("Short interest", na(x.short_interest_pct) ? "n/a" : x.short_interest_pct + "%", na(x.short_interest_pct) ? "no data source wired in" : ""),
    card("Beta, HV20 / HV60", `${fx(x.beta)}, ${pct(x.hv20, 0)} / ${pct(x.hv60, 0)}`, "max drawdown 1y " + pct(x.max_drawdown_1y, 0)),
    card("Returns 3m / 6m / 12m", [x.returns.m3, x.returns.m6, x.returns.m12].map((v) => pct(v, 0)).join(" / "), "absolute; no index data wired in"),
    card("From 52w high / low", `${pct(x.from_52w_high, 0)} / ${pct(x.from_52w_low, 0)}`, "vs EMA 200: " + pct(x.from_ema200)),
  ];
  $("metrics").innerHTML = `<h2>Key metrics</h2><div class="grid">${A.join("")}</div><h2>Additional metrics</h2><div class="grid">${B.join("")}</div>
   <div class="note">n/a means the input was not in the data; the app never estimates a missing figure. Tags: green favourable, amber neutral, red unfavourable (thresholds in app/config.py).</div>`;
}

// ---------------------------------------------------------------- chart
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
    yaxis: { domain: [0.24, 1], gridcolor: line, side: "left" }, yaxis2: { domain: [0, 0.2], gridcolor: line, side: "left" },
    legend: { orientation: "h", y: 1.06 }, shapes, annotations: ann, hovermode: "x unified", bargap: 0.05,
  };
  Plotly.react("chart", traces, layout, { responsive: true, displaylogo: false });
  const s = c.signals;
  $("chartsig").innerHTML = `${esc(c.approx_profile)}. Price vs EMA 20/50/200: ${["20", "50", "200"].map((p) => `${p}: ${s.above_ema[p] === null ? "n/a" : s.above_ema[p] ? "above" : "below"}`).join(", ")}.
    50/200 cross (last ~60 sessions): ${esc(s.cross_50_200)}. Price vs value area: ${esc(s.price_vs_value_area || "n/a")}. POC ${fx(vp.poc)}, VAL ${fx(vp.val)}, VAH ${fx(vp.vah)}.`;
}
window.matchMedia("(prefers-color-scheme: dark)").addEventListener?.("change", drawChart);

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
