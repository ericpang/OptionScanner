"""The page's layout: the tabs, and which section sits in which tab. The structure is checked on web/index.html itself; what the tabs do
and what each section holds is checked by running the real web/app.js under Node (tests/tabs.js, tests/panels.js)."""
import os

os.environ["DATA_MODE"] = "demo"

import json
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

from app import service
from app.data import Data

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="Node.js is not installed")
JS = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
HTML = (ROOT / "web" / "index.html").read_text(encoding="utf-8")


class Page(HTMLParser):
    """index.html as a list of elements, each with its attributes and the elements it sits inside."""
    VOID = {"meta", "link", "input", "br", "img", "hr"}

    def __init__(self):
        super().__init__()
        self.stack, self.nodes = [], []

    def handle_starttag(self, tag, attrs):
        node = {"tag": tag, "attrs": dict(attrs), "parents": list(self.stack), "text": ""}
        self.nodes.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_endtag(self, tag):
        while self.stack and self.stack.pop()["tag"] != tag:
            pass

    def handle_data(self, data):
        for n in self.stack:
            n["text"] += data

    def find(self, **attrs):
        return [n for n in self.nodes if all(n["attrs"].get(k.replace("_", "-")) == v for k, v in attrs.items())]

    def by_id(self, id_):
        (node,) = self.find(id=id_)
        return node


PAGE = Page()
PAGE.feed(HTML)
inside = lambda node, id_: any(p["attrs"].get("id") == id_ for p in node["parents"])

TABS = re.findall(r'"(\w+)"', re.search(r"const TABS = \[(.*?)\];", JS, re.S).group(1))
SECTION_TAB = dict(re.findall(r'(\w+): "(\w+)"', re.search(r"const SECTION_TAB = \{(.*?)\};", JS, re.S).group(1)))


# ---------------------------------------------------------------- the page, as written
def test_every_tab_has_a_button_and_a_panel_in_the_order_the_script_lists_them():
    buttons = PAGE.find(role="tab")
    panels = [n for n in PAGE.nodes if n["attrs"].get("role") == "tabpanel"]
    assert len(TABS) == 7 and len(set(TABS)) == 7
    assert [b["attrs"]["data-tab"] for b in buttons] == TABS
    assert [b["attrs"]["id"] for b in buttons] == ["tabbtn-" + t for t in TABS]
    assert [p["attrs"]["id"] for p in panels] == ["tab-" + t for t in TABS]


def test_the_buttons_and_the_panels_name_each_other_for_screen_readers():
    for t in TABS:
        button, panel = PAGE.by_id("tabbtn-" + t), PAGE.by_id("tab-" + t)
        assert button["attrs"]["aria-controls"] == "tab-" + t and panel["attrs"]["aria-labelledby"] == "tabbtn-" + t
    assert PAGE.by_id("tabs")["attrs"]["role"] == "tablist"


def test_the_page_opens_on_the_first_tab_and_the_tab_row_waits_for_a_search():
    assert "hidden" in PAGE.by_id("tabs")["attrs"]
    states = [(PAGE.by_id("tabbtn-" + t)["attrs"]["aria-selected"], PAGE.by_id("tabbtn-" + t)["attrs"].get("tabindex"), "hidden" in PAGE.by_id("tab-" + t)["attrs"]) for t in TABS]
    assert states == [("true", None, False)] + [("false", "-1", True)] * (len(TABS) - 1)   # one tab stop on the row, one panel showing


def test_each_section_sits_in_the_tab_the_script_names_and_none_is_left_out():
    assert set(SECTION_TAB.values()) == set(TABS)                       # no tab is empty
    for section, tab in SECTION_TAB.items():
        node = PAGE.by_id(section)
        assert node["tag"] == "section" and inside(node, "tab-" + tab), section
    in_tabs = {n["attrs"]["id"] for n in PAGE.nodes if n["tag"] == "section" and any(p["attrs"].get("role") == "tabpanel" for p in n["parents"])}
    assert in_tabs == set(SECTION_TAB)                                  # and no section sits in a tab the script does not know about


def test_what_has_to_be_seen_whichever_tab_is_open_is_outside_the_tabs():
    for id_ in ("status", "mode", "warnings", "cachenote", "header", "recon", "tabs"):
        node = PAGE.by_id(id_)
        assert not any(p["attrs"].get("role") == "tabpanel" for p in node["parents"]), id_
    assert [n["attrs"]["id"] for n in PAGE.nodes if n["attrs"].get("id") in ("mode", "warnings", "cachenote", "header", "recon", "panels")] == \
        ["mode", "warnings", "cachenote", "header", "recon", "panels"]  # warnings sit with the other banners, above the panels


