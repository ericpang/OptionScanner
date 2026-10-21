// Harness for tests/test_benchmarks.py. Loads web/app.js in a stubbed browser, renders the Key metrics panel for the
// /api/analyze response in the first JSON file (and, when a second is given, the /api/peers response: the peer lines
// on the cards and the Peer group panel), and prints the metric cards and the panel as JSON, tags stripped from the text.
// usage: node tests/cards.js web/app.js analyze.json [peers.json]
const fs = require("fs"), vm = require("vm");

const [appJs, responseFile, peersFile] = process.argv.slice(2);
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

const a = JSON.parse(fs.readFileSync(responseFile, "utf8"));
const peers = peersFile ? JSON.parse(fs.readFileSync(peersFile, "utf8")) : null;
vm.runInContext("renderMetrics", ctx)(a.metrics, a.snapshot, a.benchmarks, peers);
if (peers) vm.runInContext("renderPeers", ctx)(peers);

const text = (s) => s.replace(/<br>/g, "\n").replace(/<[^>]+>/g, "").replace(/&gt;/g, ">").replace(/&lt;/g, "<").replace(/&amp;/g, "&");
const cards = [...els.metrics.innerHTML.matchAll(/<div class="m ([^"]*)"><div class="l">(.*?)<\/div><div class="v">(.*?)<\/div><div class="s">(.*?)<\/div><\/div>/gs)]
  .map(([, cls, label, value, sub]) => ({ cls: cls.trim(), label: text(label), value: text(value), sub: text(sub), subHtml: sub, tips: sub.match(/title="[^"]*"/g) || [] }));
console.log(JSON.stringify({ cards, peersHtml: els.peers ? els.peers.innerHTML : "", peersText: els.peers ? text(els.peers.innerHTML) : "" }));
