"""Option structures built from live chain rows: payoff, breakevens, max profit/loss, net Greeks, model POP.

Strategy choice follows the strategy matrix in longbridge-derivatives/references/options-strategy.md
(market view x IV level). Payoff formulas follow options-pnl.md. Nothing here is a recommendation: each
candidate is a structure with its numbers and its risks.
"""
import math
from datetime import date
from typing import Optional

from . import config
from .options import (bs_price, dte, greeks, lognormal_cdf, occ_symbol, row_problem, usable, years)
from .util import num

MULT = 100


# --------------------------------------------------------------------------------------------- legs
def make_leg(side: str, kind: str, row: dict, expiry: str, underlying: str, qty: int = 1) -> dict:
    s = "call" if kind == "C" else "put"
    return {"side": side, "kind": kind, "strike": row["strike"], "expiry": expiry, "qty": qty,
            "last": row[f"{s}_last"], "iv": row[f"{s}_iv"], "vol": row[f"{s}_vol"] or 0,
            "occ": occ_symbol(underlying, expiry, kind, row["strike"])}


def stock_leg(spot: float, shares: int) -> dict:
    return {"side": "buy", "kind": "S", "strike": None, "expiry": None, "qty": shares // MULT, "last": spot,
            "iv": None, "vol": None, "occ": None}


def _sign(leg: dict) -> int:
    return 1 if leg["side"] == "buy" else -1


def net_per_share(legs: list[dict]) -> float:
    """Debit is positive, credit is negative; per share of one contract."""
    return sum(_sign(l) * l["qty"] * l["last"] for l in legs if l["kind"] != "S")


def _intrinsic(leg: dict, S: float) -> float:
    if leg["kind"] == "S":
        return S
    return max(S - leg["strike"], 0.0) if leg["kind"] == "C" else max(leg["strike"] - S, 0.0)


def pnl_at(legs: list[dict], S: float, spot: float, r: float, q: float, eval_expiry: Optional[str] = None,
           today: Optional[date] = None) -> float:
    """Profit per share of one contract at underlying price S, at the nearest expiry (or `eval_expiry`).
    Legs that expire later are valued with Black-Scholes at their own IV (calendars and diagonals)."""
    expiries = [l["expiry"] for l in legs if l["expiry"]]
    ev = eval_expiry or (min(expiries) if expiries else None)
    total = 0.0
    for l in legs:
        if l["kind"] == "S":
            total += l["qty"] * (S - spot)
            continue
        if l["expiry"] == ev or ev is None:
            value = _intrinsic(l, S)
        else:
            rem = dte(l["expiry"], today) - dte(ev, today)
            value = bs_price(S, l["strike"], years(rem), r, q, l["iv"], l["kind"])
        total += _sign(l) * l["qty"] * (value - l["last"])
    return total


def analyze_payoff(legs: list[dict], spot: float, r: float, q: float, today: Optional[date] = None) -> dict:
    """Max profit/loss, breakevens (dollars per contract set). Exact for single-expiry structures (the payoff
    is piecewise linear between strikes); a +/-50% price grid for calendars and diagonals."""
    strikes = sorted({l["strike"] for l in legs if l["strike"] is not None})
    multi_expiry = len({l["expiry"] for l in legs if l["expiry"]}) > 1
    s_far = max(strikes + [spot]) * 3
    if multi_expiry:
        xs = sorted(set([spot * (0.5 + i / 100) for i in range(101)] + strikes))
    else:
        xs = sorted(set([0.0] + strikes + [s_far]))
    vs = [pnl_at(legs, x, spot, r, q, today=today) for x in xs]
    unlimited_profit = unlimited_loss = False
    if not multi_expiry and len(xs) >= 2:
        slope = (vs[-1] - vs[-2]) / (xs[-1] - xs[-2]) if xs[-1] != xs[-2] else 0.0
        unlimited_profit, unlimited_loss = slope > 1e-9, slope < -1e-9
    bes = []
    for i in range(1, len(xs)):
        a, b = vs[i - 1], vs[i]
        if (a < 0 < b) or (a > 0 > b):
            bes.append(xs[i - 1] + (xs[i] - xs[i - 1]) * (0 - a) / (b - a))
        elif a == 0 and i - 1 > 0 and not multi_expiry:
            bes.append(xs[i - 1])
    bes = sorted(set(round(b, 2) for b in bes))
    return {"max_profit": None if unlimited_profit else max(vs) * MULT,
            "max_loss": None if unlimited_loss else max(-min(vs), 0.0) * MULT,
            "unlimited_profit": unlimited_profit, "unlimited_loss": unlimited_loss, "breakevens": bes,
            "range_limited": multi_expiry}


