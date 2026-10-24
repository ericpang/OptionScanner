// Harness for tests/test_cache.py. Loads web/app.js in a stubbed browser, hands the `cache` block of each response in the JSON
// file to the page's cache note, one after the other as the panels' responses arrive, and prints the note's text after each.
// usage: node tests/cache_note.js web/app.js caches.json
const fs = require("fs"), vm = require("vm");

const [appJs, cachesFile] = process.argv.slice(2);
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

const text = (s) => s.replace(/<[^>]+>/g, "");
vm.runInContext('current = { symbol: "AAPL.US", cache: null }', ctx);
const note = vm.runInContext("noteCache", ctx);
const out = JSON.parse(fs.readFileSync(cachesFile, "utf8")).map((c) => { note(c); return { text: text(els.cachenote.innerHTML), html: els.cachenote.innerHTML }; });
console.log(JSON.stringify(out));