def test_the_data_sources_box_is_closed_until_asked_for_and_is_outside_the_tabs():
    box = PAGE.by_id("sources")["parents"][-1]
    assert box["tag"] == "details" and "open" not in box["attrs"] and "srcbox" in box["attrs"]["class"]
    assert not inside(PAGE.by_id("sources"), "panels") and "Data sources" in box["text"]


def test_no_id_is_used_twice():
    ids = [n["attrs"]["id"] for n in PAGE.nodes if "id" in n["attrs"]]
    assert len(ids) == len(set(ids)), sorted({i for i in ids if ids.count(i) > 1})


def test_every_section_the_script_clears_for_a_new_search_is_on_the_page():
    cleared = re.findall(r'"(\w+)"', re.search(r'\[("header",[^\]]*)\]\.forEach\(\(i\) => \(\$\(i\)\.innerHTML = ""\)\)', JS).group(1))
    assert "warnings" in cleared                                         # one search's warnings never linger into the next
    assert not [i for i in cleared if not PAGE.find(id=i)]
    assert not [s for s in SECTION_TAB if s not in cleared + ["chartsec"]]   # and every section is cleared (the chart section is hidden instead)


def test_the_page_still_has_the_chart_controls_inside_the_price_tab():
    for id_ in ("ranges", "avwapdays", "chart", "chartsig", "avwapinsight", "obvinsight"):
        assert inside(PAGE.by_id(id_), "tab-price"), id_


