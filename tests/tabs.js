// Harness for tests/test_layout.py. Loads web/app.js in a stubbed browser whose tab row and panels remember what the page does to them,
// drives the tabs the way a visitor does (a click, the arrow keys, a stale page with no tab row) and prints what happened as JSON.
// usage: node tests/tabs.js web/app.js
const fs = require("fs"), vm = require("vm");

const [appJs] = process.argv.slice(2);
const TABS = ["overview", "fundamentals", "valuation", "filter", "price", "options", "news"];
const PANELS_TOP = 300, HEADER_HEIGHT = 100;   // where the panels start on the page, and how tall the sticky header is

// A page whose elements are the stubs below, except the ids in `missing`, which it does not have (an older page).
function browser(missing = []) {
  const els = {}, gone = new Set(missing), events = [], scrolls = [], created = [];
  let focused = null, scrollY = 0;
  const el = (id) => {
    if (gone.has(id)) return null;
    return (els[id] ||= {
      id, innerHTML: "", textContent: "", hidden: false, className: "", dataset: id.startsWith("tabbtn-") ? { tab: id.slice(7) } : {}, children: [], tabIndex: 0,
      attrs: {}, handlers: {}, classList: { toggle() {} }, insertAdjacentHTML() {}, querySelector: () => null, after() {},
      addEventListener(type, fn) { (this.handlers[type] ||= []).push(fn); },
      setAttribute(k, v) { this.attrs[k] = String(v); },
      focus() { focused = id; },
      appendChild(c) { this.children.push(c); },
      getBoundingClientRect: () => ({ top: PANELS_TOP - scrollY }),
      parentNode: { offsetHeight: HEADER_HEIGHT },
    });
  };
  const main = { id: "main", children: [], appendChild(c) { this.children.push(c); } };
  const ready = [];
  const ctx = {
    document: { getElementById: el, addEventListener: (type, fn) => type === "DOMContentLoaded" && ready.push(fn), querySelector: (s) => (s === "main" ? main : null),
      createElement: (tag) => { const c = { tag, id: "" }; created.push(c); return c; }, documentElement: {} },
    window: { matchMedia: () => ({ addEventListener() {} }), get scrollY() { return scrollY; }, scrollTo: (x, y) => { scrolls.push([x, y]); scrollY = y; },
      dispatchEvent: (e) => events.push(e.type) },
    Event: class { constructor(type) { this.type = type; } },
    getComputedStyle: () => ({ getPropertyValue: () => "" }), location: { search: "" }, history: {}, localStorage: {}, fetch() {}, URLSearchParams,
  };
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync(appJs, "utf8"), ctx);
  const run = (code) => vm.runInContext(code, ctx);
  return { els, main, events, scrolls, created, ready, run, setScroll: (y) => { scrollY = y; },
    state: () => ({ active: run("activeTab"), selected: TABS.filter((t) => els["tabbtn-" + t] && els["tabbtn-" + t].attrs["aria-selected"] === "true"),
      shown: TABS.filter((t) => els["tab-" + t] && !els["tab-" + t].hidden), tabindex: TABS.map((t) => els["tabbtn-" + t] && els["tabbtn-" + t].tabIndex),
      focused, resizes: events.length }) };
}

const out = {};

// A click on a tab button, and the keys, as the page's own handlers (wired when the page loads) get them.
{
  const b = browser();
  b.ready.forEach((f) => f());   // the page has loaded
  const click = (tab) => b.els.tabs.handlers.click[0]({ target: { closest: () => ({ dataset: { tab } }) } });
  const press = (key) => { let prevented = false; b.els.tabs.handlers.keydown[0]({ key, preventDefault: () => (prevented = true) }); return prevented; };
  out.wired = { click: b.els.tabs.handlers.click.length, keydown: b.els.tabs.handlers.keydown.length };
  b.setScroll(0);
  click("valuation");
  out.click = b.state();
  b.run('showTab("nonsense")');
  out.unknown = b.state();
  out.keys = {};
  for (const key of ["ArrowRight", "End", "ArrowRight", "ArrowLeft", "Home", "a"]) { const prevented = press(key); out.keys[key + "#" + Object.keys(out.keys).length] = { prevented, ...b.state() }; }
}

// The page scrolls to the top of a tab the visitor picks (under the sticky header), but never when the code switches tabs, and never needlessly.
{
  const b = browser();
  b.setScroll(1500); b.run('showTab("news", true)');
  const deep = b.scrolls.slice();
  const b2 = browser();
  b2.setScroll(50); b2.run('showTab("news", true)');
  const b3 = browser();
  b3.setScroll(1500); b3.run('showTab("news")');
  out.scroll = { deep, shallow: b2.scrolls, bycode: b3.scrolls, top: PANELS_TOP - HEADER_HEIGHT };
}

// A section the page lacks is made inside its tab's panel; with no panel either, at the end of the page.
{
  const b = browser(["valuation"]);
  b.run("ensureSections()");
  out.missingSection = { made: b.created.map((c) => [c.tag, c.id]), inPanel: b.els["tab-valuation"].children.map((c) => c.id), inMain: b.main.children.map((c) => c.id) };
  const b2 = browser(["valuation", "tab-valuation", "panels"]);
  b2.run("ensureSections()");
  out.missingPanel = { inMain: b2.main.children.map((c) => c.id) };
  const b3 = browser(["header", "sources"]);   // in no tab, so never in a panel
  b3.run("ensureSections()");
  out.missingUntabbed = { inMain: b3.main.children.map((c) => c.id), inPanels: Object.values(b3.els).flatMap((e) => e.children.map((c) => c.id)) };
}

// An older page without the tab row at all: nothing to switch, nothing to fail.
{
  const b = browser(["tabs", "panels", ...TABS.flatMap((t) => ["tab-" + t, "tabbtn-" + t])]);
  b.ready.forEach((f) => f());
  let threw = null;
  try { b.run('showTab("news"); showTab("filter")'); } catch (e) { threw = String(e); }
  out.stale = { threw, active: b.run("activeTab"), resizes: b.events.length };
}

console.log(JSON.stringify(out));
