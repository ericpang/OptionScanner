// Harness for tests/test_layout.py. Loads web/app.js in a stubbed browser, draws the /api/analyze response in the scenario file (and, when
// it has one, the /api/flows response) the way the page does, and prints what each section holds, as JSON, with the Plotly calls made.
// The scenario file holds: analysis (the /api/analyze response); flows (the /api/flows response, optional).
// usage: node tests/panels.js web/app.js scenario.json
const fs = require("fs"), vm = require("vm");

const [appJs, scenarioFile] = process.argv.slice(2);
const sc = JSON.parse(fs.readFileSync(scenarioFile, "utf8"));
const els = {}, plots = [];
const el = (id) => (els[id] ||= { id, innerHTML: "", textContent: "", hidden: false, className: "", dataset: {}, children: [],
  classList: { toggle() {} }, addEventListener() {}, querySelector: () => null, after() {},
  insertAdjacentHTML(where, html) { if (where === "beforeend") this.innerHTML += html; } });
const ctx = {
  document: { getElementById: el, addEventListener() {}, querySelector: () => null, createElement: () => el("created"), documentElement: {} },
  window: { matchMedia: () => ({ addEventListener() {} }), Plotly: { newPlot: (id) => plots.push(id), react: (id) => plots.push(id) } },
  getComputedStyle: () => ({ getPropertyValue: () => "" }), location: { search: "" }, history: {}, localStorage: {}, fetch() {}, URLSearchParams,
};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(appJs, "utf8"), ctx);

vm.runInContext(`current.symbol = ${JSON.stringify(sc.analysis.symbol)};`, ctx);
vm.runInContext("renderAnalysis", ctx)(sc.analysis);
if (sc.flows) vm.runInContext("renderFlows", ctx)(sc.flows);
const sections = {};
for (const id of ["header", "recon", "mode", "warnings", "thesis", "quality", "valuation", "metrics", "filter", "flows", "sources"]) sections[id] = els[id] ? els[id].innerHTML : null;
console.log(JSON.stringify({ sections, plots }));