def model_pop(legs: list[dict], spot: float, r: float, q: float, today: Optional[date] = None) -> Optional[float]:
    """P(profit at the nearest expiry) under a risk-neutral lognormal using the legs' IV. A model estimate."""
    opts = [l for l in legs if l["kind"] != "S"]
    if not opts:
        return None
    ev = min(l["expiry"] for l in opts)
    near = [l for l in opts if l["expiry"] == ev]
    sigma = sum(l["iv"] for l in near) / len(near)
    T = years(dte(ev, today))
    strikes = sorted({l["strike"] for l in legs if l["strike"] is not None})
    if len({l["expiry"] for l in opts}) > 1:  # calendars: sample the density
        n, p = 4000, 0.0
        lo, hi = spot * 0.2, spot * 3.0
        prev = lognormal_cdf(lo, spot, T, r, q, sigma)
        p += 0.0
        for i in range(1, n + 1):
            x = lo + (hi - lo) * i / n
            c = lognormal_cdf(x, spot, T, r, q, sigma)
            mid = x - (hi - lo) / (2 * n)
            if pnl_at(legs, mid, spot, r, q, today=today) > 0:
                p += c - prev
            prev = c
        return round(p, 3)
    bes = analyze_payoff(legs, spot, r, q, today)["breakevens"]
    pts = [0.0] + sorted(set(bes)) + [math.inf]
    p = 0.0
    for a, b in zip(pts, pts[1:]):
        probe = (a + b) / 2 if b != math.inf else max(strikes + [spot]) * 2
        if a == 0 and b == math.inf:
            probe = spot
        if pnl_at(legs, probe, spot, r, q, today=today) > 0:
            p += lognormal_cdf(b, spot, T, r, q, sigma) - lognormal_cdf(a, spot, T, r, q, sigma)
    return round(p, 3)


def net_greeks(legs: list[dict], spot: float, r: float, q: float, today: Optional[date] = None) -> dict:
    """Position Greeks: delta in shares, gamma in shares per $1, theta in $ per day, vega in $ per IV point."""
    d = g = th = v = 0.0
    for l in legs:
        if l["kind"] == "S":
            d += l["qty"] * MULT
            continue
        gr = greeks(spot, l["strike"], years(dte(l["expiry"], today)), r, q, l["iv"], l["kind"])
        k = _sign(l) * l["qty"] * MULT
        d += k * gr["delta"]
        g += k * gr["gamma"]
        th += k * gr["theta"]
        v += k * gr["vega"]
    return {"delta": round(d, 1), "gamma": round(g, 3), "theta": round(th, 2), "vega": round(v, 2)}


# --------------------------------------------------------------------------------------------- picking strikes
def _delta(row: dict, kind: str, spot: float, T: float, r: float, q: float) -> float:
    iv = row["call_iv"] if kind == "C" else row["put_iv"]
    return abs(greeks(spot, row["strike"], T, r, q, iv, kind)["delta"])


def pick_delta(rows: list[dict], kind: str, target: float, spot: float, T: float, r: float, q: float,
               side: Optional[str] = None) -> Optional[dict]:
    """Usable row whose |delta| is closest to target. side='otm' restricts to out-of-the-money strikes."""
    cand = [x for x in rows if row_problem(x, kind) is None]
    if side == "otm":
        cand = [x for x in cand if (x["strike"] > spot if kind == "C" else x["strike"] < spot)]
    return min(cand, key=lambda x: abs(_delta(x, kind, spot, T, r, q) - target)) if cand else None


