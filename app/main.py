"""FastAPI app: JSON endpoints under /api, static single-page UI at /."""
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config, service, twostage
from .data import DataError, InputError, get_data

app = FastAPI(title="Stock Deep-Dive: Bull & Bear Case")
WEB = Path(__file__).resolve().parent.parent / "web"


@app.exception_handler(DataError)
async def data_error(_, exc: DataError):
    code = 400 if isinstance(exc, InputError) else 502
    return JSONResponse(status_code=code, content={"error": str(exc)})


@app.exception_handler(Exception)
async def unexpected_error(_, exc: Exception):
    return JSONResponse(status_code=500, content={"error": f"Internal error ({type(exc).__name__}: {exc}). "
                                                           "This is a bug; the server log has the traceback."})


def _run(fn, symbol, *args):
    sym = service.check_symbol(symbol)
    d = get_data().session(sym)   # reads from and records into this ticker's cache file (when there is a disk cache)
    try:
        out = fn(d, sym, *args)
    finally:
        d.save_snapshot()         # keep what was fetched even if a later step failed
    info = d.cache_info()
    if info is not None and isinstance(out, dict):
        out["cache"] = info       # the page says when cached data was fetched
    return out


@app.get("/api/health")
def health():
    return {"ok": True, "mode": get_data().mode}


@app.post("/api/refresh")
def refresh(symbol: str):
    """Drop the ticker's cached data (memory and disk); the page then searches again and fetches live."""
    sym = service.check_symbol(symbol)
    get_data().refresh(sym)
    return {"ok": True, "symbol": sym}


@app.get("/api/analyze")
def analyze(symbol: str):
    return _run(service.analyze, symbol)


@app.get("/api/filings")
def filings(symbol: str):
    return _run(service.get_filing_text, symbol)


@app.get("/api/peers")
def peers(symbol: str, refresh: bool = False):
    """The stock against its industry peers. The first search of an industry takes about 40 seconds."""
    return _run(service.get_peers, symbol, refresh)


@app.get("/api/filter")
def two_stage_filter(symbol: str, avg_fwd_pe: Optional[float] = Query(None, gt=0, le=1000), avg_fwd_pe_src: str = Query("", max_length=200),
                     eps_cagr: Optional[float] = Query(None, ge=-100, le=500), eps_cagr_src: str = Query("", max_length=200),
                     category: str = Query("auto", pattern="^(auto|software|hardware)$"), stage2: bool = False, q4: bool = False,
                     q5: Optional[int] = Query(None, ge=1, le=3), q6: Optional[int] = Query(None, ge=1, le=3),
                     q7: Optional[int] = Query(None, ge=1, le=3), q8: Optional[int] = Query(None, ge=1, le=3)):
    """The Two-Stage Stock Filter. The query holds what only the user can supply: the stock's five-year average forward P/E, a three-to-five-year
    expected EPS growth (a percentage number, 12 for 12%), a software or hardware choice, whether to run stage two anyway, whether question 4's
    thesis is confirmed, and ratings of 1 to 3 for the judgment questions 5 to 8."""
    inputs = twostage.clean_inputs(avg_fwd_pe, avg_fwd_pe_src, eps_cagr, eps_cagr_src, category, stage2, q4, q5, q6, q7, q8)
    return _run(service.get_filter, symbol, inputs)


@app.get("/api/chart")
def chart(symbol: str, range: str = "1y", bins: int = Query(config.PROFILE_BINS, ge=10, le=120),
          days: int = Query(config.AVWAP_DAYS, ge=config.AVWAP_MIN_DAYS, le=config.AVWAP_MAX_DAYS)):
    return _run(service.get_chart, symbol, range, bins, days)


@app.get("/api/flows")
def flows(symbol: str):
    return _run(service.get_flows, symbol)


@app.get("/api/news")
def news(symbol: str, limit: int = Query(config.NEWS_DEFAULT, ge=1, le=config.NEWS_MAX)):
    return _run(service.get_news, symbol, limit)


@app.get("/api/options")
def options(symbol: str, view: str = Query("auto", pattern="^(auto|bullish|bearish|neutral)$"),
            dte_min: int = Query(config.OPT_DTE_MIN, ge=config.OPT_MIN_DTE_ANY, le=400),
            dte_max: int = Query(config.OPT_DTE_MAX, ge=config.OPT_MIN_DTE_ANY, le=400),
            defined_risk: bool = True, shares: int = Query(0, ge=0, le=1_000_000), include_credit: bool = True,
            min_credit: float = Query(config.OPT_MIN_CREDIT_PCT_WIDTH, ge=0.0, le=0.6)):
    return _run(service.get_options, symbol, view, dte_min, dte_max, defined_risk, shares, include_credit, min_credit)


@app.get("/api/options/payoff")
def options_payoff(symbol: str, occ: list[str] = Query(...), side: list[str] = Query(...),
                   qty: int = Query(1, ge=1, le=1000), spot_range: float = Query(0.3, gt=0.05, le=0.9)):
    return _run(service.get_payoff, symbol, occ, side, qty, spot_range)


def _build_id() -> str:
    """Changes whenever any UI file changes. Used to bust the browser cache and shown in the page footer."""
    return str(int(max(f.stat().st_mtime for f in WEB.iterdir() if f.is_file())))


@app.get("/")
def index():
    """The page, with versioned asset URLs. Static files have no Cache-Control header of their own, so Chrome
    caches them heuristically and can pair an old app.js with a new page (or the reverse), which makes whole
    panels silently disappear. The version in the URL changes with the files, and the page is never cached."""
    v = _build_id()
    html = (WEB / "index.html").read_text(encoding="utf-8")
    html = html.replace("/static/app.js", f"/static/app.js?v={v}").replace("/static/styles.css", f"/static/styles.css?v={v}")
    html = html.replace("__BUILD__", v)
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


class _Revalidate(StaticFiles):
    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-cache"   # always revalidate (a 304 when nothing changed)
        return resp


app.mount("/static", _Revalidate(directory=WEB), name="static")
