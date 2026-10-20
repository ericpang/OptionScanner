# Stock Deep-Dive: Bull & Bear Case

A simple web app. Type a ticker (e.g. `AAPL.US`, `700.HK`, `600519.SH`) and get a deep analysis of the stock, ending in a **bull case**, a **bear case** and a **balanced verdict**. An **option trade scanner** (see [below](#option-trade-scanner)) then turns that analysis into candidate option structures for the stock, and can scan a list of symbols.

All market data comes from [Longbridge](https://open.longbridge.com). The analysis methods come from the skills in [.agents/skills/](.agents/skills/).

> Output is for reference only. It is not investment advice.

---

## What it does

1. The user enters a symbol in `<CODE>.<MARKET>` format.
2. The backend pulls quotes, financials, valuation, insider and news data from Longbridge.
3. It runs three analysis lenses (below) and checks that the financial statements agree with each other before it scores anything.
4. It builds the **bull case** and **bear case**, each with cited evidence, and shows them side by side with a verdict card.
5. Every figure links back to a **Data Source appendix** listing its source, fetch time and period.
6. The **Options** panel reads the option chain, judges whether implied volatility is rich or cheap, and builds candidate option trades that fit the stock's bull or bear view, with payoff, breakevens, Greeks and risk flags (see [Option trade scanner](#option-trade-scanner)).

### Pages / UI

| Section | Contents |
|---|---|
| Cache note | Only when the page was built from the data cache: when the data was fetched and when it will be refreshed (see [Data cache](#data-cache-per-ticker)) |
| Header | Symbol, price, market cap, PE/PB, 52-week range |
| Key Metrics | P/E (TTM), PEG, FCF growth, Debt/EBITDA, analyst ratings, operating margin, ROIC, ROE, net income growth, plus the additional metrics (see [Key metrics](#key-metrics)) |
| Price Chart | Daily candles, EMA 20/50/200, volume and volume profile with POC and value area (see [Price chart](#price-chart-daily-candles-ema-2050200-and-volume-profile)) |
| Business & Moat | Moat type and width, pricing power, industry runway |
| Financial Health | ROE history, FCF, leverage, margins, capex intensity |
| Fair value estimate | App-built DCF, earnings power value and Graham number, a sensitivity grid, and a reverse DCF; explicitly not GF Value (see [Fair value estimate](#fair-value-estimate-not-gf-value)) |
| Institutional Flows | 13F holder changes, insider trades, large-order capital flow, OBV / CMF, and an Accumulation / Neutral / Distribution read (see [Institutional buying and selling](#institutional-buying-and-selling)) |
| Options | IV vs HV, term structure and skew, put/call volume, and candidate option trades with payoff, breakevens, Greeks and risk flags; a **Scan** page ranks candidates across a list of symbols (see [Option trade scanner](#option-trade-scanner)) |
| Quality Scorecard | Piotroski F, Altman Z, Beneish M, Sloan accruals, Magic Formula, Graham, Buffett and ROIC vs. WACC, each with its published thresholds (see [Stock quality scorecard](#stock-quality-scorecard)) |
| News & Market Mood | The latest 10 news items (expandable to 25) plus analyst consensus, market temperature and community activity, with no automated sentiment scoring (see [Latest news and market mood](#latest-news-and-market-mood)) |
| Management & Insiders | Insider trades, buybacks, dividends, institutional holders |
| Quant Snapshot | Volatility regime, momentum, seasonality, factor z-scores |
| **Bull case** (green) | 3–5 evidence-backed arguments plus an upside price band |
| **Bear case** (red) | 3–5 evidence-backed arguments plus a downside price band |
| Verdict | Moat stars, Graham score, "great business / wait for price" vs "cheap but no moat" tag, expected holding period |
| Data Source appendix | Source, fetch time and period for every number, plus the reconciliation result |

---

## Key metrics

A **Key Metrics** panel sits under the header. Each metric shows its value, a 5-year trend where it applies, the peer median, and a green/amber/red tag. Each metric is also tied to the bull or bear case.

| # | Metric | Definition | Data source |
|---|---|---|---|
| 1 | **P/E (TTM)** | Price ÷ trailing-12-month diluted EPS (sum of the last 4 quarters of IS) | `calc-index` (PE), cross-checked against `financial-statement --kind IS --report qf` |
| 2 | **PEG** | P/E ÷ forward EPS growth %. Growth comes from Longbridge's `consensus` table: each upcoming quarter's EPS estimate against the same fiscal quarter a year earlier (up to 4 quarters). The P/E in the numerator is price ÷ trailing EPS **on the same basis** (non-GAAP `normalized_eps` where available, else GAAP), so a non-GAAP forecast is never compared with GAAP trailing EPS. Falls back to 3-year net income CAGR (labelled) when there is no consensus. Not shown if growth ≤ 0. **This is near-term growth, not the 3-5 year growth that many data sites use**, so values can differ from other sources. | `consensus`, `calc-index`, IS annual for the fallback |
| 3 | **Free Cash Flow Growth** | YoY and 3-year CAGR of FCF, where FCF = Operating CF − Capex | `financial-statement --kind CF --report af` / `qf` |
| 4 | **Debt / EBITDA** | (Short-term debt + long-term debt) ÷ TTM EBITDA, where EBITDA = operating income + D&A. Also shows net debt / EBITDA. | BS for debt, IS and CF for EBITDA |
| 5 | **Analyst Ratings** | Buy / Hold / Sell counts, consensus rating, mean target price and upside vs. current price | `institution-rating` (rating distribution and target price) |
| 6 | **Operating Margin** | Operating income ÷ revenue, TTM, with a 5-year trend | IS annual / quarterly |
| 7 | **ROIC** | NOPAT ÷ average invested capital, where NOPAT = operating income × (1 − effective tax rate) and invested capital = equity + debt − cash | IS and BS annual |
| 8 | **ROE** | Net income ÷ average shareholders' equity, 5–10 year track. Buffett threshold is ≥ 15%. | IS and BS annual |
| 9 | **Net Income Growth** | YoY and 3-year / 5-year CAGR of net income, with the latest quarter's YoY | IS annual / quarterly |

### Additional metrics

These fill gaps in the first nine: growth quality, other valuation lenses, balance-sheet risk, shareholder returns, sentiment and trading risk.

| Group | Metric | Definition | Data source |
|---|---|---|---|
| Growth | **Revenue growth** | YoY, 3y and 5y CAGR, and latest-quarter YoY | IS annual / quarterly |
| Growth | **EPS growth and revisions** | Forward EPS vs. 90 days ago (upward or downward revisions), and last 4 quarters of earnings surprise | `forecast-eps`, `financial-statement` |
| Profitability | **Gross margin** | Gross profit ÷ revenue, 5-year trend (a pricing-power signal; Buffett threshold ≥ 30%) | IS |
| Profitability | **Net margin and FCF margin** | Net income ÷ revenue, and FCF ÷ revenue | IS, CF |
| Profitability | **Cash conversion** | FCF ÷ net income (earnings quality; below 0.8 is a flag) | IS, CF |
| Valuation | **EV/EBITDA** | (Market cap + debt − cash) ÷ TTM EBITDA | `calc-index`, BS, IS |
| Valuation | **P/S and P/B** | Price to sales and price to book, each with its percentile vs. 10-year history | `calc-index`, `valuation` |
| Valuation | **FCF yield** | TTM FCF ÷ market cap | CF, `quote` |
| Valuation | **Valuation percentile vs. industry** | PE/PB/PS rank within the industry | `valuation`, `compare` |
| Balance sheet | **Interest coverage** | EBIT ÷ interest expense (below 3x is a flag) | IS |
| Balance sheet | **Current ratio and quick ratio** | Current assets (less inventory for quick) ÷ current liabilities | BS |
| Balance sheet | **Net debt / equity** | (Debt − cash) ÷ shareholders' equity | BS |
| Balance sheet | **Altman Z-score** | Standard 5-factor distress score (not applied to financials) | BS, IS |
| Quality | **Piotroski F-score** | 9-point fundamentals checklist | BS, IS, CF |
| Capital return | **Dividend yield and payout ratio** | Yield; dividends ÷ net income; years of consecutive growth | `dividend`, `calc-index` |
| Capital return | **Share count change** | 3-year change in diluted shares; buyback yield (net buybacks ÷ market cap) | `corporate`, IS, CF |
| Ownership | **Insider net activity** | Net insider buying or selling over 6 and 12 months (Form 4) | `insider-trades` (US only) |
| Ownership | **Institutional ownership** | % held and the latest 13F change among top holders | `shareholder`, `investors` |
| Sentiment | **Short interest** | % of float short and days to cover, where the market reports it | check `longbridge --help` |
| Trading risk | **Beta, 20d/60d historical volatility, max drawdown** | Computed from daily K-lines; HV percentile from the quant volatility reference | `kline` |
| Trading risk | **Relative strength** | 3, 6 and 12 month return vs. the market index and the sector | `kline` |
| Trading risk | **Distance from 52-week high/low and from EMA 200** | % above or below each | `quote`, `kline` |
| Events | **Next earnings date and recent filings** | Upcoming catalyst and the latest 10-K/10-Q/8-K | `filing`, `news` |

Skipped on purpose: the DuPont breakdown (covered by ROE plus margins and leverage) and any intraday-only or options-flow data (outside the scope of this app).

### How the metrics feed the bull and bear case

| Metric | Leans bull | Leans bear |
|---|---|---|
| P/E (TTM) | At or below the stock's own 10-year median and the peer median | Well above both |
| PEG | Below 1 | Above 2 |
| FCF growth | Positive for 3 years, with FCF at least 80% of net income | Declining, or negative FCF |
| Debt / EBITDA | Below 2x | Above 4x |
| Analyst ratings | Majority Buy and mean target above price | Majority Hold/Sell or target below price |
| Operating margin | Stable or expanding, above the peer median | Contracting for 2 or more years |
| ROIC | Above about 10–12% and above cost of capital | Below cost of capital |
| ROE | 15% or more and consistent, without heavy leverage | Under 10%, or high only because of leverage |
| Net income growth | Steady double-digit growth | Falling, or volatile and loss-making |
| Revenue growth | Growing faster than the peer median | Shrinking, or slower than peers |
| EPS revisions | Estimates revised up | Estimates revised down, or repeated misses |
| Gross margin | At or above 30% and stable | Falling for 2 or more years |
| Cash conversion | FCF at or above 0.8x net income | Below 0.8x for 2 or more years |
| EV/EBITDA, P/S, FCF yield | Below own history and peers | Well above both |
| Interest coverage, current ratio | Above 5x and above 1.5 | Below 3x or below 1 |
| Altman Z / Piotroski | Z above 3, F of 7 or more | Z below 1.8, F of 3 or less |
| Share count change | Shrinking (net buybacks) | Rising dilution |
| Insider net activity | Net buying | Sustained net selling |
| Short interest | Low and falling | High and rising |
| Price vs. EMA 200 and relative strength | Above EMA 200, outperforming | Below EMA 200, underperforming |

The thresholds are defaults you can change in `app/config.py`.

### Edge cases

- **Negative or zero earnings.** P/E and PEG show "n/m", and the growth rates show "n/m" where the base is negative.
- **Banks, insurers, brokers.** Debt/EBITDA, ROIC and operating margin are replaced by ROA, NIM, NPL and capital adequacy, and the panel says so.
- **Net cash.** Debt/EBITDA shows "net cash" when cash exceeds debt.
- **Missing fields.** Show "n/a" with the reason. Never silently estimate.
- **Reconciliation.** These metrics use the same statement data as the scoring, so they are blocked if the reconciliation gate fails.
- **Derived figures.** PEG, ROIC and Debt/EBITDA are calculated by the app, and the appendix marks them as derived with the formula and inputs used.

---

## Price chart: daily candles, EMA 20/50/200 and volume profile

A chart panel sits under the Key Metrics panel.

### What it shows

- **Daily candlesticks**, default range 1 year, with 6M / 1Y / 2Y / 5Y toggles.
- **EMA 20, 50 and 200** lines in three distinct colours, each toggleable from the legend.
- **Volume bars** in a lower pane, coloured by up or down day.
- **Volume profile**, a horizontal histogram on the right that shares the price axis with the candles. It marks:
  - **POC** (point of control): the price level with the most volume.
  - **VAH / VAL** (value area high and low): the range holding 70% of the volume. The value area is shaded.
  - Bars inside the value area are drawn darker than those outside.
- **Signals under the chart**: price vs. each EMA, whether EMA 20/50 and 50/200 have recently crossed (golden or death cross), and where the price sits relative to POC and the value area. These also feed the bull and bear case (for example, price above EMA 200 and above the value area is a bull point; price below EMA 200 and below VAL is a bear point).

### How it is computed

- **Data:** `longbridge kline SYMBOL --period day --count N --format json`. The backend requests the chart range plus 250 extra bars of warm-up so the EMA 200 is already settled on the first visible day. Adjusted prices are used where the CLI supports it (check `longbridge kline --help`).
- **EMA:** `ema[t] = close[t] × k + ema[t-1] × (1 − k)` with `k = 2 / (n + 1)`, seeded with the simple average of the first `n` closes. Computed on the server in `app/chart.py`, not in the browser, so the numbers match the API.
- **Volume profile:** the price range of the visible window is split into 50 equal bins (configurable). Each daily bar's volume is spread evenly across the bins between its low and high. The bin with the largest total is the POC. The value area starts at the POC and expands to the larger neighbouring bin each step until it holds 70% of total volume.
- **Limit:** a profile built from daily bars is an approximation, because the real distribution within each day is unknown. Intraday bars would be more accurate (for example, `kline --period 5m` or `1h` where available), so `app/chart.py` accepts a finer-granularity input if you want to add it later. The chart footnote says "profile estimated from daily bars".

### Frontend

[Plotly.js](https://plotly.com/javascript/) is the recommended charting library. It supports candlesticks, line overlays and a horizontal bar histogram with a shared y-axis in one figure, with zoom and hover. Plotly's chart is drawn with two panels: candles with EMAs on the top left, volume on the bottom left, and the profile on the right sharing the price axis. TradingView's lightweight-charts also works for the candles and EMAs but has no built-in volume profile, so it would need a custom overlay.

The chart must work in light and dark themes, and the EMA colours must stay distinguishable (and not rely on colour alone, so the legend labels each line).

### API

`GET /api/chart?symbol=AAPL.US&range=1y&bins=50`

```jsonc
{
  "symbol": "AAPL.US",
  "candles": [ { "t": "2026-01-02", "o": 0, "h": 0, "l": 0, "c": 0, "v": 0 } ],
  "ema": { "20": [0], "50": [0], "200": [0] },
  "volume_profile": {
    "bins": [ { "price_low": 0, "price_high": 0, "volume": 0 } ],
    "poc": 0, "vah": 0, "val": 0
  },
  "signals": {
    "above_ema": { "20": true, "50": true, "200": true },
    "cross_50_200": "golden | death | none",
    "price_vs_value_area": "above | inside | below"
  }
}
```

---

## Institutional buying and selling

Yes. No single indicator shows what institutions are doing, so the app combines **reported holdings and trades** (what they did, with a delay) with **flow indicators** (what money appears to be doing now). A panel next to the chart shows both, with a net **Accumulation / Neutral / Distribution** read.

### 1. Reported activity (hard data, but delayed)

| Indicator | What it shows | Delay | Data source |
|---|---|---|---|
| **13F holdings change** | The 20 largest holders **by position size** (not by size of change, which would make every holder look like a buyer), how many added or trimmed, and the net shares added. The panel shows the 10 largest. | About 45 days after quarter end (US only) | `shareholder --sort owned` |
| **Insider trades (Form 4)** | Open-market buys and sells by officers and directors; net buying or selling over 3, 6 and 12 months; cluster buys (3 or more insiders in 30 days) | 2 business days | `insider-trades` |
| **Buybacks and share count** | Net buybacks vs. dilution (the company itself as a buyer) | Quarterly | `corp-action`, `financial-statement` |

The 13F and Form 4 data are real filings, so they are the strongest evidence but arrive late. The panel always shows the "as of" date.

### 2. Flow indicators (near real time, an estimate)

| Indicator | What it shows | Source |
|---|---|---|
| **Capital flow by order size** | Net inflow or outflow split into large, medium and small orders. Large-order net flow is the closest proxy for institutional activity. Longbridge's `capital` command returns only the latest session's split by order size, so the app shows that session, not a 20-day history. | `capital` |
| **Capital distribution** | Today's inflow and outflow broken down by order size (large / medium / small) | `capital` |
| **HK broker queue** | For HK stocks, which brokers sit on the bid and ask | `brokers` (not wired into the app yet) |
| **On-Balance Volume (OBV)** | Running total of volume, added on up days and subtracted on down days. Rising OBV with flat price suggests accumulation. | Computed from daily K-lines |
| **Accumulation/Distribution line and Chaikin Money Flow (20)** | Where the close sits within the day's range, weighted by volume. CMF above 0 suggests buying pressure. | Computed from daily K-lines |
| **Up/down volume ratio (20d, 50d)** | Volume on up days ÷ volume on down days | Computed from daily K-lines |
| **High-volume days** | Days with volume above 2x the 50-day average, labelled as accumulation (close up, near the high) or distribution (close down, near the low). Marked on the main chart. | Computed from daily K-lines |
| **Volume-profile context** | Whether heavy volume sits below price (support) or above it (overhead supply) | Existing volume profile |

### Net read

Signal wording follows the direction: a signal that points to distribution is worded as selling ("Insiders net selling (6m)", "OBV falling (20d)"), so the "Distribution signals" list never contains a bullish-sounding phrase. The panel lists accumulation, distribution, neutral and missing signals separately. Holder data is quarterly and up to about 45 days old, while the price and volume signals are current, so the label can legitimately disagree with the holder table.

The app scores the signals above on a simple tally:

- **Accumulation:** at least 3 of these are true: net 13F buying, net insider buying, positive large-order net flow over 20 days, rising OBV, CMF above 0, up/down volume ratio above 1.2.
- **Distribution:** the same test in reverse.
- **Neutral:** anything else, or when too few signals are available. The panel says which signals were missing.

The result also feeds the bull and bear case, for example "large orders net buying for 20 days while insiders buy" (bull) or "13F holders trimmed and large-order flow is negative" (bear).

### Limits

- Longbridge data does not identify who is trading. "Large orders" is a size-based proxy, and a big order is not always an institution.
- Dark-pool and block-trade prints are not available from the skills here, so they are left out.
- 13F covers US-listed stocks only and shows long positions only. For HK, CN, and SG stocks, the panel relies on capital flow, the HK broker queue, and the computed volume indicators, and says so.
- OBV, A/D and CMF are derived from price and volume, so they infer buying pressure and don't measure institutional trades.
- Short interest is covered under the additional metrics and is not repeated here.

### API

`GET /api/flows?symbol=AAPL.US`

```jsonc
{
  "symbol": "AAPL.US",
  "reported": {
    "institutional_ownership_pct": 0, "change_qoq_pct": 0, "as_of": "2026-06-30",
    "top_holders": [ { "name": "", "shares": 0, "change_pct": 0 } ],
    "insider": { "net_3m": 0, "net_6m": 0, "net_12m": 0, "cluster_buy": false }
  },
  "flow": {
    "capital_flow": [ { "date": "", "large": 0, "medium": 0, "small": 0 } ],
    "large_order_net_20d": 0,
    "obv": [0], "ad_line": [0], "cmf_20": 0, "updown_volume_20d": 0,
    "high_volume_days": [ { "date": "", "type": "accumulation | distribution" } ]
  },
  "net_read": { "label": "accumulation | neutral | distribution", "signals_true": [], "signals_missing": [] }
}
```

---

## Latest news and market mood

A **News** panel lists the latest news for the stock, and a small **Market mood** strip shows sentiment facts that come straight from the data, with no automated scoring.

### Latest news (10 items, expandable to 25)

- **Count:** 10 items by default, because the last few days of coverage are what matters for a deep analysis, and 3 items let one story dominate. A "Show more" control loads up to 25. Both numbers are in `app/config.py`.
- **Source:** `longbridge news SYMBOL --format json` first, as the `longbridge` skill directs. Use WebSearch only if Longbridge returns fewer items than requested or nothing recent (breaking news, macro events). Any item from WebSearch is labelled with its publisher, date and URL.
- **Selection:** newest first, with duplicates and near-duplicates removed (same story from several outlets, matched on normalised title). Items older than 30 days are marked "stale".
- **Grouping:** items are grouped by day (Today, Yesterday, This week, Earlier) so the timeline is easy to scan.
- **Each item shows:** headline, publisher, publish time, a 1–2 line summary (the article's own opening text from `longbridge news detail <id>`, not generated), and a link to the full article.
- **Filings:** the latest 8-K / 10-Q / 10-K from `longbridge filing SYMBOL` appear in a separate "Recent filings" list (last 5). They do not take news slots.
- **Search:** a box runs `longbridge news search "keyword"` for this symbol, for example "guidance" or "lawsuit".

### Market mood (facts only, no scoring)

| Item | What it shows | Source |
|---|---|---|
| **Analyst consensus** | Buy / Hold / Sell counts and target price vs. current (the Analyst Ratings metric) | analyst commands |
| **Market temperature** | The market-wide 0–100 sentiment index for context | `market-temp` |
| **Community activity** | Number of recent discussion posts vs. their 30-day average, with the latest few post titles and links | `topic SYMBOL` |
| **Short interest** | % of float short, where the market reports it (see additional metrics) | check `longbridge --help` |

These are shown as reported. The app does **not** classify headlines or posts as positive or negative, and does not produce a combined sentiment label or score, because automated text sentiment is unreliable. Readers judge the news themselves from the headlines and links.

### How it feeds the bull and bear case

- The news does not change any score. The bull and bear case come from the quantitative metrics and quality scores.
- The thesis step may quote a news item, with its headline, date and link, next to a bull or bear point it relates to (for example a guidance change next to the margin trend). The reader judges the link between them.
- An optional `GEMINI_API_KEY` (Google Gemini) is used only to write the bull and bear narrative from the fetched figures. It is not used to score news.

### Limits

- Longbridge news coverage differs by market, and some HK/CN items may be in Chinese. They are shown in the original language, labelled as such.
- Community post counts show attention, not direction.
- The app does not infer or invent news. Everything shown comes from the fetched articles.

### API

`GET /api/news?symbol=AAPL.US&limit=10`

```jsonc
{
  "symbol": "AAPL.US",
  "news": [
    {
      "id": "", "title": "", "publisher": "", "published_at": "", "url": "",
      "summary": "", "language": "en", "stale": false, "source": "longbridge | websearch"
    }
  ],
  "filings": [ { "type": "10-Q", "filed_at": "", "url": "" } ],
  "mood": {
    "analyst_consensus": { "buy": 0, "hold": 0, "sell": 0, "target_mean": 0 },
    "market_temperature": 0,
    "community": { "posts": 0, "volume_vs_30d": 0, "latest": [ { "title": "", "url": "" } ] },
    "short_interest_pct": null
  }
}
```

---

## Fair value estimate (not GF Value)

The page shows an **app-built fair value estimate** under the quality scorecard. It is **not GuruFocus's GF Value**. GF Value is a proprietary number from GuruFocus's own model, and its only legitimate programmatic source is the GuruFocus API, which is for Premium members and needs an API token. The documentation could not be read from here (the site returned 403), so no GuruFocus integration was built. If you later get a token, a GF Value card can be added next to this one.

The estimate uses three methods, all from the financial statements and the quote (`app/valuation.py`):

| Method | How it works | Fits |
|---|---|---|
| **Discounted cash flow** (headline) | Free cash flow (TTM) to equity. Stage-1 growth for 5 years, fading linearly to 3% terminal growth over 5 more years, discounted at the cost of equity (risk-free rate + beta x equity premium; beta is assumed 1.0 because the data has none). Stage-1 growth is the **median** of the available growth rates (FCF, net income and revenue 3-year CAGR, and consensus EPS growth), floored at 0 and **capped at 20%**. | Companies with positive free cash flow |
| **Earnings power value** | Normalised operating earnings (average margin x TTM revenue, after tax) divided by the cost of capital, plus cash, less debt. Gives no credit for growth, so it is a floor-style value. | Companies with positive operating earnings |
| **Graham number** | sqrt(22.5 x EPS x book value per share), from the quoted P/E and P/B. | Steady, asset-backed companies; far below the price of most growth stocks |

- **Per-share value needs no share count.** It is price x (equity value / market cap), so the share count cancels. The statements carry none.
- **Sensitivity grid.** Fair value at three discount rates (-1, 0, +1 point) and three growth rates (-5, 0, +5 points). The range shown beside the headline is the grid's minimum and maximum.
- **Reverse DCF.** The page also solves for the stage-1 growth that makes the DCF equal today's price, for example "today's price implies free cash flow growing about 19% a year for 5 years". This needs no growth assumption from the app, so it is the most robust line in the section.
- **Rating bands** on price / fair value: up to 0.80 undervalued, 0.80 to 0.95 modestly undervalued, 0.95 to 1.15 roughly fair, 1.15 to 1.40 modestly overvalued, above 1.40 overvalued. These cut-offs are the app's own.
- **Thesis.** Only the extremes add a point: price at or below 0.80 of the DCF value is a bull point and at or above 1.40 is a bear point, both labelled "(app model)". The DCF value also appears in the reference price levels.
- **Not applicable** to banks, insurers and REITs, and the DCF is skipped (with the reason) when free cash flow is not positive. Nothing is estimated for missing inputs.

Known biases, stated on the page: free cash flow is before stock-based compensation (which flatters software and chip companies), a beta of 1.0 is too high for defensive stocks such as KO (so their DCF is understated), and the 20% growth cap is deliberately conservative for very fast growers. Change `TERMINAL_GROWTH` and `DCF_MAX_GROWTH` in `app/config.py` to test other assumptions.

---

## Stock quality scorecard

A **Quality Scorecard** panel near the top shows well-known, published scoring systems side by side. The app uses these instead of its own invented composite score. Each shows its score, the published thresholds, and the inputs behind it, so the reader can check the arithmetic.

| Scorecard | Origin | What it measures | Scale and published thresholds |
|---|---|---|---|
| **Piotroski F-Score** | Piotroski (2000) | Fundamental strength: profitability, leverage/liquidity, operating efficiency (9 yes/no tests) | 0–9. 8–9 strong, 0–2 weak |
| **Altman Z-Score** | Altman (1968) | Bankruptcy risk | Above 2.99 safe, 1.81–2.99 grey zone, below 1.81 distress. Manufacturing formula; use the Z″ variant for non-manufacturers. Not for financials |
| **Beneish M-Score** | Beneish (1999) | Likelihood of earnings manipulation (8 ratios) | Above -1.78 flags possible manipulation. Not for financials |
| **Sloan accruals ratio** | Sloan (1996) | Earnings quality: (net income − operating CF) ÷ average assets | Within ±10% fine, above 25% a red flag |
| **Greenblatt Magic Formula** | Greenblatt | Cheap and good: earnings yield (EBIT ÷ EV) and return on capital, each ranked against peers | Shows the two figures and their peer percentile ranks |
| **Graham score** | Graham, via the skill | Deep-value safety: NCAV, PE, PB, dividend, debt cover, earnings stability | 0–100 plus value-trap flags (from `longbridge-value-investing`) |
| **Buffett moat rating** | Buffett, via the skill | Moat, financial health, management, valuation, long-term visibility | 1–5 stars per dimension (from `longbridge-value-investing`) |
| **ROIC vs. cost of capital** | Standard value-creation test | Whether the business earns more than its capital costs | ROIC − WACC. Positive is value-creating |

### Rules

- **Published definitions only.** Formulas and thresholds follow the original papers or the skill references. The app does not tune weights, and does not merge these into one "quality number". The header shows how many scorecards pass their favourable threshold (for example "5 of 7 pass"), and each row shows the actual score.
- **Inputs from the same statements.** All of them draw on the financial statements that already passed the reconciliation gate. If the gate fails, no scorecard is shown.
- **Sector rules.** Banks, insurers and REITs skip Altman, Beneish, the Piotroski items that need working capital, and NCAV. The row shows "not applicable" with the reason. Companies listed under 3 years show scores flagged "limited history".
- **Missing inputs.** If an input is missing, the score is "n/a" and says which input. It is never estimated.
- **Sources.** Each row names its source (Longbridge fields used) and the fiscal period, and appears in the Data Source appendix with the formula.
- **Context.** Piotroski and Magic Formula come from studies of broad US stock samples. The row notes that a score is a screening aid and not a prediction.

### API

The scorecard rides on the main `GET /api/analyze` response under `quality`:

```jsonc
"quality": {
  "piotroski": { "score": 0, "tests": { "roa_positive": true }, "band": "strong | average | weak" },
  "altman_z": { "score": 0, "zone": "safe | grey | distress | n/a" },
  "beneish_m": { "score": 0, "flag": false },
  "sloan_accruals": { "ratio": 0, "flag": false },
  "magic_formula": { "earnings_yield": 0, "return_on_capital": 0, "rank_ey": 0, "rank_roc": 0 },
  "graham": { "adjusted_score": 0, "value_trap": false },
  "buffett": { "stars": { "moat": 0, "financials": 0, "management": 0, "valuation": 0, "visibility": 0 } },
  "roic_vs_wacc": { "wacc": 0, "spread": 0 },
  "passing": 0, "applicable": 0
}
```

---

## Option trade scanner

> **Status: the single-stock Options panel is built and tested** (`app/options.py`, `app/strategies.py`, `app/option_scan.py`, `/api/options`, `/api/options/payoff`, 20 tests in `tests/test_options.py`). It was run against live Longbridge data for AAPL and TSLA. **Not built yet:** the multi-symbol **Scan** page (`/api/options/scan`) and HK warrants. Where this section names CLI behaviour, it was checked against the installed CLI (v0.30.0) in October 2026. It integrates the [longbridge-derivatives](.agents/skills/longbridge-derivatives/SKILL.md) skill.

An **Options** panel turns the stock analysis into a short list of candidate option trades for that stock. A separate **Scan** page (planned) will run the same logic over a list of symbols and rank the candidates. The output is a set of structured candidates with their risks, not a recommendation. The skill's strategy descriptions are educational, and the wording here stays neutral.

### How the derivatives skill is used

| Skill reference | What the app takes from it |
|---|---|
| [option.md](.agents/skills/longbridge-derivatives/references/option.md) | Contract discovery: list expiries, then strikes for one expiry, then quote the contracts. Output fields and the OCC symbol format. |
| [options-strategy.md](.agents/skills/longbridge-derivatives/references/options-strategy.md) | The **strategy matrix** (market view x IV level) that decides which structures to build, the six-step workflow, and the output template (market context, legs, key metrics, rationale, alternative). |
| [options-volatility.md](.agents/skills/longbridge-derivatives/references/options-volatility.md) | **IV vs HV**, IV percentile rank, and the volatility smile and skew (put skew = OTM put IV minus ATM IV). Provides the "rich / fair / cheap" read that feeds the strategy matrix. |
| [options-pnl.md](.agents/skills/longbridge-derivatives/references/options-pnl.md) | Payoff at expiry for each leg type, breakevens, max profit and max loss, and net Greeks. Drives the payoff chart and the key-metrics block. |
| [options-advanced.md](.agents/skills/longbridge-derivatives/references/options-advanced.md) | **Calendar and diagonal spreads** (when the near/far IV ratio is above 1.1), skew trades (25-delta put vs call IV), and the IV/HV thresholds (above 1.3 rich, below 0.8 cheap). |
| [warrant.md](.agents/skills/longbridge-derivatives/references/warrant.md) | Out of scope for the first version (HK warrants and CBBCs are a different product). |

The `longbridge-quant` skill's [volatility-strategy.md](.agents/skills/longbridge-quant/references/volatility-strategy.md) (HV percentile and long-vol vs short-vol regime) supplies the HV side.

### What the CLI actually returns

These differ from the skill's documentation in places, so the app is built to the real behaviour:

| Command | Real behaviour |
|---|---|
| `option chain SYM.US` | A list of expiry dates, **including dates that have already passed**. The app drops expiries before today. |
| `option chain SYM.US --date YYYY-MM-DD` | One row per strike with `strike`, `call_last`, `call_iv`, `call_vol`, `put_last`, `put_iv`, `put_vol`, `standard`. **No contract symbols, no open interest and no Greeks**, although the skill says it returns symbols. Deep in-the-money rows can carry nonsense IV (for example 200%) and are filtered out. |
| `option quote <OCC>` | Fields: `last`, `volume`, `implied_volatility`, `open_interest`, `strike_price`, `expiry_date`, `direction`, `contract_multiplier`, `historical_volatility`, `underlying_symbol`, `timestamp`. **No delta, gamma, theta or vega.** There is **no bid or ask**, only the last trade, which can be stale. `open_interest` was 0 on a contract that had traded that day, so it is not relied on. |
| OCC symbol | `TICKER` + `YYMMDD` + `C` or `P` + strike x 1000, **with the `.US` suffix and no zero padding**: `AAPL261120C327500.US` (a $327.50 call). The skill's 8-digit zero-padded form and the form without `.US` both return an empty list. |
| `option volume SYM.US` | Today's call and put volume. `option volume daily SYM.US --count N` gives the history: total, put and call volume, put/call volume ratio, total, put and call open interest, put/call open-interest ratio. **US market only.** |

Because the chain has no symbols, the app builds the OCC symbol from the strike and expiry, then confirms it with `option quote`. An empty reply means the contract does not exist and the candidate is dropped.

### Scan pipeline (one stock)

1. **Market view.** Taken from the analysis already on the page (override with the View selector: Auto, Bullish, Neutral, Bearish). The automatic view is a tally of up to five inputs, listed in full in the panel:
   - thesis: bull points minus bear points, halved and capped at plus or minus 2 points;
   - price above (+1) or below (-1) EMA 200;
   - a recent EMA 50/200 golden (+1) or death (-1) cross;
   - the institutional-flow label: accumulation (+1) or distribution (-1);
   - the quality scorecards: 60% or more passing (+1), 30% or fewer (-1).
   A score of +3 or more is Bullish, -3 or less is Bearish, anything between is Neutral. If the fundamental analysis is unavailable (for example the reconciliation check stopped it), the thesis and quality inputs are skipped and the panel says so. The thresholds live in `app/config.py`.
2. **Expiry window.** Default 21 to 60 days to expiry (editable in the panel); expiries under 7 days and expiries already in the past are excluded. To stay within the CLI rate limit, at most 5 chains are read: expiries spread across the window plus two further out for the term structure. Candidates are built for every expiry inside the window, so the same structure appears at several expiries and can be compared.
3. **IV environment.** ATM IV for each expiry (call and put IV at the strike nearest the price, interpolated) against HV from the quote's `historical_volatility` and the app's own HV20/HV60. IV/HV above 1.3 is **rich**, below 0.8 is **cheap**, otherwise **fair**. Longbridge provides no IV history, so a true IV percentile is not available. The app records each day's ATM IV in a small local file (`data/iv_history.json`, git-ignored) and shows the percentile once it has 60 observations; until then the panel shows n/a with the number of observations so far, not a proxy. Demo mode does not write to this file.
4. **Skew and term structure.** Skew is OTM put IV minus ATM IV and 25-delta put IV minus 25-delta call IV (delta from Black-Scholes). Term structure is ATM IV by expiry. A near/far ratio above 1.1 adds calendar and diagonal candidates.
5. **Candidates.** The strategy matrix picks structures; strikes come from live chain rows:

   | View | IV | Structures built |
   |---|---|---|
   | Bullish | any | Long call, bull call spread |
   | Bullish | rich | Bull put spread |
   | Bearish | any | Long put, bear put spread |
   | Bearish | rich | Bear call spread |
   | Neutral | rich | Iron condor |
   | Neutral | cheap | Long straddle, long strangle |
   | Neutral | fair | Iron condor, long straddle, long strangle, each flagged "no clear edge" (the skill's matrix has no neutral row for fair IV) |
   | Neutral | near ATM IV / far ATM IV above 1.1 | Calendar spread (call calendar at the strike nearest the price) |
   | Own shares (optional input) | any | Covered call, protective put |

   **Credit structures and IV.** The skill's matrix sells premium (bull put spread, bear call spread, iron condor) only when IV is rich, so with fair IV a bullish view produced only debit trades (long call, bull call spread). The **"Include credit spreads in any IV"** option (on by default; `include_credit=false` restores the strict matrix) adds credit structures in every IV regime. When IV is not rich they carry a flag, "IV is fair, so selling premium has no IV edge" (or "IV is cheap ... against the matrix"), and their rationale no longer claims rich IV. Each credit structure is tried with a **narrow and a wide wing** (about 3% and 6% of the price, `OPT_WING_PCTS`), and duplicates are removed.

**Minimum credit.** A credit spread must collect at least 25% of its wing width by default; the panel's **Min credit** box (`min_credit`, 0 to 0.6) changes it. When spreads are hidden by it the panel says how many. On NVDA with fair IV, 25% leaves one bull put spread and 15% shows five; the lower the credit relative to width, the more is risked for each dollar collected (the table shows return on risk).

Short strikes target about 0.25 delta (candidates are dropped if a short leg is above 0.35); debit structures buy about 0.60 delta and sell about 0.30 delta. The wing width is about 3% of the price, rounded to the strike grid. The expected move (price x IV x sqrt(years)) is shown, with a note on whether the short strikes are outside it. **Undefined-risk structures (short straddle, short strangle, risk reversal) are off by default** and appear only if "allow undefined risk" is switched on.
6. **Metrics per candidate** (all computed from the legs, nothing guessed):
   - net debit or credit, from last prices;
   - max profit, max loss and breakevens (payoff at expiry, per the P&L reference);
   - return on risk (max profit / max loss) and, for credit trades, credit as a percentage of width;
   - net delta, gamma, theta per day and vega per 1 IV point, from **Black-Scholes using each leg's IV** (American-style early exercise is ignored; the dividend yield comes from `calc-index`; the risk-free rate is `RISK_FREE` in `app/config.py`);
   - a **model-based probability of profit** (lognormal at expiry using the leg-weighted IV), labelled as a model estimate;
   - liquidity per leg: chain volume (flagged thin under 10 contracts) and a stale-quote flag when a leg's last trade is older than the last session.
7. **Filters.** Drop candidates with a leg whose last price is under $0.05, whose chain volume is zero, or whose IV is outside 5% to 150%; with a credit under 25% of width; or whose short leg is more than 0.35 delta. Each drop is counted and listed so it is clear why a strike was skipped.
8. **Risk flags** on every candidate: earnings before expiry (from `longbridge finance-calendar report --symbol`, next 150 days; "earnings date unknown" if the lookup fails), early-assignment risk on short American-style legs (worse near an ex-dividend date), undefined risk, thin liquidity, and a note that fills at the last price are not guaranteed because no bid or ask is available.

There is **no composite score**. Candidates are sorted by a column the user picks (default: probability of profit among those passing the filters), and every component is shown. This follows the same rule as the quality scorecard: published definitions and visible inputs, not invented weights.

### Scan mode (many stocks)

`Scan` takes a pasted list of symbols (up to 20) and runs a lighter pipeline per symbol: quote, daily bars for HV, the expiries in the window, one chain per chosen expiry, and one batched `option quote` for the candidate legs. About 8 CLI calls per symbol, within the 10 calls per second limit; results are cached for `CACHE_TTL_SECONDS`.

The results table has one row per candidate across all symbols: symbol, structure, expiry, legs, credit or debit, max loss, return on risk, probability of profit, IV/HV, flags. Optional filters:

- IV/HV above or below a level,
- defined risk only (on by default),
- **quality filter**: only symbols that pass at least N of the stock quality scorecards, so premium-selling candidates are not ranked on stocks with weak fundamentals (this runs the full analysis, so it is slower).

### UI

An **Options** section below the institutional-flow panel. It loads after the other panels (the scan makes about 20 CLI calls and takes 10 to 30 seconds on live data), and the **Scan** button re-runs it with the current controls:

- controls: view (Auto / Bullish / Neutral / Bearish), days-to-expiry range, defined risk only, include credit spreads in any IV, minimum credit as a % of width, shares owned (enables covered call and protective put); each row is tagged credit or debit;
- the **market view** with its full tally;
- **IV cards**: ATM IV, HV20/HV60, IV/HV60 with the rich / fair / cheap tag, IV percentile (n/a until 60 observations are stored), put/call volume and open-interest ratios, 25-delta skew, next earnings date;
- **term structure** (ATM IV by expiry), **skew** (IV by strike for the focus expiry) and the daily **put/call volume ratio** chart;
- a **candidates table**, sortable by any column (default: probability of profit), with the net debit or credit, max profit and loss, breakevens, model POP, return on risk, delta, theta and the number of risk flags;
- click a row for the **legs** (with their OCC symbols), the rationale, net Greeks, all flags and a **payoff chart** (at expiry and the model value today, with breakevens and the current price marked).

### API

`GET /api/options?symbol=AAPL.US&view=auto&dte_min=21&dte_max=60&defined_risk=true&shares=0&include_credit=true&min_credit=0.25`

Validation: `view` is one of auto, bullish, bearish, neutral; `dte_min` and `dte_max` are 7 to 400 with `dte_min <= dte_max`; non-US symbols return `supported: false` with a message.

```jsonc
{
  "symbol": "AAPL.US", "supported": true, "spot": 343.46, "dividend_yield": 0.0031, "risk_free": 0.043,
  "view": { "value": "bullish", "mode": "auto", "auto_value": "bullish", "score": 5,
            "inputs": [ { "name": "Price vs EMA 200", "points": 1, "detail": "above" } ], "rule": "score >= +3 bullish, ..." },
  "iv": {
    "atm_iv": 0.2551, "focus_expiry": "2026-11-27", "hv20": 0.1604, "hv60": 0.2483, "iv_hv": 1.03, "regime": "fair",
    "percentile": null, "percentile_note": "needs 60 stored daily observations (1 so far)",
    "term_structure": [ { "expiry": "2026-11-13", "dte": 24, "atm_iv": 0.2833 } ],
    "skew": { "otm_put_minus_atm": 0.0479, "put_25d_minus_call_25d": 0.056, "points": [ { "strike": 300, "iv": 0.34, "side": "put" } ] },
    "put_call": { "latest": { "date": "2026-10-16", "pc_volume": 0.92, "pc_oi": 0.81 }, "history": [] }
  },
  "candidates": [
    {
      "strategy": "bull call spread", "rationale": "...", "expiry": "2026-11-13", "far_expiry": null, "dte": 24,
      "legs": [ { "side": "buy", "kind": "C", "strike": 340, "expiry": "2026-11-13", "qty": 1, "last": 12.5,
                  "iv": 0.31, "delta": 0.6, "occ": "AAPL261113C340000.US", "vol": 120 } ],
      "net": { "type": "debit", "per_share": 7.39, "dollars": 739.0 },
      "max_profit": 761.0, "max_loss": 739.0, "unlimited_profit": false, "unlimited_loss": false,
      "breakevens": [347.39], "return_on_risk": 1.03, "credit_pct_width": null, "pop_model": 0.428,
      "greeks": { "delta": 28.3, "gamma": 0.045, "theta": -3.13, "vega": 2.0 },
      "expected_move": 27.25, "short_strikes_outside_expected_move": false,
      "liquidity": { "min_leg_volume": 12, "thin": false }, "defined_risk": true,
      "flags": [ "Fills at the last price are not guaranteed: the CLI gives no bid or ask." ],
      "payoff": { "prices": [], "at_expiry": [], "today_model": [], "contracts": 1 }
    }
  ],
  "dropped": { "no_volume": 99, "price_below_min": 135, "iv_out_of_range": 14, "credit_too_small": 3 },
  "earnings_date": "2026-11-02", "warnings": [], "notes": [ "..." ]
}
```

`GET /api/options/payoff?symbol=AAPL.US&occ=AAPL261113C340000.US&side=buy&occ=...&side=sell&qty=1&spot_range=0.3` prices 1 to 6 legs from live quotes and returns `prices`, `at_expiry`, `today_model` (dollars) and `contracts`. A contract that does not exist, or a side other than buy or sell, returns 400.

`POST /api/options/scan` (planned, with the Scan page) will take `{ "symbols": [...], "dte_min": 21, "dte_max": 60, "defined_risk": true, "min_quality_pass": 0 }` and return ranked candidates across symbols.

### Limits

- **US options only to start.** `option volume` is documented as US-only, and the skill lists HK options without a tested example. Other markets return `supported: false` with a message.
- **Slow on live data.** A scan reads up to 5 chains, then confirms every leg with batched `option quote` calls (30 contracts per call). Measured at 20 to 30 seconds for AAPL and TSLA.
- **Long puts report a large max profit** (the stock going to zero) and the calendar spread figures are measured within +/-50% of the price; both are labelled.
- **No bid or ask, no reliable open interest.** Prices are last trades and can be stale; real fills will differ. The panel says so on every candidate.
- **Greeks, probability of profit and expected move are model outputs** (Black-Scholes, lognormal), not market data, and are labelled as such. American-style early exercise and discrete dividends are ignored.
- **IV percentile starts empty** and builds up from the app's own daily snapshots.
- **Not advice.** Candidates describe structures and their risks. Short options can lose far more than the premium collected, and the app does not know the user's account size, margin or tax situation.

---

## Data cache (per ticker)

A ticker that was fetched recently is not fetched again. Its data is saved in the `data/` folder, one JSON file per ticker, and the page is built from that file instead of from Longbridge.

| When you search a ticker | What happens |
|---|---|
| There is no file for it | Everything is fetched as usual and saved to `data/TICKER-TIMESTAMP.json`, for example `data/AAPL.US-20261020T143210+0800.json`. |
| The file is under 30 minutes old | Nothing is fetched. The whole page (analysis, chart, flows, news and the option scan) is built from the file by the same code that builds it from live data, so it is the same page. A line under the search box says *Data fetched from cache as of 20 Oct 2026, 2:32 pm (12 min ago)* and when the data will be refreshed. |
| The file is 30 minutes old or more | Everything is fetched again and saved to a new file named with the current time. The old file is deleted. |

How it works:

- **The file name is the clock.** The timestamp is local time with its UTC offset (`+0800`), and it alone decides whether a file is fresh, so renaming a file makes it older or younger. A time in the future is not trusted. The lifetime is `DISK_CACHE_MINUTES` in `.env` (default 30; 0 turns the cache off).
- **The file holds the fetched data, not the analysis.** It is a log of the data calls made for the ticker (`quote`, `kline`, `statements`, `option_chain` and so on), one per line, with what each returned. Scores, charts and option candidates are recomputed from it every time, which is why a cached page cannot differ from a live one. The Data sources table shows when the data was fetched, not when it was searched.
- **What the file does not hold is fetched and added.** An option scan that needs an expiry that was not scanned before, or **Show more** news, is fetched live and merged into the file, which keeps its name and time. If the page needed any of that when it loaded, the note says how many items.
- **A failed call is tried again.** It is not saved, so a hiccup such as the SEC rate limit on insider trades is not frozen in for 30 minutes. The exception is a call the app already treats as unimportant: its failure is saved, or every search would repeat it. Today that is the news summary: `longbridge news detail` answers in Markdown, not JSON (checked on CLI 0.26.0 in October 2026), so every summary fails after three attempts, about 2 seconds per article. The news panel therefore shows headlines only and takes about 20 seconds on a live fetch, but a cached one is instant.
- **Only live data is cached.** Demo mode never writes files. The IV history (`data/iv_history.json`) is added to only when the option chains were fetched live, so a cached page does not stamp today's date on old IV figures.
- **A file that cannot be read or written never breaks the page.** An unreadable file, or one written by another version of the code (`VERSION` in [app/snapshot.py](app/snapshot.py), to be bumped when a data call changes what it returns), is ignored and replaced. If `data/` cannot be written, the page is built from live data and a warning goes to the server log.
- **The files are git-ignored** (`data/*.json`). To force a fresh fetch for one ticker, delete its file.

Measured on AAPL.US with the real CLI: the first search took 29 seconds and 65 CLI calls. The same search after restarting the server took 0.2 seconds and 1 CLI call (the SEC-rate-limited insider-trades call, retried by design), and all five panels were identical.

Limits:

- The data can be up to 30 minutes old, so the price is not live. Delete the ticker's file (or set `DISK_CACHE_MINUTES` lower) when you need a current quote.
- A data call that returns a degraded result without failing (for example a statement section that came back empty) is saved as it came, and stays until the file expires.
- Searches are cached per ticker, not per set of option controls: changing the expiry window reuses everything the file holds and fetches only what is new.

---

## Skills used

The skills live in [.agents/skills/](.agents/skills/) (locked in [skills-lock.json](skills-lock.json)).

| Skill | Role in the app |
|---|---|
| [longbridge](.agents/skills/longbridge/SKILL.md) | Data layer. CLI/SDK commands for quotes, K-lines, financial statements, valuation rank, analyst estimates, news, filings, insider trades and 13F holders. |
| [longbridge-value-investing](.agents/skills/longbridge-value-investing/SKILL.md) | Core scoring. Buffett five-dimension moat analysis and Graham 100-point NCAV analysis. These supply most of the bull and bear arguments. |
| [longbridge-quant](.agents/skills/longbridge-quant/SKILL.md) | Quant overlay. Volatility regime, seasonality, multi-factor z-scores, statistical checks. |
| [longbridge-derivatives](.agents/skills/longbridge-derivatives/SKILL.md) | Option trade scanner. Option chain and quote discovery, the strategy matrix (market view x IV level), IV vs HV and skew, payoff and Greeks, calendar and diagonal spreads. See [Option trade scanner](#option-trade-scanner). |

### How each analysis maps to the bull and bear case

- **Buffett moat analyzer** ([reference](.agents/skills/longbridge-value-investing/references/buffett-moat-analyzer.md)). Scores five dimensions (business/moat, financial health, management, valuation, long-term visibility) from 1 to 5 stars. Dimensions at 4 stars or more feed the **bull** case. Dimensions at 2 stars or less feed the **bear** case. Quality and price stay separate scores, so the app can say "great business, wait for price".
- **Graham stock analysis** ([reference](.agents/skills/longbridge-value-investing/references/graham-stock-analysis.md)). Gives a 100-point static score plus dynamic adjustments and value-trap rules (2 of 5 trips means "value trap"). A high score with a stable NCAV is bullish. A shrinking NCAV, persistent negative operating cash flow or insider selling is bearish.
- **Quant** ([skill](.agents/skills/longbridge-quant/SKILL.md)). Volatility percentile, momentum and factor z-scores add the market-behaviour side of each case.
- **Derivatives** ([skill](.agents/skills/longbridge-derivatives/SKILL.md)). Does not change the bull and bear lists. It runs the other way: the bull/bear view, the trend and the flow label choose which option structures are built (bullish view leads to call spreads or bull put spreads, and so on).

### Data pulls (Longbridge CLI, `--format json`)

These are the commands the app runs, checked against the installed CLI (v0.30.0) with `--help`. Run `longbridge <command> --help` or `longbridge <command> --schema` (shows response fields, works without login) to confirm flags, because they change between versions.

```bash
longbridge quote AAPL.US --format json
longbridge calc-index AAPL.US --fields pe,pb,dps_rate,mktcap --format json
longbridge company AAPL.US --format json
longbridge financial-statement AAPL.US --kind BS --report af --format json   # also IS, CF, and --report qf
longbridge institution-rating AAPL.US --format json                          # Buy/Hold/Sell distribution + target price
longbridge forecast-eps AAPL.US --format json                                 # EPS consensus
longbridge kline AAPL.US --period day --count 1500 --format json
longbridge dividend AAPL.US --format json
longbridge shareholder AAPL.US --count 10 --format json                       # institutional holders
longbridge insider-trades AAPL.US --count 40 --format json                    # US only
longbridge capital AAPL.US --flow --format json                               # net flow by order size
longbridge short-positions AAPL.US --count 1 --format json                    # US and HK only
longbridge market-temp US --format json
longbridge news AAPL.US --count 20 --format json
longbridge filing AAPL.US --count 8 --format json
longbridge topic AAPL.US --count 20 --format json
```

**Use `financial-statement`, not `financial-report`.** On US accounts `financial-report` ignores `--kind` and returns only a summary, so it cannot supply the full income statement, balance sheet and cash flow the scorecards need.

Use WebSearch (or another web source) only for gaps Longbridge cannot fill, such as industry outlook, management track record or regulatory news. Tag each such figure `[Source: WebSearch, publisher, date, url]` so it never mixes silently with Longbridge data.

---

## Architecture

```
Browser (HTML + JS)
      │  GET /api/analyze?symbol=AAPL.US
      ▼
FastAPI backend (Python)
  ├─ data.py        runs longbridge CLI (or Python SDK), parses JSON, caches results in memory and, per ticker, on disk
  ├─ snapshot.py    per-ticker cache files in data/: TICKER-timestamp.json, reused for 30 minutes
  ├─ reconcile.py   cross-statement checks (IS↔BS↔CF, market cap, period alignment)
  ├─ buffett.py     five-dimension moat scoring
  ├─ graham.py      NCAV + static/dynamic scoring + value-trap rules
  ├─ quant.py       volatility regime, momentum, factor z-scores
  ├─ metrics.py     P/E TTM, PEG, FCF growth, Debt/EBITDA, ratings, op. margin, ROIC, ROE, NI growth + extras
  ├─ chart.py       EMA 20/50/200, volume profile (POC, VAH, VAL), trend signals
  ├─ flows.py       13F and insider activity, capital flow, OBV/A-D/CMF, accumulation/distribution read
  ├─ news.py        latest news (10, expandable to 25), dedupe, filings, market mood facts
  ├─ quality.py     Piotroski, Altman, Beneish, Sloan, Magic Formula, ROIC vs. WACC
  ├─ valuation.py   DCF with growth fade, reverse DCF, earnings power value, Graham number
  ├─ options.py     option chain and quote parsing, OCC symbols, Black-Scholes price and Greeks, IV vs HV, term structure, skew
  ├─ strategies.py  strategy matrix: builds spreads, condors, straddles, calendars from live strikes; payoff, breakevens, model POP
  ├─ option_scan.py market view from the analysis, filters and flags, multi-symbol scan
  └─ thesis.py      builds bull/bear lists from the scores, optionally asks Gemini to write the narrative
```

### Reconciliation gate

Before scoring, the backend checks that the numbers agree with each other (tolerances from the skill references):

| Check | Tolerance |
|---|---|
| Net income vs. change in retained earnings less dividends | ±3% |
| Net income plus non-cash items vs. operating cash flow | ±5% |
| Cash-flow change in cash vs. balance-sheet change in cash | ±1% |
| Current assets and liabilities sums | ±2% |
| Shares × price vs. market cap | ±2% |
| All statements from the same fiscal period | exact |

If a check fails, the app **does not score**. It shows which figure failed and by how much. A pass shows as a single summary line in the appendix.

**Tuned against real filings (checked on AAPL, MSFT, 700.HK, 9988.HK, JPM).** Applied literally, the roll-forward checks stopped almost every real company, because stock compensation, share-settlement taxes, minority interests, FX translation and restricted cash legitimately move those figures. So:

| Check | Behaviour |
|---|---|
| Current-asset sum | Pass within 2%, warning up to 10%, fail beyond |
| Liabilities sum (current + non-current) | Pass within 2%, fail beyond |
| Change in cash (cash flow vs balance sheet) | Pass within 1%, warning up to 10%, fail beyond |
| Net income vs change in retained earnings + dividends (+ buybacks) | Pass within 3%; otherwise a warning only. **Informational, never stops scoring** (deviation from the skill's 3% hard stop) |
| Shares x price vs market cap | Skipped: the statements carry no share count |
| Net income + non-cash items vs operating cash flow | Skipped: working-capital detail is not captured |

Gross errors (such as a mis-parsed statement, which showed up as a 39% gap during testing) still stop scoring.

### Sector handling

- **Banks, insurers, REITs.** NCAV does not apply. The Graham panel is hidden, and the Buffett financial-health dimension uses ROA, NIM, NPL and capital adequacy.
- **Pre-revenue or heavily loss-making companies.** The Buffett and Graham frameworks need a track record, so the app shows a notice and a lighter fundamentals and news view.
- **Listed under 3 years.** Earnings-stability and capital-allocation scores are pro-rated and flagged.

---

## Setup

### Prerequisites

- Python 3.11+
- A Longbridge account (free to open)
- Longbridge CLI
- For the option scanner: US options market access on your Longbridge account (the skill notes that US options need US market access). Check with `longbridge option chain AAPL.US`.
- Optional: a Google Gemini API key (`GEMINI_API_KEY`) for the written narrative. Without it, the app falls back to templated text built from the scores.

### 1. Install the Longbridge CLI

Windows (the installer is bundled with the skill; review it first):

```powershell
powershell -ExecutionPolicy Bypass -File .agents\skills\longbridge\scripts\install-cli.ps1
```

macOS / Linux:

```bash
sh .agents/skills/longbridge/scripts/install-cli.sh
```

Then sign in and verify:

```bash
longbridge auth login
longbridge check
longbridge quote AAPL.US
```

### 2. Install the app

```bash
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

If you prefer the Python SDK over the CLI: `pip install longbridge` (see [Python SDK overview](.agents/skills/longbridge/references/python-sdk/overview.md)).

### 3. Configure

Create `.env` in the project root:

```
GEMINI_API_KEY=...                # optional; GEMINI_MODEL overrides the model (default gemini-2.5-flash)
DATA_MODE=cli                     # cli | demo (synthetic data, no login needed)
CACHE_TTL_SECONDS=900             # in-memory cache for data calls
DISK_CACHE_MINUTES=30             # per-ticker cache files in data/ (see Data cache); 0 = off
```

### 4. Run

```bash
uvicorn app.main:app --reload
```

On Windows you can instead double-click [start.bat](start.bat) (installs dependencies on first run, starts the server in a minimised window and opens the browser) and [stop.bat](stop.bat) (stops whatever is listening on the port). Both use port 8000; set the `PORT` environment variable to change it.

Open http://localhost:8000, enter a ticker and press **Analyze**. You can also open `http://localhost:8000/?symbol=AAPL.US`.

**Try it without a Longbridge login:** set `DATA_MODE=demo` in `.env`. The app then runs on deterministic synthetic data and shows a "DEMO DATA" banner. Use it to see the UI, not to judge a stock.

### 5. Check the CLI field mapping (first run with real data)

The Longbridge CLI's exact JSON layout could not be checked while building the app (it needs a logged-in account). The data layer in `app/normalize.py` and `app/data.py` is written to tolerate different layouts, but you should confirm it on your first real run:

```bash
python -m app.probe AAPL.US     # prints the raw JSON of each CLI call
```

If a metric shows `n/a` when it should have a value, add the CLI's field label to `ALIASES` in `app/normalize.py`. The "Some data could not be fetched" banner and the Data sources table show which calls failed. Calls that fail (for example `insider-trades` outside the US, or `short-positions` outside US and HK) fail soft: the matching panel shows `n/a` or a warning. `longbridge <command> --schema` lists a command's response fields without needing a login.

### 6. Tests

```bash
python -m pytest -q
```

The tests use demo mode and cover the EMA, volume profile, growth rules, flow indicators, news de-duplication, field-mapping layouts, the reconciliation gate (including a failing case), the API endpoints and input validation. `tests/test_cache.py` covers the per-ticker cache: file naming, the freshness boundary, replacing and renaming an old file, merging calls the file lacks, retrying failed calls, unreadable files, an unwritable folder, requests arriving together, and that a cached page equals the live one.

---

## Suggested project layout

```
OptionScanner/
├── README.MD
├── skills-lock.json
├── .agents/skills/          # longbridge, longbridge-value-investing, longbridge-quant, longbridge-derivatives
├── app/
│   ├── main.py              # FastAPI app and /api/analyze
│   ├── data.py
│   ├── reconcile.py
│   ├── buffett.py
│   ├── graham.py
│   ├── quant.py
│   ├── metrics.py           # key and additional metrics
│   ├── chart.py             # EMAs, volume profile, chart signals
│   ├── flows.py             # institutional buying/selling indicators
│   ├── news.py              # latest news and market mood
│   ├── quality.py           # published quality scorecards
│   ├── valuation.py         # app-built fair value estimate
│   ├── options.py           # chain/quote parsing, OCC symbols, Black-Scholes, IV metrics
│   ├── strategies.py        # strategy builders, payoff, breakevens
│   ├── option_scan.py       # view, filters, risk flags, multi-symbol scan
│   ├── snapshot.py          # per-ticker disk cache
│   ├── config.py            # bull/bear thresholds
│   └── thesis.py
├── data/                    # everything in here is git-ignored
│   ├── iv_history.json      # daily ATM IV snapshots, builds the IV percentile over time
│   └── AAPL.US-20261020T143210+0800.json   # one cache file per ticker searched in the last 30 minutes
├── tests/                   # test_app.py, test_options.py, test_valuation.py, test_cache.py
└── web/
    ├── index.html           # single page: search box, cards, bull/bear columns
    ├── app.js
    └── styles.css
```

---

## API

The option endpoints (`/api/options`, `/api/options/payoff`, `/api/options/scan`) are described in [Option trade scanner](#option-trade-scanner).

When the data cache is on (live mode), every JSON response also carries `cache`, which says where its data came from:

```jsonc
"cache": { "symbol": "AAPL.US", "from_cache": true, "as_of": "2026-10-20T06:32:10+00:00",
           "age_seconds": 720, "ttl_seconds": 1800, "live_calls": 0 }
```

`from_cache` is true when the cache file supplied any of the response's data, `as_of` is when that data was fetched (UTC), and `live_calls` counts the calls the file did not hold, which were fetched and added to it. The page reads this only from the four requests a search makes (analyze, chart, flows, news): later requests such as a chart range or **Show more** read the file those just wrote, and would make a live search look like a cached one.

`GET /api/analyze?symbol=AAPL.US`

```jsonc
{
  "symbol": "AAPL.US",
  "snapshot": { "price": 0, "market_cap": 0, "pe": 0, "pb": 0 },
  "metrics": {
    "pe_ttm": 0,
    "peg": { "value": 0, "growth_basis": "forward_consensus | historical_3y" },
    "fcf_growth": { "yoy": 0, "cagr_3y": 0 },
    "debt_to_ebitda": { "gross": 0, "net": 0 },
    "analyst_ratings": { "buy": 0, "hold": 0, "sell": 0, "consensus": "", "target_mean": 0, "upside_pct": 0 },
    "operating_margin": { "ttm": 0, "history": [] },
    "roic": 0,
    "roe": { "latest": 0, "history": [] },
    "net_income_growth": { "yoy": 0, "cagr_3y": 0, "cagr_5y": 0 },
    "extra": {
      "revenue_growth": {}, "eps_revisions": {}, "gross_margin": 0, "fcf_margin": 0, "cash_conversion": 0,
      "ev_ebitda": 0, "fcf_yield": 0, "interest_coverage": 0, "current_ratio": 0,
      "altman_z": 0, "piotroski_f": 0, "dividend": {}, "share_count_change_3y": 0,
      "insider_net": {}, "short_interest": {}, "beta": 0, "relative_strength": {}
    }
  },
  "reconciliation": { "status": "pass", "residuals": [] },
  "buffett": { "moat": "wide", "stars": { "moat": 5, "financials": 5, "management": 4, "valuation": 2, "visibility": 4 }, "holding_period": "5y+" },
  "graham": { "static_score": 0, "adjusted_score": 0, "value_trap": false, "buy_line": 0 },
  "quant": { "hv20_percentile": 0, "momentum_60d": 0, "factor_z": {} },
  "bull": [ { "title": "", "evidence": "", "source": "" } ],
  "bear": [ { "title": "", "evidence": "", "source": "" } ],
  "verdict": { "tag": "great business, wait for price", "summary": "" },
  "sources": [ { "field": "", "source": "", "fetched_at": "", "period": "" } ]
}
```

---

## Notes and limits

- Longbridge covers US, HK, CN (SH/SZ), SG and crypto, but coverage varies. Tested live: US and HK stocks work end to end. **A-shares (SH/SZ):** the statements endpoint returned `openapi error 2601400` for 600519.SH, so those symbols show the chart, flows and news but no metrics or scorecards. SG (D05.SG) returned no price in testing.
- The statements carry no EPS, share count or interest expense for some companies, so P/E uses the quoted TTM P/E, share-based items show n/a (or use market cap / price for the latest period, with a warning), and PEG growth comes from the consensus EPS estimate against trailing EPS.
- Debt includes short-term borrowings, the current portion of long-term debt, long-term debt and long-term leases.
- The kline API returns at most 1000 candles per request, so longer history is fetched in date-range chunks, forward-adjusted.
- The option scanner works from last-trade prices (the CLI gives no bid or ask), and Greeks and probabilities are model outputs. See its Limits section.
- Insider trades come from the 100 most recent SEC Form 4 filings. If those do not reach back 12 months, the 12-month figure is labelled partial with the date the data starts from (with only 40 filings, AVGO's 12-month insider selling was understated by about $120M). Only open-market buys and sells count; grants, tax withholding, gifts and exercises are ignored. SEC EDGAR rate-limits repeated requests (HTTP 429), in which case the insider signal shows as missing.
- Magic Formula return on capital is shown as ">500%" when the capital base is tiny (asset-light companies such as AVGO), since the ratio is not meaningful there.
- The page requests several panels at once, so identical provider calls are shared (one fetch, however many panels ask), and a failed call is remembered for 45 seconds instead of being retried by every panel. Without this, a rate-limited source such as SEC EDGAR made the page 3 to 4 times slower (36 s instead of 10 s for the first panels on AVGO) and the Options section, which waits for the other panels, appeared last. The Options section header now shows immediately and the scan starts when the other panels finish. SEC 429 errors are not retried, since SEC throttling lasts much longer than a retry delay.
- Browser caching: the page is served with `Cache-Control: no-store`, the script and stylesheet URLs carry a version that changes whenever a UI file changes, and static files are sent with `no-cache` (always revalidated). Before this, Chrome cached `app.js` and `index.html` heuristically and could run an old script against a new page, which made panels vanish. The footer shows a "UI build" number; if two machines show different numbers, one is running stale files. The script also creates any section its page is missing instead of aborting.
- Missing statement lines: Longbridge omits empty line items. On a normal balance sheet (one with a current-assets section) a missing debt line is read as zero debt, so debt-free companies such as ANET show 0.0x Debt/EBITDA and net cash instead of n/a; banks and insurers keep n/a. Any other missing figure shows n/a and never stops the analysis (a test removes every statement field in turn and checks the analysis still completes). Beneish and the other scorecards report n/a when an input is absent.
- Piotroski "leverage down" counts a company with zero long-term debt in both years as met, since there is no leverage to reduce; a strict reading would fail every debt-free company.
- ROIC and Magic Formula return on capital are shown as ">100%" and ">500%" when invested capital is mostly cash (net-cash companies), because the exact figure is not meaningful.
- Share-class tickers such as BRK.B.US are accepted; their option contracts drop the dot (`BRKB261120C500000.US`).
- Unexpected server errors return a message naming the error, not a bare "Request failed (500)".
- Beneish M-score flags show their two biggest drivers (for example sales growth and accruals) and the thesis calls them an "earnings-quality screen", not manipulation: fast-growing companies and companies with non-cash investment gains (NVDA is one) often trip it. The metrics add **non-operating income as a share of pre-tax income** (TTM), and the net-income-growth point says so when it is 10% or more, because investment gains and interest flatter net income, ROE and net income growth.
- Checked against public filings: for NVDA.US, Longbridge's fiscal 2026 revenue ($215.938B), operating income ($130.387B), net income ($120.067B) and the latest quarter's revenue ($96.221B) and net income ($59.688B) match NVIDIA's SEC-filed results exactly.
- **Piotroski F-Score basis.** The page scores the **latest quarter against the same quarter a year earlier** when it can (all nine tests, with period-end assets in both quarters), and shows the **fiscal-year** score beside it. A fiscal year can be up to three quarters old, which misleads for a company whose business changed fast: NVDA's last fiscal year included a one-off first-quarter charge, so its fiscal-year score was 3 of 8 while the latest quarter scores 5 of 9. GuruFocus scores trailing twelve months against the year before (7 for NVDA); that needs eight quarters of statements and Longbridge returns five, so it cannot be reproduced exactly. Seven of the nine individual tests agree with GuruFocus for NVDA. The two that differ, ROA and asset turnover, are scaled by assets from a year earlier in Piotroski's paper and in GuruFocus, which favours companies whose asset base has just grown fast; the app cannot do that for the year-ago quarter because balance sheets only go back five quarters.
- **Share counts** are derived from the consensus table (a quarter's net income divided by its EPS; a fiscal year is the mean of its quarters, at least three). This gives the Piotroski "fewer shares" test, the 3-year share count change and per-share NCAV a real input instead of n/a or a market-cap estimate. It is diluted GAAP shares, so it moves with dilution and buybacks.
- **Which period each scorecard uses** (shown on the page): Piotroski, the latest quarter against the same quarter a year earlier (fiscal-year score alongside); Altman, the latest quarter's balance sheet with trailing-twelve-month EBIT and sales; Sloan accruals, trailing twelve months over the average of the latest and year-ago assets; Magic Formula and Graham debt cover, the latest balance sheet; Beneish, the last fiscal year against the one before (its indices are calibrated on annual data). Before this, Altman, Sloan and Magic Formula used the January fiscal-year balance sheet against today's price, which mixed dates: for NVDA, Altman read 68 instead of 39, Sloan 10.9% instead of 25.4% (inside versus beyond the 25% red-flag line) and Magic Formula return on capital 434% instead of 175%.
- **Unavailable is not the same as none.** If the insider (Form 4) feed fails, for example from an SEC rate limit, the insider figures show n/a, the Buffett "not net sellers" test shows n/a, and Graham says its insider adjustment was not applied. Before this, a failed lookup was read as "no insider selling", which removed Graham's penalty and passed the Buffett test; a genuinely empty feed (no trades) is still a real zero.
- Buffett star averages round half up (4.5 stars is 5); Python's default rounding sent 4.5 to 4 and 3.5 to 4.
- The CLI is limited to 10 API calls per second. The backend batches calls and caches them: in memory for 15 minutes, and per ticker on disk for 30 minutes (see [Data cache](#data-cache-per-ticker)).
- The Graham and Buffett frameworks suit long-horizon investors. For a holding period under 3 years, the app shows a warning that these are not the right lenses.
- The verdict wording is neutral and factual (for example, "meets Graham NCAV criteria"). It is not a buy or sell recommendation.

**Disclaimer:** For reference only. Not investment advice. Make investment decisions independently, based on your own risk tolerance.
