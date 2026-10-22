// Harness for tests/test_obv.py. Loads web/app.js in a stubbed browser, renders the OBV insights panel for the "obv" block
// of the /api/chart response in the JSON file, and prints the panel's HTML.
// usage: node tests/obv_card.js web/app.js chart.json
const fs = require("fs"), vm = require("vm");

const [appJs, responseFile] = process.argv.slice(2);
const els = {};
const el = (id) => (els[id] ||= { id, innerHTML: "", textContent: "", hidden: false, className: "", dataset: {}, children: [],
  classList: { toggle() {} }, addEventListener() {}, insertAdjacentHTML() {}, querySelector: () => null, after() {} });
const ctx = {
  document: { getElementById: el, addEventListener() {}, querySelector: () => null, createElement: () => el("created"), documentElement: {} },
  window: { matchMedia: () => ({ addEventListener() {} }) },
  getComputedStyle: () => ({ getPropertyValue: () => "" }), location: { search: "" }, history: {}, localStorage: {}, fetch() {},
};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(appJs, "utf8"), ctx);

const chart = JSON.parse(fs.readFileSync(responseFile, "utf8"));
vm.runInContext("renderObv", ctx)(chart.obv);
console.log(JSON.stringify({ html: els.obvinsight.innerHTML }));
