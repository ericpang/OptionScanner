"""Settings and the bull/bear thresholds (defaults from the README; change them here)."""
import ast
import math
import operator
import os
import sys
from pathlib import Path


def _load_env() -> None:
    f = Path(__file__).resolve().parent.parent / ".env"
    if not f.exists():
        return
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def _clamp_int(raw, default: int, lo: int, hi: int) -> int:
    """An integer setting from the environment: held within [lo, hi], or the default when it is not a number."""
    try:
        return max(lo, min(hi, int(float(raw))))
    except (TypeError, ValueError, OverflowError):
        return default


_ARITH = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}


def _arith(node) -> float:
    """The value of a parsed number, or of numbers combined with + - * / and brackets. Nothing else is evaluated."""
    if isinstance(node, ast.Expression):
        return _arith(node.body)
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        return _arith(node.operand) * (-1 if isinstance(node.op, ast.USub) else 1)
    if isinstance(node, ast.BinOp) and type(node.op) in _ARITH:
        return _ARITH[type(node.op)](_arith(node.left), _arith(node.right))
    raise ValueError("not arithmetic")


def _minutes(name: str, default: float) -> float:
    """A lifetime in minutes from the environment: a number, or numbers multiplied or added, such as 60*24*7 for a week (the way a
    long one is easiest to write). Negative counts as 0. Anything else is reported and the default is used, so that a typo cannot
    keep the server from starting."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = _arith(ast.parse(raw.strip(), mode="eval"))
        if not math.isfinite(value):
            raise ValueError("not finite")
    except (SyntaxError, ValueError, ZeroDivisionError, OverflowError, RecursionError):
        print(f"config: {name}={raw!r} is not a number of minutes; using {default:g}", file=sys.stderr)
        return default
    return max(0.0, value)


_load_env()

DATA_MODE = os.environ.get("DATA_MODE", "cli").lower()  # cli | demo
# In-memory cache lifetimes (app/data.py). Freshness is set by DISK_CACHE_MINUTES and LIVE_CACHE_MINUTES; memory never outlives either.
MEMORY_TTL = 900   # synthetic demo data, which has no disk cache
DEDUPE_TTL = 5     # live data that is not kept on disk: just long enough for panels loading together to share one fetch
FAIL_TTL = 45  # a failed call is remembered this long, so a rate-limited source is not hit again by every panel
RATING_TTL = 60   # institution-rating is read by the quote (industry name) and by the analyst call; one search shares a fetch, no more
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
# Per-ticker cache files in DATA_DIR (app/snapshot.py). A file, and the fundamentals in it (STATIC_CALLS below), is reused for this many
# minutes after it was made: a week (10080) is reasonable, since statements, ratings and holders change a quarter at a time.
DISK_CACHE_SECONDS = int(_minutes("DISK_CACHE_MINUTES", 30) * 60)   # 0 turns the disk cache off
# The market data in that file (LIVE_CALLS below) is fetched again once it is this old, however long the file lives. Never longer than
# DISK_CACHE_MINUTES; 0 fetches it on every search.
LIVE_CACHE_SECONDS = int(_minutes("LIVE_CACHE_MINUTES", 60) * 60)
# The SEC refuses requests that do not say who is asking: your name and email, e.g. "Jane Doe jane@example.com".
SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "").strip()
GEMINI_API_KEY =os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")  # any model your key can use
CLI_TIMEOUT = 60

# ---- which data calls change by the hour (snapshot.py) ----------------------------------------------
# The data providers' calls, by how fast their answers change. The ticker's cache file holds each call in LIVE_CALLS for LIVE_CACHE_MINUTES
# and each in STATIC_CALLS for as long as the file lives. A call belongs in exactly one set (a test holds the providers to that); one that is
# in neither is treated as market data, so a new data call is never frozen for a week by accident.
LIVE_CALLS = frozenset({
    "quote",                # price, market cap, P/E, P/B, 52-week range, the extended-hours print
    "kline", "kline_raw",   # daily bars: the newest is still forming during a session, and a dividend moves the adjusted older ones
    "industry_valuation",   # the industry's median P/E, P/B, P/S and the stock's rank move with prices; benchmarks.py drops the rank when
                            # this figure and the metric card's own drift apart, so it has to be as fresh as the quote
    "splits",               # a split rescales every bar, and kline_raw is adjusted with this list (pricehist.split_adjust): as fresh as the bars
    "capital_flow",         # the latest session's money flow by order size
    "news", "filings", "topics", "market_temp",   # headlines, the SEC filing list, community posts, the market's temperature
    "option_expiries", "option_chain", "option_quotes", "option_volume_daily",   # option prices, IV and volume
    "earnings_date",        # the next date from today on: the answer depends on the day it was asked, so it cannot wait a week
})
STATIC_CALLS = frozenset({
    "statements", "report_history",         # change when the company reports, a quarter at a time
    "analyst",                              # ratings, target price and consensus estimates, revised now and then
    "dividends", "insider", "ownership",    # dividend history, Form 4 filings, 13F holders
    "short_interest", "short_interest_detail",   # published twice a month
    "sec_disclosures",                      # the latest 10-K and 10-Q text
    "industry_members",                     # the companies of the industry, which only picks the peers by size
    "adj_close",                            # a past session's adjusted close, asked for by date
    "news_detail",                          # one article's text
    "peers",                                # nothing to fetch (a stub)
})


def is_live_call(name: str) -> bool:
    """Whether the data call changes by the hour (and so is fetched again after LIVE_CACHE_MINUTES). Unknown calls count as live."""
    return name not in STATIC_CALLS


NEWS_DEFAULT = 10
NEWS_MAX = 25
NEWS_STALE_DAYS = 30
PROFILE_BINS = 50
VALUE_AREA = 0.70
EMA_PERIODS = (20, 50, 200)
EMA_WARMUP = 250

# ---- anchored VWAP and its volume matrix (avwap.py) ---------------------------------------------------
AVWAP_MIN_DAYS = 5
AVWAP_MAX_DAYS = 500
# The AVWAP is anchored this many trading days before the latest closed session. This is only the default: the chart
# has a box to change it, and /api/chart takes `days`.
AVWAP_DAYS = _clamp_int(os.environ.get("AVWAP_DAYS"), 20, AVWAP_MIN_DAYS, AVWAP_MAX_DAYS)
AVWAP_FLAT_VOLUME = 0.20    # volume within this share of its average is "flat" (the chop-zone rule)
AVWAP_CHOP_BARS = 5         # "consolidating at the AVWAP": the latest closes span no more than AVWAP_CHOP_ATR average
AVWAP_CHOP_ATR = 1.0        # true ranges, and the latest is within that of the line (about 6% of sessions at 20 days)
AVWAP_ATR_DAYS = 14

# ---- On-Balance Volume insights (obv.py) ---------------------------------------------------------------
OBV_DAYS = 20               # the price / volume / OBV read covers the last N closed sessions (volume is set against the N before)
OBV_FLAT_ATR = 1.0          # price is "flat" when it moved no more than this many ATRs (AVWAP_ATR_DAYS) over those sessions
OBV_FLAT_SHARE = 0.10       # OBV is "flat" when its net change is within this share of the volume traded over them
OBV_FLAT_VOLUME = 0.10      # volume is "flat" when its average is within this share of the earlier period's
OBV_MA_DAYS = 20            # the moving average drawn over the OBV line on the chart
OBV_DIVERGENCE_DAYS = 60    # divergence looks at this many sessions; the last OBV_DIVERGENCE_RECENT are "recent" and the
OBV_DIVERGENCE_RECENT = 10  # sessions before them are what a new high or low is judged against
OBV_DIVERGENCE_GAP = 0.05   # OBV must fall short of its earlier extreme by this share of the window's volume to count

# ---- the Two-Stage Stock Filter (twostage.py) ---------------------------------------------------------
# The checklist's own cut-offs (Reset & Invest, v2.1), not this app's. Whether a figure equal to its cut-off passes is
# decided in twostage.py, which words each comparison the way the checklist does ("above" and "under" are strict).
TS_NET_DEBT_EBITDA_MAX = 1.5   # check 1: net debt / positive TTM EBITDA strictly below this (or cash pool above debt)
TS_FCF_MARGIN_MIN = 0.15       # check 2: TTM free cash flow / revenue at least this
TS_ROIC_MIN = 0.15             # check 2: TTM ROIC strictly above this
TS_SBC_MAX = 0.10              # check 2: TTM stock-based compensation / revenue strictly below this
TS_PEG_MAX = 1.5               # check 3: PEG at or below this passes ...
TS_PEG_REVIEW_MAX = 2.0        # ... above it and at or below this is a review, not a fail; above this fails
TS_PRICE_RISE_MAX = 0.50       # check 3: price rise over three calendar months strictly below this
TS_GROWTH_MIN = 0.10           # question 1: TTM revenue growth strictly above this
TS_GM_SOFTWARE = 0.50          # question 1: TTM gross margin strictly above this for software ...
TS_GM_HARDWARE = 0.35          # ... and this for hardware
TS_OPM_MIN = 0.20              # question 1: TTM operating margin strictly above this
TS_SHORT_MAX = 0.05            # question 2: short interest / float strictly below this scores the point
TS_SHORT_MAX_AGE_DAYS = 60     # ... but only for a report this recent: FINRA publishes twice a month, and Longbridge's history for some
                               # stocks (JPM, KO) stops in June 2022, which is not "the latest reported" short interest
TS_LIST_AT = 7                 # of 8: this many or more makes the list ...
TS_CLOSE_CALL = 6.5            # ... and this many is a close call
TS_TSR_YEARS = 5               # question 3: total return and share count are compared over this many years
# What the data can and cannot settle, so a result inside the data's own error is called UNKNOWN, not a pass or a fail:
TS_TSR_MARGIN = 0.01           # a 5-year return within this (cumulative, 1 point) of the S&P 500 proxy is too close to call
TS_TAX_LOW, TS_TAX_HIGH = 0.10, 0.35   # an effective tax rate outside this range is flagged as distorting NOPAT
TS_BARS = 600                  # unadjusted daily bars fetched: two years of closes for the 200-day EMA, with room to spare
TS_PIVOT_SIDE = 2              # a pivot is strictly above (below) the two highs (lows) on either side, so two sessions confirm it

# Assumptions used for the ROIC vs. WACC test (stated in the UI)
RISK_FREE = 0.043
EQUITY_PREMIUM = 0.05
COST_OF_DEBT = 0.05
DEFAULT_TAX = 0.21

# bull / bear thresholds: (bull if ..., bear if ...)
T = {
    "peg": (1.0, 2.0),
    "debt_ebitda": (2.0, 4.0),
    "roic": (0.12, 0.08),
    "roe": (0.15, 0.10),
    "ni_growth": (0.10, 0.0),
    "gross_margin": (0.30, 0.20),
    "cash_conversion": (0.8, 0.8),
    "interest_cov": (5.0, 3.0),
    "current_ratio": (1.5, 1.0),
    "altman": (2.99, 1.81),
    "piotroski": (7, 3),
}

# ---- industry comparison (benchmarks.py) ------------------------------------------------------------
BENCH_MIN_COMPANIES = 5      # an industry median over fewer companies than this is flagged as a thin sample
BENCH_VALUE_TOLERANCE = 0.10  # the percentile ranks Longbridge's own figure for the company, so it is shown only when
                              # that figure is within this share of the one on the metric card

# ---- peer comparison (peers.py, peerstore.py) -------------------------------------------------------
BENCH_PEERS = 10             # the peer group: the companies in the stock's industry closest to it in market cap
BENCH_SPARES = 6             # more candidates, tried in turn when a peer's data cannot be used
BENCH_MIN_PEERS = 3          # a peer median needs at least this many peers with a figure for that metric
BENCH_WORKERS = 2            # peers fetched at once; the statements endpoint allows about 2 calls a second in all
# A peer's computed figures are reused this long. A company's fundamentals change a quarter at a time and a peer costs
# about a dozen CLI calls to fetch. 0 means they are never reused.
PEER_CACHE_SECONDS = _clamp_int(os.environ.get("PEER_CACHE_DAYS"), 7, 0, 365) * 86400
# A peer that could not be fetched (no statements for it, a timeout) is not tried again for this long, so a repeat search
# is not slowed by it. Only remembered when enough other peers worked: a down service must not be remembered.
PEER_RETRY_SECONDS = 3600
# The currency a market's shares trade in, to spot statements reported in another one (an ADR, say).
MARKET_CURRENCY = {"US": "USD", "HK": "HKD", "SH": "CNY", "SZ": "CNY", "SG": "SGD"}

# ---- fair value (valuation.py) ----------------------------------------------------------------------
TERMINAL_GROWTH = 0.03      # long-run growth after the fade; never above the risk-free rate
DCF_MAX_GROWTH = 0.20       # cap on stage-1 growth: no company is assumed to compound faster than this for 5 years

# ---- option scanner ---------------------------------------------------------------------------------
OPT_DTE_MIN = 21
OPT_DTE_MAX = 60
OPT_MIN_DTE_ANY = 7          # never use expiries closer than this
OPT_MIN_PRICE = 0.05         # drop legs whose last price is below this
OPT_IV_RANGE = (0.05, 1.5)   # drop chain rows with IV outside this (deep ITM rows carry nonsense IV)
OPT_MIN_VOLUME = 1           # chain volume needed for a leg (the CLI gives no bid/ask or reliable open interest)
OPT_THIN_VOLUME = 10         # below this a leg is flagged as thin
OPT_MAX_EXPIRIES = 5         # chain calls per scan (keeps within the CLI rate limit)
OPT_SHORT_DELTA = (0.20, 0.30)
OPT_MAX_SHORT_DELTA = 0.35
OPT_MIN_CREDIT_PCT_WIDTH = 0.25   # default minimum credit as a share of the wing width (adjustable in the panel)
OPT_WING_PCTS = (0.03, 0.06)      # wing widths tried for credit structures, as a share of the price
IV_RICH = 1.3                # IV / HV above this is rich, below IV_CHEAP is cheap (options-advanced.md)
IV_CHEAP = 0.8
CALENDAR_RATIO = 1.1         # near / far ATM IV above this adds calendar spreads
IV_HISTORY_MIN_OBS = 60
VIEW_THRESHOLD = 3           # automatic view: score >= +3 bullish, <= -3 bearish
