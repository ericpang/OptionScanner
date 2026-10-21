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
