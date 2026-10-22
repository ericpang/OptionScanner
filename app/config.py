"""Settings and the bull/bear thresholds (defaults from the README; change them here)."""
import os
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


_load_env()

DATA_MODE = os.environ.get("DATA_MODE", "cli").lower()  # cli | demo
# In-memory cache lifetimes (app/data.py). Freshness is set by DISK_CACHE_MINUTES; memory never outlives the disk cache.
MEMORY_TTL = 900   # synthetic demo data, which has no disk cache
DEDUPE_TTL = 5     # live data with the disk cache off: just long enough for panels loading together to share one fetch
FAIL_TTL = 45  # a failed call is remembered this long, so a rate-limited source is not hit again by every panel
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
# Per-ticker cache files in DATA_DIR (app/snapshot.py): a search within this many minutes of the last fetch reuses it.
DISK_CACHE_SECONDS = int(float(os.environ.get("DISK_CACHE_MINUTES", "30")) * 60)   # 0 turns the disk cache off
# The SEC refuses requests that do not say who is asking: your name and email, e.g. "Jane Doe jane@example.com".
SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "").strip()
GEMINI_API_KEY =os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")  # any model your key can use
CLI_TIMEOUT = 60

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