# ---------------------------------------------------------------- the tabs, as the real app.js runs them (under Node)
@pytest.fixture(scope="module")
def tabs():
    done = subprocess.run([NODE, str(ROOT / "tests" / "tabs.js"), str(ROOT / "web" / "app.js")], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@needs_node
def test_a_click_shows_that_tab_alone_and_leaves_the_others_out_of_the_tab_order(tabs):
    assert tabs["wired"] == {"click": 1, "keydown": 1}
    c = tabs["click"]
    assert c["active"] == "valuation" and c["selected"] == ["valuation"] and c["shown"] == ["valuation"]
    assert c["tabindex"] == [-1, -1, 0, -1, -1, -1, -1] and c["resizes"] == 1   # Plotly is told to measure again, once
    assert c["focused"] is None                                                 # a click does not move the focus


@needs_node
def test_a_tab_the_page_does_not_have_changes_nothing(tabs):
    assert tabs["unknown"] == tabs["click"]


@needs_node
def test_the_arrow_keys_move_along_the_tabs_and_wrap_and_home_and_end_jump(tabs):
    got = [(k.split("#")[0], v["active"], v["focused"], v["prevented"]) for k, v in tabs["keys"].items()]
    assert got == [("ArrowRight", "filter", "tabbtn-filter", True), ("End", "news", "tabbtn-news", True), ("ArrowRight", "overview", "tabbtn-overview", True),
                   ("ArrowLeft", "news", "tabbtn-news", True), ("Home", "overview", "tabbtn-overview", True),
                   ("a", "overview", "tabbtn-overview", False)]         # any other key is left alone
    last = list(tabs["keys"].values())[-1]
    assert last["resizes"] == list(tabs["keys"].values())[-2]["resizes"]   # and does not redraw anything


@needs_node
def test_picking_a_tab_scrolls_to_its_top_only_when_the_page_is_further_down(tabs):
    s = tabs["scroll"]
    assert s["deep"] == [[0, s["top"]]]       # the top of the panels, under the sticky header
    assert s["shallow"] == [] and s["bycode"] == []


@needs_node
def test_a_missing_section_is_made_in_its_tabs_panel_or_at_the_end_of_the_page(tabs):
    assert tabs["missingSection"] == {"made": [["section", "valuation"]], "inPanel": ["valuation"], "inMain": []}
    assert tabs["missingPanel"] == {"inMain": ["valuation"]}
    assert tabs["missingUntabbed"] == {"inMain": ["header", "sources"], "inPanels": []}      # the header and the sources are in no tab


@needs_node
def test_an_older_page_without_the_tab_row_does_not_fail(tabs):
    assert tabs["stale"]["threw"] is None and tabs["stale"]["active"] == "filter"


# ---------------------------------------------------------------- what each section holds, as the real app.js draws it (under Node)
@pytest.fixture(scope="module")
def analysis():
    return service.analyze(Data(), "AAPL.US")


@pytest.fixture(scope="module")
def flows():
    return service.get_flows(Data(), "AAPL.US")


@pytest.fixture
def halted(monkeypatch):
    d = Data()
    real = d.p.statements

    def broken(sym):
        s = real(sym)
        s["annual"][-1]["current_assets"] *= 3
        return s
    monkeypatch.setattr(d.p, "statements", broken)
    return service.analyze(d, "AAPL.US")


def panels(tmp_path, analysis, flows=None):
    """{sections: {id: the section's HTML}, plots: [the charts Plotly was asked for]} after the page draws the response."""
    (tmp_path / "scenario.json").write_text(json.dumps({"analysis": analysis, **({"flows": flows} if flows else {})}), encoding="utf-8")
    done = subprocess.run([NODE, str(ROOT / "tests" / "panels.js"), str(ROOT / "web" / "app.js"), str(tmp_path / "scenario.json")],
                          capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@needs_node
def test_the_overview_gives_the_verdict_first_then_the_evidence_and_keeps_the_price_levels_out(tmp_path, analysis):
    t = panels(tmp_path, analysis)["sections"]["thesis"]
    assert t.index("Verdict:") < t.index("Bull case") < t.index("Bear case") < t.index("Not measured by this app")
    assert analysis["verdict"]["summary"] in t and "Reference price levels" not in t


@needs_node
def test_the_valuation_tab_holds_the_fair_value_then_the_price_levels_then_the_morningstar_box(tmp_path, analysis):
    v = panels(tmp_path, analysis)["sections"]["valuation"]
    assert v.index("Fair value estimate") < v.index("Reference price levels") < v.index("Morningstar fair value estimate")
    assert analysis["levels"] and all(level["label"] in v for level in analysis["levels"])
    assert v.count("Reference price levels") == 1


@needs_node
def test_the_price_levels_show_even_when_no_dcf_can_be_built(tmp_path, analysis):
    v = panels(tmp_path, {**analysis, "valuation": {"applicable": False, "note": "No cash flow to discount."}})["sections"]["valuation"]
    assert "No cash flow to discount." in v and v.index("No cash flow to discount.") < v.index("Reference price levels") < v.index("Morningstar")


@needs_node
def test_the_buffett_and_graham_breakdowns_are_folded_under_the_scorecard(tmp_path, analysis):
    q = panels(tmp_path, analysis)["sections"]["quality"]
    assert q.index("<table>") < q.index('<details class="more"')
    folded = q[q.index('<details class="more"'):]
    assert "Buffett five dimensions" in folded and "Graham cigar-butt analysis" in folded and folded.rstrip().endswith("</details>")
    assert "Piotroski" in q[:q.index('<details class="more"')] and q.count("<details") == q.count("</details>") == 1


@needs_node
def test_data_warnings_go_above_the_tabs_and_the_sources_box_keeps_only_the_tables(tmp_path, analysis):
    s = panels(tmp_path, {**analysis, "warnings": ["shares: statements had no share count"]})["sections"]
    assert "Some data could not be fetched: shares: statements had no share count" in s["warnings"]
    assert "could not be fetched" not in s["sources"] and "<h2" not in s["sources"]      # the box is labelled by its own summary line
    assert "<table>" in s["sources"] and "Reconciliation:" in s["sources"]
    assert panels(tmp_path, {**analysis, "warnings": []})["sections"]["warnings"] == ""


@needs_node
def test_the_flows_panel_no_longer_repeats_the_obv_line_the_chart_already_draws(tmp_path, analysis, flows):
    out = panels(tmp_path, analysis, flows)
    f = out["sections"]["flows"]
    assert "obvchart" not in f and out["plots"] == []
    assert "own pane of the price chart" in f and "Reported activity" in f and "Flow indicators" in f


@needs_node
def test_a_company_that_fails_reconciliation_leaves_no_tab_blank(tmp_path, halted):
    assert halted["halted"]
    s = panels(tmp_path, halted)["sections"]
    for tab in ("thesis", "quality", "valuation", "filter"):
        assert "Not shown: this company's statements failed the reconciliation checks" in s[tab], tab
    assert "Reconciliation failed: no scores shown" in s["recon"] and halted["reconciliation"]["summary"] in s["sources"]
    assert not s["metrics"]                                                              # nothing is scored, so there are no cards
