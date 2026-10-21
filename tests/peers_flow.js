// Harness for tests/test_benchmarks.py. Drives the page's peer loading in a stubbed browser with a scripted `fetch`, and
// prints what happened in each scenario as JSON: the request made, what the panels showed, and whether a search waited.
// usage: node tests/peers_flow.js web/app.js analyze.json peers.json
const fs = require("fs"), vm = require("vm");

const [appJs, analyzeFile, peersFile] = process.argv.slice(2);
const analysis = JSON.parse(fs.readFileSync(analyzeFile, "utf8"));
const peers = JSON.parse(fs.readFileSync(peersFile, "utf8"));
const text = (s) => String(s).replace(/<br>/g, "\n").replace(/<[^>]+>/g, "");
const reply = (data, ok = true, status = 200) => Promise.resolve({ ok, status, json: () => Promise.resolve(data) });

function browser(fetchImpl) {
  const els = {};
  const el = (id) => (els[id] ||= { id, innerHTML: "", textContent: "", hidden: false, className: "", dataset: {}, children: [], value: "",
    classList: { toggle() {} }, addEventListener() {}, insertAdjacentHTML() {}, querySelector: () => null, after() {} });
  const ctx = {
    document: { getElementById: el, addEventListener() {}, querySelector: () => null, createElement: () => el("created"), documentElement: {} },
    window: { matchMedia: () => ({ addEventListener() {} }) }, getComputedStyle: () => ({ getPropertyValue: () => "" }),
    location: { search: "" }, history: { replaceState() {} }, localStorage: {}, fetch: fetchImpl, URLSearchParams, setTimeout,
  };
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync(appJs, "utf8"), ctx);
  ctx.__analysis = analysis;
  return { ctx, els, run: (code) => vm.runInContext(code, ctx) };
}

// loadPeers on a page that has the analysis up. `during` runs while the request is in flight.
async function load({ answer = () => reply(peers), setup = "", during = () => {}, analysisCode = "__analysis" } = {}) {
  const urls = []; let loading = "";
  const b = browser((u) => { urls.push(u); loading = text(b.els.peers ? b.els.peers.innerHTML : ""); return answer(u); });
  b.run(`current = { symbol: "AAPL.US", analysis: ${analysisCode}, peers: null }; ${setup}`);
  const p = b.run(`loadPeers("AAPL.US")`);
  during(b);
  await p;
  return { b, urls, loading, metricsHavePeerLines: (b.els.metrics ? b.els.metrics.innerHTML : "").includes("Peer median"),
    peersSet: b.run("current.peers !== null"), panel: text(b.els.peers ? b.els.peers.innerHTML : "") };
}

(async () => {
  const out = {};
  const n = await load();
  out.normal = { urls: n.urls, loading: n.loading, metricsHavePeerLines: n.metricsHavePeerLines, peersSet: n.peersSet, panel: n.panel.trim().slice(0, 40) };

  const r = await load({ setup: "forcePeers = true;" });
  out.refresh = { urls: r.urls, flagAfter: r.b.run("forcePeers") };

  let release; const gate = new Promise((res) => (release = res));
  const s = await load({ answer: () => gate.then(() => reply(peers)), during: (b) => { b.run(`current.symbol = "MSFT.US"`); release(); } });
  out.stale = { metricsHavePeerLines: s.metricsHavePeerLines, peersSet: s.peersSet };   // the user searched something else meanwhile

  const e = await load({ answer: () => reply({ error: "boom" }, false, 502) });
  out.error = { panel: e.panel };

  out.halted = { urls: (await load({ analysisCode: "{ halted: true }" })).urls };
  out.noAnalysis = { urls: (await load({ analysisCode: "null" })).urls };

  // A whole search: the peers are asked for as soon as the analysis is up, and the search does not wait for them.
  const urls = [];
  const b = browser((u) => {
    urls.push(u);
    if (u.startsWith("/api/peers")) return new Promise(() => {});       // never answers
    return u.startsWith("/api/analyze") ? reply(analysis) : reply({});
  });
  let timer;
  const done = await Promise.race([b.run(`run("AAPL.US")`).then(() => "finished"), new Promise((res) => (timer = setTimeout(() => res("waited"), 3000)))]);
  clearTimeout(timer);
  out.search = { done, peersAsked: urls.some((u) => u.startsWith("/api/peers?symbol=AAPL.US")), filingsAsked: urls.some((u) => u.startsWith("/api/filings")) };
  console.log(JSON.stringify(out));
})();