def strike_step(rows: list[dict]) -> float:
    ks = [x["strike"] for x in rows]
    diffs = sorted(b - a for a, b in zip(ks, ks[1:]) if b > a)
    return diffs[len(diffs) // 2] if diffs else 1.0


def pick_wing(rows: list[dict], kind: str, base_strike: float, direction: int, width: float) -> Optional[dict]:
    """Usable row about `width` away from base_strike (direction +1 higher, -1 lower)."""
    target = base_strike + direction * width
    cand = [x for x in rows if row_problem(x, kind) is None
            and (x["strike"] > base_strike if direction > 0 else x["strike"] < base_strike)]
    return min(cand, key=lambda x: abs(x["strike"] - target)) if cand else None


# --------------------------------------------------------------------------------------------- builders
RATIONALE = {
    "long call": "Bullish view; buys upside with a defined loss equal to the premium.",
    "bull call spread": "Bullish view; caps the cost and the gain by selling a higher call.",
    "bull put spread": "Bullish view with rich IV; collects premium and keeps the risk defined.",
    "long put": "Bearish view; buys downside with a defined loss equal to the premium.",
    "bear put spread": "Bearish view; caps the cost and the gain by selling a lower put.",
    "bear call spread": "Bearish view with rich IV; collects premium and keeps the risk defined.",
    "iron condor": "Range-bound view with rich IV; collects premium from both sides with defined risk.",
    "long straddle": "Expects a large move with cheap IV; pays premium for both directions.",
    "long strangle": "Expects a large move with cheap IV; cheaper than a straddle but needs a bigger move.",
    "short strangle": "Range-bound view with rich IV; collects premium with UNDEFINED risk on both sides.",
    "calendar spread": "Near-term IV is richer than far-term IV; sells the near expiry and buys the far expiry.",
    "covered call": "Holds shares; sells an out-of-the-money call for income and gives up upside above the strike.",
    "protective put": "Holds shares; buys a put as insurance against a fall.",
}


def build_for_expiry(view: str, reg: str, expiry: str, ed: dict, ctx: dict, allow_undefined: bool, shares: int,
                     dropped: dict, include_credit: bool = True) -> list[tuple[str, list[dict], dict]]:
    """Return (strategy name, legs, extra) for one expiry according to the strategy matrix."""
    rows, S, r, q = ed["rows"], ctx["spot"], ctx["r"], ctx["q"]
    T, u = ed["T"], ctx["symbol"]
    calls = usable(rows, "C", dropped)
    puts = usable(rows, "P", dropped)
    if not calls and not puts:
        return []
    step = strike_step(rows)

    def wing(pct):  # wing width as a share of the price, rounded to the strike grid
        return max(step, round(pct * S / step) * step)
    out: list[tuple[str, list[dict], dict]] = []
    lo_d, hi_d = config.OPT_SHORT_DELTA
    short_d = (lo_d + hi_d) / 2

    def leg(side, kind, row):
        return make_leg(side, kind, row, expiry, u)

    def add(name, legs, **extra):
        if all(legs):
            out.append((name, legs, extra))

    def credit_spread(kind, width):  # sell ~0.25 delta, buy the wing `width` further out of the money
        s_row = pick_delta(rows, kind, short_d, S, T, r, q, side="otm")
        if not s_row:
            return None
        w_row = pick_wing(rows, kind, s_row["strike"], -1 if kind == "P" else 1, width)
        return (s_row, w_row) if w_row else None

    widths = sorted({wing(p) for p in config.OPT_WING_PCTS})   # a narrow and a wide wing; wider ones usually pay more
                                                              # per dollar of width

    bullish, bearish, neutral = view == "bullish", view == "bearish", view == "neutral"
    if bullish:
        lc = pick_delta(rows, "C", 0.60, S, T, r, q)
        if lc:
            add("long call", [leg("buy", "C", lc)])
            sc = pick_delta(rows, "C", 0.30, S, T, r, q, side="otm")
            if sc and sc["strike"] > lc["strike"]:
                add("bull call spread", [leg("buy", "C", lc), leg("sell", "C", sc)])
        if reg == "rich" or include_credit:   # the matrix sells premium only when IV is rich; the toggle widens it
            for w in widths:
                sp = credit_spread("P", w)
                if sp:
                    add("bull put spread", [leg("sell", "P", sp[0]), leg("buy", "P", sp[1])], credit_spread=True,
                        marginal=(reg != "rich"), reg=reg)
    if bearish:
        lp = pick_delta(rows, "P", 0.60, S, T, r, q)
        if lp:
            add("long put", [leg("buy", "P", lp)])
            sp_ = pick_delta(rows, "P", 0.30, S, T, r, q, side="otm")
            if sp_ and sp_["strike"] < lp["strike"]:
                add("bear put spread", [leg("buy", "P", lp), leg("sell", "P", sp_)])
        if reg == "rich" or include_credit:
            for w in widths:
                sc = credit_spread("C", w)
                if sc:
                    add("bear call spread", [leg("sell", "C", sc[0]), leg("buy", "C", sc[1])], credit_spread=True,
                        marginal=(reg != "rich"), reg=reg)
    if neutral:
        if reg in ("rich", "fair") or include_credit:
            for w in widths:
                cs, ps = credit_spread("C", w), credit_spread("P", w)
                if cs and ps:
                    add("iron condor", [leg("sell", "P", ps[0]), leg("buy", "P", ps[1]),
                                        leg("sell", "C", cs[0]), leg("buy", "C", cs[1])], credit_spread=True,
                        marginal=(reg != "rich"), reg=reg)
            if allow_undefined and reg == "rich":
                sc2 = pick_delta(rows, "C", lo_d, S, T, r, q, side="otm")
                sp2 = pick_delta(rows, "P", lo_d, S, T, r, q, side="otm")
                if sc2 and sp2:
                    add("short strangle", [leg("sell", "P", sp2), leg("sell", "C", sc2)])
        if reg in ("cheap", "fair"):
            atm = min((x for x in rows if row_problem(x, "C") is None and row_problem(x, "P") is None),
                      key=lambda x: abs(x["strike"] - S), default=None)
            if atm:
                add("long straddle", [leg("buy", "C", atm), leg("buy", "P", atm)], marginal=(reg == "fair"), reg=reg)
            c3 = pick_delta(rows, "C", 0.30, S, T, r, q, side="otm")
            p3 = pick_delta(rows, "P", 0.30, S, T, r, q, side="otm")
            if c3 and p3:
                add("long strangle", [leg("buy", "P", p3), leg("buy", "C", c3)], marginal=(reg == "fair"), reg=reg)
    if shares >= MULT:
        qty = shares // MULT
        cc = pick_delta(rows, "C", 0.30, S, T, r, q, side="otm")
        if cc:
            lg = make_leg("sell", "C", cc, expiry, u, qty)
            add("covered call", [stock_leg(S, shares), lg])
        pp = pick_delta(rows, "P", 0.25, S, T, r, q, side="otm")
        if pp:
            add("protective put", [stock_leg(S, shares), make_leg("buy", "P", pp, expiry, u, qty)])
    return out


def build_calendars(ctx: dict, window: tuple[int, int], dropped: dict) -> list[tuple[str, list[dict], dict]]:
    """Sell the near ATM call and buy the same strike further out, when near ATM IV / far ATM IV is above 1.1."""
    eds = ctx["expiries"]
    S, u = ctx["spot"], ctx["symbol"]
    out = []
    near = [e for e, d in eds.items() if window[0] <= d["dte"] <= window[1] and d.get("atm_iv")]
    for ne in near:
        for fe, fd in eds.items():
            if fd["dte"] <= eds[ne]["dte"] + 14 or not fd.get("atm_iv"):
                continue
            if eds[ne]["atm_iv"] / fd["atm_iv"] < config.CALENDAR_RATIO:
                continue
            both = [x for x in eds[ne]["rows"]
                    if row_problem(x, "C") is None
                    and any(y["strike"] == x["strike"] and row_problem(y, "C") is None for y in fd["rows"])]
            if not both:
                continue
            k = min(both, key=lambda x: abs(x["strike"] - S))
            far_row = next(y for y in fd["rows"] if y["strike"] == k["strike"])
            out.append(("calendar spread", [make_leg("sell", "C", k, ne, u), make_leg("buy", "C", far_row, fe, u)],
                        {"ratio": round(eds[ne]["atm_iv"] / fd["atm_iv"], 2)}))
    return out


# --------------------------------------------------------------------------------------------- candidate
def _iv_note(extra: dict) -> str:
    reg = extra.get("reg", "fair")
    if extra.get("credit_spread"):
        return ("IV is cheap, so selling premium here is against the matrix and pays little for the risk."
                if reg == "cheap" else "IV is fair, so selling premium has no IV edge.")
    return "IV is fair, so the strategy matrix has no clear edge for this view."


def _rationale(name: str, extra: dict) -> str:
    text = RATIONALE.get(name, "")
    if extra.get("marginal"):  # the matrix would not pick this at the current IV level: do not claim an IV edge
        text = text.replace(" with rich IV", "").replace(" with cheap IV", "")
        text = text.rstrip(".") + f". {_iv_note(extra)} Shown for reference rather than as an IV-driven pick."
    return text


def describe(name: str, legs: list[dict], ctx: dict, extra: dict, today: Optional[date] = None) -> dict:
    S, r, q = ctx["spot"], ctx["r"], ctx["q"]
    pay = analyze_payoff(legs, S, r, q, today)
    net = net_per_share(legs)
    qty = max((l["qty"] for l in legs if l["kind"] != "S"), default=1)
    opts = [l for l in legs if l["kind"] != "S"]
    near_exp = min(l["expiry"] for l in opts)
    ed = ctx["expiries"][near_exp]
    max_loss, max_profit = pay["max_loss"], pay["max_profit"]
    width = None
    shorts = [l for l in opts if l["side"] == "sell"]
    longs = [l for l in opts if l["side"] == "buy"]
    if extra.get("credit_spread"):
        # widest wing among the spreads; one side of a condor can lose at most one wing's width
        by_kind = {}
        for l in opts:
            by_kind.setdefault(l["kind"], []).append(l["strike"])
        width = max(max(v) - min(v) for v in by_kind.values())
    flags = []
    if any(l["side"] == "sell" and l["kind"] != "S" for l in legs):
        flags.append("Short American-style leg: early assignment is possible, more likely when it is in the money "
                     "or near an ex-dividend date.")
    undefined = pay["unlimited_loss"] or (name == "short strangle")
    if undefined:
        flags.append("UNDEFINED RISK: the loss is not capped.")
    min_vol = min(l["vol"] for l in opts)
    if min_vol < config.OPT_THIN_VOLUME:
        flags.append(f"Thin liquidity: the least-traded leg had {min_vol:.0f} contracts today.")
    em = S * (sum(l["iv"] for l in opts) / len(opts)) * math.sqrt(years(dte(near_exp, today)))
    out_em = None
    if shorts:
        out_em = all(abs(l["strike"] - S) >= em for l in shorts)
    if extra.get("marginal"):
        flags.append(_iv_note(extra) + " Shown for reference.")
    if pay["range_limited"]:
        flags.append("Calendar: max profit and loss are measured within +/-50% of the price at the near expiry.")
    credit = -net if net < 0 else None
    for l in opts:  # model delta of each leg (Black-Scholes from the leg's IV), signed as a long option
        gr = greeks(S, l["strike"], years(dte(l["expiry"], today)), r, q, l["iv"], l["kind"])
        l["delta"] = round(gr["delta"], 2)
    return {
        "strategy": name, "rationale": _rationale(name, extra), "expiry": near_exp,
        "far_expiry": max(l["expiry"] for l in opts) if len({l["expiry"] for l in opts}) > 1 else None,
        "dte": dte(near_exp, today), "legs": legs,
        "net": {"type": "credit" if net < 0 else "debit", "per_share": round(abs(net), 2),
                "dollars": round(abs(net) * MULT * qty, 2)},
        "max_profit": None if max_profit is None else round(max_profit * qty, 2),
        "max_loss": None if max_loss is None else round(max_loss * qty, 2),
        "unlimited_profit": pay["unlimited_profit"], "unlimited_loss": pay["unlimited_loss"],
        "breakevens": pay["breakevens"],
        "return_on_risk": round(max_profit / max_loss, 2) if max_profit and max_loss else None,
        "credit_pct_width": round(credit / width, 2) if credit is not None and width else None,
        "pop_model": model_pop(legs, S, r, q, today), "greeks": net_greeks(legs, S, r, q, today),
        "expected_move": round(em, 2), "short_strikes_outside_expected_move": out_em,
        "liquidity": {"min_leg_volume": min_vol, "thin": min_vol < config.OPT_THIN_VOLUME},
        "defined_risk": not undefined, "flags": flags, "_width": width, "_extra": extra,
    }


def passes(c: dict, dropped: dict, min_credit: float = config.OPT_MIN_CREDIT_PCT_WIDTH) -> bool:
    """Candidate-level filters from the README (credit size and short-leg delta)."""
    if c["_extra"].get("credit_spread") and c["credit_pct_width"] is not None \
            and c["credit_pct_width"] < min_credit:
        dropped["credit_too_small"] = dropped.get("credit_too_small", 0) + 1
        return False
    shorts = [l for l in c["legs"] if l["kind"] != "S" and l["side"] == "sell"]
    if c["_extra"].get("credit_spread") and any(abs(l["delta"]) > config.OPT_MAX_SHORT_DELTA for l in shorts):
        dropped["short_delta_too_high"] = dropped.get("short_delta_too_high", 0) + 1
        return False
    return True
