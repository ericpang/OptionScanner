// Harness for tests/test_twostage.py. Loads web/app.js in a stubbed browser and renders the Two-stage filter section for a scenario,
// then prints the section's HTML, the request the stored inputs would make, and the concentration flag as JSON.
// The scenario file holds: symbol; filter (the /api/filter response); analysis (the /api/analyze response, optional: the evidence under the
// judgment questions); stored (what this browser has kept for the symbol, optional); filing (the /api/filings response, optional).
// usage: node tests/filter_card.js web/app.js scenario.json
const fs = require("fs"), vm = require("vm");

const [appJs, scenarioFile] = process.argv.slice(2);
const sc = JSON.parse(fs.readFileSync(scenarioFile, "utf8"));
const els = {}, kept = {};
if (sc.stored) kept[`tsf:${sc.symbol}`] = JSON.stringify(sc.stored);
const el = (id) => (els[id] ||= { id, innerHTML: "", textContent: "", hidden: false, className: "", dataset: {}, children: [],
  classList: { toggle() {} }, addEventListener() {}, insertAdjacentHTML() {}, querySelector: () => null, after() {} });
const ctx = {
  document: { getElementById: el, addEventListener() {}, querySelector: () => null, createElement: () => el("created"), documentElement: {} },
  window: { matchMedia: () => ({ addEventListener() {} }) },
  getComputedStyle: () => ({ getPropertyValue: () => "" }), location: { search: "" }, history: {}, fetch() {}, URLSearchParams,
  localStorage: { getItem: (k) => (k in kept ? kept[k] : null), setItem: (k, v) => { kept[k] = String(v); }, removeItem: (k) => { delete kept[k]; } },
};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(appJs, "utf8"), ctx);

vm.runInContext(`current.symbol = ${JSON.stringify(sc.symbol)}; current.analysis = ${JSON.stringify(sc.analysis || null)}; current.filing = ${JSON.stringify(sc.filing || null)};`, ctx);
vm.runInContext("renderFilter", ctx)(sc.filter);
const stored = vm.runInContext("tsGet", ctx)(sc.symbol);
console.log(JSON.stringify({ html: els.filter.innerHTML, query: vm.runInContext("tsQuery", ctx)(sc.symbol, stored).toString(), conc: els.tsconc ? els.tsconc.innerHTML : null }));
