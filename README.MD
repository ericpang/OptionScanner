# Stock Deep-Dive: Bull & Bear Case

A simple web app. Type a ticker (e.g. `AAPL.US`, `700.HK`, `600519.SH`) and get a deep analysis of the stock, ending in a **bull case**, a **bear case** and a **balanced verdict**. An **option trade scanner** (see [below](#option-trade-scanner)) then turns that analysis into candidate option structures for the stock, and can scan a list of symbols.

All market data comes from [Longbridge](https://open.longbridge.com). The analysis methods come from the skills in [.agents/skills/](.agents/skills/).

> Output is for reference only. It is not investment advice.

---

## What it does

1. The user enters a symbol in `<CODE>.<MARKET>` format.
2. The backend pulls quotes, financials, valuation, insider and news data from Longbridge.
3. It runs three analysis lenses (below) and checks that the financial statements agree with each other before it scores anything.
4. It builds the **bull case** and **bear case**, each with cited evidence, and shows them side by side under a verdict card.
5. Every figure links back to the **Data sources** table (a box at the bottom of the page) listing its source, fetch time and period.
6. The **Options** tab reads the option chain, judges whether implied volatility is rich or cheap, and builds candidate option trades that fit the stock's bull or bear view, with payoff, breakevens, Greeks and risk flags (see [Option trade scanner](#option-trade-scanner)).

### Pages / UI

The page is a header and **seven tabs**, one for each group of related analyses. A search opens on **Overview**, which gives the answer first; the other tabs hold the detail behind it. The tab you leave open stays open for the next search, so two stocks can be compared on the same tab. The left and right arrow keys move along the tabs, and Home and End go to the first and the last.

| Where | Contents |
|---|---|
| Above the tabs (always visible) | The status line. A banner when the data is synthetic (demo mode). A banner when some data could not be fetched or had to be approximated; it sits here, not at the bottom of the page, so a caveat is seen whichever tab is open. The cache note, only when the page was built from the data cache: how old the fundamentals and the market data are, and when each will be fetched again (see [Data cache](#data-cache-per-ticker)). The header: symbol, price, market cap, PE/PB, 52-week range. And, only when it happens, the reconciliation failure (see [Reconciliation gate](#reconciliation-gate)), after which the tabs that would have held scores say so instead of being blank |
| **Overview** | The **verdict** first: the tag from the Buffett framework (for example "Great business, wait for price"), one line counting the supporting and cautionary points and the scorecards that pass, and the written narrative when `GEMINI_API_KEY` is set. Under it the **bull case** and the **bear case** side by side. The bear case is one list of every concern the data shows, merged from the rule-based bear points and the broader critic checks (valuation, slowing growth, leverage and liquidity, earnings quality, dilution, insider selling, price action), grouped by theme, each with the figures behind it and a high / medium / low rating (`app/risks.py`; thresholds are this app's own). Risks that only the company's filings can show, such as customer concentration, are listed as not measured, with links to the latest annual and quarterly filings. With `SEC_USER_AGENT` set in `.env`, the app also reads the latest 10-K and newer 10-Q from SEC EDGAR (`app/sec.py`) and shows, verbatim and unscored, the company's own summary of risk factors and any sentence that gives a customer's share of revenue; it is a text search and can miss or mis-catch, so confirm in the filing |
| **Fundamentals** | The **quality scorecard**: Piotroski F, Altman Z, Beneish M, Sloan accruals, Magic Formula, Graham, Buffett and ROIC vs. WACC, each with its published threshold (see [Stock quality scorecard](#stock-quality-scorecard)); the Buffett five dimensions and the Graham cigar-butt breakdown behind two of its rows are folded away under it. Then the **key metrics** and the **additional metrics** (see [Key metrics](#key-metrics)), and the **peer group** those metrics are compared with (see [Industry comparison](#industry-comparison)) |
| **Valuation** | The **fair value estimate**: an app-built DCF, earnings power value and Graham number, a sensitivity grid and a reverse DCF; explicitly not GF Value (see [Fair value estimate](#fair-value-estimate-not-gf-value)). Under it the **reference price levels** (the analyst target, the volume profile's value area, Graham's buy line, the DCF value and the 52-week range, each against the price), and a box for Morningstar's own estimate, which you type in |
| **Two-stage filter** | The Reset & Invest checklist: three pass/fail checks, then eight scored questions, with a source and period beside every figure, what to verify first, and a form for the figures only you can supply (see [Two-stage stock filter](#two-stage-stock-filter)) |
| **Price & flows** | The **price chart**: daily candles, EMA 20/50/200, an OBV pane, volume and the volume profile with POC and value area (see [Price chart](#price-chart-daily-candles-ema-2050200-and-volume-profile)). Under it the two **technical insights**: the anchored VWAP read of the latest closed session, one of six price-and-volume conclusions, colour-coded, over a configurable number of days (see [Anchored VWAP](#anchored-vwap-avwap-and-the-volume-matrix)), and the On-Balance Volume read, which matches price, volume and OBV direction to a 27-row table and adds a price/OBV divergence test (see [On-Balance Volume](#on-balance-volume-obv-insights)). Then **institutional buying and selling**: 13F holder changes, insider trades, large-order capital flow, CMF, and an Accumulation / Neutral / Distribution read (see [Institutional buying and selling](#institutional-buying-and-selling)) |
| **Options** | IV vs HV, term structure and skew, put/call volume, and candidate option trades with payoff, breakevens, Greeks and risk flags (see [Option trade scanner](#option-trade-scanner); a multi-symbol **Scan** page is planned there) |
| **News** | The latest 10 news items (expandable to 25), plus analyst consensus, market temperature, community activity and short interest, with no automated sentiment scoring (see [Latest news and market mood](#latest-news-and-market-mood)) |
| Bottom of the page | **Data sources and reconciliation**, a box that starts closed: the source, fetch time and period for every figure, and the result of each reconciliation check |

The tabs are laid out in `web/index.html`; `TABS` and `SECTION_TAB` in `web/app.js` say which section sits in which tab, and `tests/test_layout.py` checks that the two agree. Each section is still filled by its own request, in the order the answers arrive, whichever tab is showing.

---

## Key metrics

The **Key Metrics** panel is in the Fundamentals tab, under the quality scorecard. Each metric shows its value, a 5-year trend where it applies, the peer median, and a green/amber/red tag. Each metric is also tied to the bull or bear case.

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
| Profitability | **Free cash flow to equity (FCFE) and FCFE margin** | Operating CF − capex + debt issued − debt repaid, TTM: the cash left for shareholders once debt is paid down (see [Free cash flow to equity](#free-cash-flow-to-equity)). Margin = FCFE ÷ revenue. Not shown for banks, insurers and REITs | CF, IS |
| Valuation | **EV/EBITDA** | (Market cap + debt − cash) ÷ TTM EBITDA | `calc-index`, BS, IS |
| Valuation | **P/S and P/B** | Price to sales and price to book, each with its percentile vs. 10-year history | `calc-index`, `valuation` |
| Valuation | **FCF yield** | TTM FCF ÷ market cap | CF, `quote` |
| Valuation | **FCFE yield** | TTM FCFE ÷ market cap | CF, `quote` |
| Valuation | **Industry median and percentile** | P/E, P/B and P/S against the median of the companies in the same industry: how many times the median the stock is, and its percentile among them (see [Industry comparison](#industry-comparison)) | `industry-valuation dist` |
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

### Free cash flow to equity

**Free cash flow to equity (FCFE)** is the cash left for shareholders after the business has paid for its investment *and* for its debt: **operating cash flow − capex + debt issued − debt repaid**. Free cash flow stops before debt; FCFE counts it. A company that pays debt down has less FCFE than FCF, because the repayment is cash that did not reach shareholders, and one that borrows has more. Three cards show it, beside the FCF ones: **Free cash flow to equity** (the amount, the sum that makes it up, the gross debt flows and the fiscal-year history), **FCFE margin** (FCFE ÷ revenue) and **FCFE yield** (FCFE ÷ market cap). The margin and the yield are set against the peer median like the other ratios.

For example, on 2026-10-22, AAPL paid down more debt than it raised, so its trailing-twelve-month FCFE is 119.4B against a free cash flow of 136.7B (operating CF 146.7B − capex 10.0B − 17.3B of net debt repaid). NVDA borrowed 24.9B in its latest quarter, so its FCFE of 151.9B is above its free cash flow of 127.0B.

- **Source.** The cash flow statement's `total_debt_issued` and `total_debt_repaid` (`financial-statement --kind CF`). Only the totals are read: the short- and long-term lines under them are sometimes empty while the total is filled (Apple's quarters), so adding those up would miss debt.
- **Period.** The last four quarters summed, or the last fiscal year when any of the four lacks a figure; the card says which. Every part (operating CF, capex, debt issued and repaid, and revenue for the margin) is summed over the same periods, so the sum on the card adds up exactly.
- **An empty line is a zero.** Longbridge leaves out a debt line when there was none, so on a cash flow statement a missing line counts as no debt issued or repaid. Without that, one quiet quarter (most quarters, for a company that borrows rarely) would stop the four from being summed. A period with no cash flow statement at all stays unknown.
- **Signs are kept as given.** Some Hong Kong quarters look to be derived by subtraction from half-year and nine-month figures, and such a quarter can come back with negative debt issued and positive debt repaid (Tencent's September 2025 quarter: −192.8B and +136.0B HKD). Over the year the net still comes out right: Tencent's four quarters to June 2026 net to +73B HKD of borrowing, against a +93B change in the debt on its balance sheet (on 20 companies across the US, Hong Kong and mainland China, the net differed from the balance sheet's change in debt by less than 20% of the debt in every case; leases, currency moves and acquisitions explain the rest). So the figures are summed as they come. Forcing every repayment negative, as is done for capex, turned that +73B into −199B and a free cash flow of 187B into an FCFE loss.
- **Lumpy by nature.** One large bond issue or repayment moves FCFE much more than it moves FCF, so read it with the history on the card and with FCF beside it. It is shown, not scored: it feeds no bull or bear point.
- **Not for banks, insurers and REITs,** whose debt is part of the business rather than a way of funding it (the companies the fair-value estimate skips too). The cards show n/a with the reason. The industry name is matched in English and in Chinese (`FIN_WORDS` in [app/quality.py](app/quality.py)): Longbridge returns it in Chinese on some accounts (半导体厂商, 多元化银行), and a check that read only English words never fired for them, which also let banks through to the Altman score and the DCF.
- **Not the DCF's input.** The fair-value DCF discounts operating cash flow less capex, not this figure: a year of debt paydown or borrowing is not something to project forever.

### Industry comparison

The metric cards compare the company with its industry in two ways. The **valuation multiples** (P/E, P/S, P/B) are set against the **industry median**, which Longbridge publishes. Everything else (margins, returns, growth, leverage) is set against the median of the company's **peers**, which the app measures itself, because Longbridge publishes nothing for those. Both kinds of line are coloured by whether the company is **better or worse** than the median: **blue** when better, **red** when worse, and grey when it is equal or there is nothing to compare. The arrow (▲ above the median, ▼ below) takes the line's colour, so a ▼ can be blue (less debt, a cheaper multiple). Which way is better is the app's own call, set for each figure in `HIGHER_IS_BETTER` in [app/benchmarks.py](app/benchmarks.py) and in line with the cards' green, amber and red tags and the bull and bear table below: a **lower** figure is better for P/E, P/S, P/B, EV / EBITDA, Debt / EBITDA and net debt / equity, and a **higher** one for everything else (margins, returns, growth, cash conversion, FCF and FCFE yield, interest coverage, current and quick ratio). That is a simplification: a high ROE can come from debt and a low multiple from trouble, and any difference counts, however small. The colours are `--accent` (blue) and `--bad` (red) in `web/styles.css`, lighter in dark mode, and hovering a line says which way round it was judged.

**Valuation multiples: the industry median.** The P/E, P/S and P/B cards each carry two extra lines: the industry median, and where the company stands against it. For example, `Industry median 20.7 (20 companies)` and `▲ 1.9x the median, percentile 74`.

- **Source.** One call per stock, `longbridge industry-valuation dist SYMBOL`. For each multiple it returns the industry's median, low and high, the company's rank and how many companies were ranked. It is market data (the multiples move with the price), so the cache keeps it for `LIVE_CACHE_MINUTES`, not for the file's whole life.
- **Median, not average.** Only companies with a positive value are counted, so loss-makers do not drag a P/E median toward zero, and a few extreme multiples (negative equity, near-zero earnings) cannot wreck it the way they wreck an average.
- **The ratio uses the card's own figure.** "1.9x the median" is the number on the card divided by the median beside it. The percentile is the share of the *other* companies the stock is above, from Longbridge's rank of its own figure for the company. It is shown only when that figure is within 10% of the card's (`BENCH_VALUE_TOLERANCE` in `app/config.py`), because a rank of a different number (a P/E on last year's EPS, say) does not describe this one.
- **When there is no comparison.** A multiple that is not positive (a loss, negative equity) shows the median and "no comparison without a positive figure". A multiple beyond the industry's range shows "above (or below) the industry's range" instead of a percentile. A median over fewer than 5 companies (`BENCH_MIN_COMPANIES`) is marked "thin sample". If the call fails (an ETF, a stock Longbridge does not place in an industry, a rate limit that outlasts the retries), the cards show no industry lines and the "Some data could not be fetched" banner says why.
- **Lower is better.** A multiple below the industry median counts as better (cheaper) and is blue; above it, worse and red, and one above the industry's whole range is worse too. Equal to the median as it reads (1.0x), or not positive: grey.
- **Only as like-for-like as the group.** The industry is Longbridge's own grouping. AAPL's is "Hardware, Storage & Peripherals": 43 companies, including Dell, Seagate and Kodak, and quantum-computing start-ups worth a few million dollars.
- **Not `valuation`.** That command also prints an `industry_median`, but it counts loss-makers (0.78 for AAPL's P/E, where `industry-valuation dist` gives 20.7), so it is not used for the median. (Its list of the industry's companies is used for the peers, below.)

**Margins, returns, growth and leverage: the peer median.** Nineteen cards (operating margin, ROIC, ROE, Debt / EBITDA, FCF growth, net income growth, revenue growth, gross, net, FCF and FCFE margin, cash conversion, EV / EBITDA, FCF yield, FCFE yield, interest coverage, current and quick ratio, net debt / equity) carry two more lines, for example `Peer median 9.7% (10 peers)` and `▲ 37.1 pts above the median, above 10 of 10`. A **Peer group** panel under the metrics lists the peers and, in a table, every figure for every peer, so a median can be checked.

- **Who the peers are.** The 10 companies in the stock's industry that are closest to it in market cap. `longbridge valuation SYMBOL` lists the whole industry with market caps (43 companies for AAPL, the stock itself included). Left out are the stock itself, its other lines (Hong Kong lists an RMB counter beside the HKD one, so 80700.HK is Tencent, not a peer of 700.HK; many companies have share classes) and any company already chosen under another line. A candidate that cannot be used is replaced by the next closest, up to 6 spares.
- **How they are measured.** Each peer's statements go through the same code as the stock's (`metrics.compute`), so a peer's ROIC is defined exactly like the stock's, over the same periods: trailing twelve months for margins and coverage, the last fiscal year for ROIC, ROE, growth and gross margin. A peer whose statements fail the reconciliation checks is left out, as the stock would be (DELL, HPE and Baidu were, in testing). ROE and net debt / equity skip a peer whose equity is zero or negative (Dell's ROE reads -300%), and EV / EBITDA, FCF yield and FCFE yield skip a peer whose statements are in another currency than its shares (an ADR). A peer that is a bank, insurer or REIT has no FCFE and is skipped for the two FCFE figures.
- **The statistic.** The median of the peers that have the figure, and at least 3 must (`BENCH_MIN_PEERS`); a figure with fewer shows "Peer median n/a: only 2 peers have this figure". The tooltip gives the middle half of the peers. "Above 7 of 9" is how many peers the stock is above. The gap is in percentage points for percentages, in the figure's own unit for ratios, and "times the median" for EV / EBITDA. The arrow says above or below the median, and the colour better or worse (see above).
- **Cost and timing.** A peer takes 11 CLI calls, and `financial-statement` allows about 2 calls a second to the whole account (it answers a third with code 429002), so the first search of an industry takes about 40 seconds: 36 s for MSFT, 40 s for 700.HK and 47 s for AAPL and NVDA, which had peers to replace. It is its own request (`GET /api/peers`), started when the analysis is up, so nothing else on the page waits for it. The cards gain their peer lines, and the panel appears, when it finishes. Statement calls are spaced 0.55 s apart across the whole server (`CliProvider.STATEMENT_GAP`), which also removed the rate-limit retries the page's own statement calls were making (about one in four failed before).
- **Reuse.** Each peer's figures (not its raw statements) are kept in `data/peers.json` for 7 days (`PEER_CACHE_DAYS` in `.env`; 0 means never reused). They are kept by company, so any stock in the same industry reuses them, and a repeat search takes a few milliseconds. A peer left out because its statements fail the checks is remembered for just as long. A peer that could not be fetched (SK Hynix's ADR has no statements at all) is remembered for an hour (`PEER_RETRY_SECONDS`), so a repeat search does not spend seconds on it again, but only when at least 3 other peers worked: a down service or a logged-out CLI is never remembered. **Refresh data** measures the peers again.
- **When there is nothing to show.** If fewer than 3 peers can be used, or the industry cannot be listed, the panel says why and the cards show no peer lines. The stock's own statements must pass the reconciliation checks, so an ETF, a Singapore stock with no price, or a halted analysis shows no peers.
- **Not compared.** PEG (its growth is a forward consensus for some companies and a historical CAGR for others, so a median across peers would mix bases), analyst ratings, dividend yield (Longbridge returns no yield at all for a company that pays none, which would bias a median toward payers), share count, insiders, short interest and the price-based figures.
- **Nearest in size, not in business.** The peers are the closest companies by market cap in Longbridge's own industry grouping. AAPL's has quantum-computing start-ups and Kodak in it, and a mega-cap has few real peers. Periods follow each company's own latest figures, so fiscal years differ between peers.

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
- **Derived figures.** PEG, ROIC and Debt/EBITDA are calculated by the app, and the Data sources table marks them as derived with the formula and inputs used.

---

## Price chart: daily candles, EMA 20/50/200 and volume profile

The chart is the first thing in the **Price & flows** tab.

### What it shows

- **Daily candlesticks**, default range 1 year, with 6M / 1Y / 2Y / 5Y toggles.
- **EMA 20, 50 and 200** lines in three distinct colours, each toggleable from the legend.
- **Technical insights**, two panels under the chart: the latest closed session read against an anchored VWAP (see [Anchored VWAP](#anchored-vwap-avwap-and-the-volume-matrix)), and the On-Balance Volume read (see [On-Balance Volume](#on-balance-volume-obv-insights)). The chart itself draws no AVWAP or average-volume line; it does draw OBV.
- **OBV pane** between the candles and the volume bars: the OBV line and its 20-day moving average (dotted), on the same dates. OBV is shifted to start at 0 at the first bar shown, because its running total starts at the beginning of the history and the level means nothing; only its direction does.
- **Volume bars** in a lower pane, coloured by up or down day.
- **Volume profile**, a horizontal histogram on the right that shares the price axis with the candles. It marks:
  - **POC** (point of control): the price level with the most volume.
  - **VAH / VAL** (value area high and low): the range holding 70% of the volume. The value area is shaded.
  - Bars inside the value area are drawn darker than those outside.
- **Signals under the chart**: price vs. each EMA, whether EMA 20/50 and 50/200 have recently crossed (golden or death cross), and where the price sits relative to POC and the value area. These also feed the bull and bear case (for example, price above EMA 200 and above the value area is a bull point; price below EMA 200 and below VAL is a bear point). The On-Balance Volume read adds its own points (see [On-Balance Volume](#on-balance-volume-obv-insights)).

### How it is computed

- **Data:** `longbridge kline SYMBOL --period day --count N --format json`. The backend requests the chart range plus 250 extra bars of warm-up so the EMA 200 is already settled on the first visible day. Adjusted prices are used where the CLI supports it (check `longbridge kline --help`).
- **EMA:** `ema[t] = close[t] × k + ema[t-1] × (1 − k)` with `k = 2 / (n + 1)`, seeded with the simple average of the first `n` closes. Computed on the server in `app/chart.py`, not in the browser, so the numbers match the API.
- **Volume profile:** the price range of the visible window is split into 50 equal bins (configurable). Each daily bar's volume is spread evenly across the bins between its low and high. The bin with the largest total is the POC. The value area starts at the POC and expands to the larger neighbouring bin each step until it holds 70% of total volume.
- **Limit:** a profile built from daily bars is an approximation, because the real distribution within each day is unknown. Intraday bars would be more accurate (for example, `kline --period 5m` or `1h` where available), so `app/chart.py` accepts a finer-granularity input if you want to add it later. The chart footnote says "profile estimated from daily bars".

### Frontend

[Plotly.js](https://plotly.com/javascript/) is the recommended charting library. It supports candlesticks, line overlays and a horizontal bar histogram with a shared y-axis in one figure, with zoom and hover. Plotly's chart is drawn with two panels: candles with EMAs on the top left, volume on the bottom left, and the profile on the right sharing the price axis. TradingView's lightweight-charts also works for the candles and EMAs but has no built-in volume profile, so it would need a custom overlay.

The chart must work in light and dark themes, and the EMA colours must stay distinguishable (and not rely on colour alone, so the legend labels each line).

### API

`GET /api/chart?symbol=AAPL.US&range=1y&bins=50&days=20`

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
  },
  "avwap": { "days": 20, "conclusion": {} },   // see Anchored VWAP below
  "obv": { "days": 20, "conclusion": {}, "divergence": {}, "series": {} }   // see On-Balance Volume below
}
```

---

## Anchored VWAP (AVWAP) and the volume matrix

A **Technical insights** panel under the chart reads the latest closed session against an **anchored VWAP** as one of six conclusions, colour-coded so a result can be scanned at a glance. The number of days is configurable. The code is in [app/avwap.py](app/avwap.py) (the market clock is in [app/sessions.py](app/sessions.py)) and the tests in `tests/test_avwap.py` and `tests/test_sessions.py`. The chart draws no line for it: a dashed AVWAP line and a dashed average-volume line were tried and dropped as not useful, so the read is a handful of numbers, not a series.

### The AVWAP

- **Formula.** `AVWAP = sum(typical price x volume) / sum(volume)`, with typical price = (high + low + close) / 3, summed from the anchor session up to each later session.
- **Anchor.** The first of the last N closed trading days, N = 20 by default. At the latest closed session the AVWAP equals the plain N-day volume-weighted average price. Anchoring changes the AVWAP of the sessions before it, which all accumulate from the same anchor, and that is what a cross is judged against.
- **Setting N**, in three places (the more specific wins): `AVWAP_DAYS` in `.env` or in [app/config.py](app/config.py) is the default; `days` on `/api/chart` sets it for one request; and the **AVWAP anchored [N] trading days back** box beside the range buttons updates the panel at once and is kept while you search other tickers (reloading the page goes back to the default). The limits are 5 and 500 (`AVWAP_MIN_DAYS`, `AVWAP_MAX_DAYS`): the box clamps a typed value and the API answers 422 outside them. If the history is shorter than N, the panel says how many days it has and gives no read.
- **Not offered:** an anchor at a chosen date (an earnings day, a swing low). The anchor is always N sessions back from the latest closed one.

### The matrix

The panel shows the first rule that fits, checked in the order 6, 5, then 1 to 4: rules 5 and 6 are special cases of the four quadrants, so they must be tested first or they could never be reached. The six conclusions read exactly as the framework words them.

| # | Condition on the latest closed session | Conclusion | Colour |
|---|---|---|---|
| 1 | Close above the AVWAP, volume above its N-day average | Strong Institutional Accumulation (Strong Bullish) | green, solid |
| 2 | Close above the AVWAP, volume below its average | Exhaustion / Liquidity Trap (Caution / Neutral) | yellow |
| 3 | Close below the AVWAP, volume above its average | Aggressive Institutional Distribution (Strong Bearish) | red, solid |
| 4 | Close below the AVWAP, volume below its average | Passive Fade / Lack of Interest (Neutral / Bearish) | red, tinted |
| 5 | Close crossed the AVWAP, volume above its average | Trend Breakout/Breakdown Point (Directional Trigger) | blue |
| 6 | Price consolidating at the AVWAP, volume flat | Chop Zone / Equilibrium (Sit on Hands) | yellow |

Where the framework left a term open, this is what the app does (the numbers are in [app/config.py](app/config.py)):

- **Price** is the close. **Volume** is that session's volume, compared with the *mean* volume of the same N sessions the AVWAP covers, the session itself included. Volume exactly at the average counts as not above it.
- **Crossed** means the close is on one side of the AVWAP and the close before it was on the other side of that session's AVWAP (or on it). A cross on average or lower volume is no breakout: it keeps the quadrant it landed in. The panel says which way it crossed.
- **Consolidating at the AVWAP** means the last 5 closes (`AVWAP_CHOP_BARS`) span no more than 1 x ATR(14) (`AVWAP_CHOP_ATR`, `AVWAP_ATR_DAYS`) and the latest close is within that distance of the AVWAP. Tightness is judged on the closes alone because, for a short anchor, the AVWAP is the average of those same closes: the first version of the test, five closes each within one ATR of the AVWAP, made the chop zone fire on 42% of sessions at a 5-day anchor, and this one gives 16%.
- **Flat volume** is within 20% of the average (`AVWAP_FLAT_VOLUME`). A close exactly at the AVWAP is equilibrium whatever the volume.
- The chop zone is tested before the breakout, because price sitting on the AVWAP on ordinary volume wanders across it all the time, and each wander would otherwise read as a breakout. The same wobble on heavy volume is still a breakout.
- The panel's sentences restate what was measured: the distance, the volume ratio, the cross. Where volume is within 20% of its average, which is where the matrix's above/below split is thin, they add that the volume side of the call is marginal.

**How often each fires.** Ten real tickers (US, HK and A-share), about 1,240 sessions each, evaluated at every session, October 2026:

| Anchor N | 1 | 2 | 3 | 4 | 5 breakout | 6 chop | Closes across the AVWAP |
|---|---|---|---|---|---|---|---|
| 5 | 14.5% | 24.6% | 13.8% | 21.8% | 9.2% | 16.1% | 24.5% |
| 10 | 16.1% | 28.2% | 14.7% | 23.2% | 7.1% | 10.6% | 16.1% |
| 20 | 17.5% | 31.1% | 15.8% | 25.1% | 4.8% | 5.7% | 10.7% |
| 50 | 18.2% | 34.8% | 15.9% | 25.0% | 3.4% | 2.8% | 7.2% |
| 100 | 18.6% | 38.3% | 15.2% | 23.4% | 2.3% | 2.1% | 4.9% |
| 250 | 19.9% | 43.1% | 13.8% | 20.4% | 1.6% | 1.3% | 3.1% |

A shorter anchor stays closer to price and is crossed more often, so rules 5 and 6 fire more. Rules 2 and 4 outnumber 1 and 3 because a few very heavy days pull the average volume up, so most sessions fall below it. That comes from comparing with the mean, not from any stock.

### The latest closed session

The newest bar the data source returns can be a session that is still running. Its volume is a fraction of a day's and would read as "below average", so the app checks whether that bar's session had ended **when the data was fetched** (not when you look: a cached page's bars are up to an hour old, and the time is that of the bars' own fetch, not of the cache file). It uses the exchange's own clock: the regular close plus 15 minutes for the closing auction, which is 16:00 in New York, 16:00 in Hong Kong, 15:00 in Shanghai and Shenzhen and 17:00 in Singapore ([app/sessions.py](app/sessions.py)). If the session had not ended, the read is made on the session before and the panel says so. Half days are not modelled, so on those the newest bar is treated as unfinished for a few extra hours.

### Colours

Green is the accumulation call and red the distribution call, drawn as solid pills so the strong calls stand out; yellow is the trap and the chop zone; the weaker bearish call is a tinted red; the breakout trigger is blue, because it names a point to watch rather than a direction (the panel says which way the cross went). Colour is never the only signal: every state carries its words too. It works in the light and dark themes.

### API

`GET /api/chart?symbol=AAPL.US&range=1y&days=20` (`days` is optional, 5 to 500) also returns `avwap`:

```jsonc
"avwap": {
  "days": 20, "min_days": 5, "max_days": 500,
  "status": "ok | insufficient_history | no_volume", "message": null,
  "last_bar_final": true,                      // false: the newest bar was still forming when it was fetched
  "newest_bar": "2026-10-20", "as_of": "2026-10-20",   // as_of is the session the read is on
  "anchor": { "date": "2026-09-22" },
  "latest": { "close": 0, "avwap": 0, "distance": 0.012, "side": "above | below | at",
              "volume": 0, "avg_volume": 0, "rel_volume": 1.28, "crossed": "up | down | null",
              "consolidating": false, "atr": 0 },
  "conclusion": { "id": "accumulation", "label": "", "bias": "", "text": "Strong Institutional Accumulation (Strong Bullish)",
                  "tone": "good | warn | bad | info", "strong": true, "direction": null, "summary": "" },
  "matrix": [ { "id": "", "label": "", "bias": "", "text": "", "tone": "", "strong": false, "when": "" } ],
  "rules": { "flat_volume": 0.2, "chop_bars": 5, "chop_atr": 1.0, "atr_days": 14 }
}
```

A `status` other than `ok` leaves `latest` and `conclusion` null and gives the reason in `message`. The wording, tones and rule table come from the backend; the page only shows them.

### Limits

- The AVWAP is built from daily bars (one typical price per day), not from every trade, so it approximates the true volume-weighted average cost.
- "Institutional" is an inference from price against the volume-weighted average and from volume; the data does not show who traded. The matrix is a rule-of-thumb read of two numbers, not a forecast and not advice.
- Prices are forward-adjusted (see Notes and limits); volume is as reported.

---

## On-Balance Volume (OBV) insights

A second **Technical insights** panel, below the AVWAP one, reads the last 20 closed sessions as three things (price, volume and OBV direction), looks them up in a table with one row for each of the 27 combinations, and runs a separate divergence test between price and OBV. OBV and its 20-day moving average are also drawn in their own pane of the price chart. The code is in [app/obv.py](app/obv.py) and the tests in `tests/test_obv.py` (with the Node harness `tests/obv_card.js`).

**OBV** is a running total of volume: a session's volume is added on an up close and subtracted on a down close (`flows.obv`). Its level depends on where the history starts and means nothing; its direction does.

### The three readings

Each is taken over the last N = 20 closed sessions (`OBV_DAYS`):

| Reading | Rising / increasing | Falling / decreasing | Flat |
|---|---|---|---|
| **Price** | The close moved up by more than 1 x ATR(14) over the N sessions (`OBV_FLAT_ATR`) | Moved down by more than that | Moved by no more than that |
| **Volume** | Average volume over the N sessions is more than 10% above the average of the N before them (`OBV_FLAT_VOLUME`) | More than 10% below it | Within 10% |
| **OBV** | Its net change over the N sessions is more than 10% of the volume traded over them (`OBV_FLAT_SHARE`) | Less than minus 10% | Within 10% |

The read needs 2 x N = 40 closed sessions of history; with fewer the panel says how many it has and gives no read.

### The table

One row for each of the 27 combinations (price rising, falling or flat; volume increasing, flat or decreasing; OBV rising, falling or flat). The panel highlights the matching row under **How this is decided**.

| Price | Volume | OBV | Market condition | Underlying dynamic | Trading action |
|---|---|---|---|---|---|
| Rising | Increasing | Rising | Strong Bullish Trend (green, solid) | Institutional accumulation; buyers in full control. | Buy / Hold longs |
| Rising | Increasing | Falling | Distribution Into Strength † (red, tinted) | Heavy volume lifts the price, yet net volume is leaving; sellers are using the rally to unload. | Take profits / Tighten stops |
| Rising | Increasing | Flat | Churning Rally † (yellow) | Price rises on heavy volume but net buying and selling cancel out; the advance is not being absorbed. | Hold with tight stops / Wait for confirmation |
| Rising | Decreasing | Rising | Low-Volume Float (yellow) | Price drifts up on low liquidity; lack of selling pressure. | Hold longs / Trail stops |
| Rising | Decreasing | Falling | Bearish Divergence (red, solid) | Smart money is quietly exiting; retail chasing a hollow rally. | Exit longs / Look to Short |
| Rising | Decreasing | Flat | Exhausted Rally (yellow) | Price ticks higher on alternating low-vol days; no buying backing. | Take partial profits |
| Rising | Flat | Rising | Steady Advance † (green, tinted) | Price climbs on unchanged volume with buying running ahead of selling. | Hold longs |
| Rising | Flat | Falling | Unconfirmed Rally † (red, tinted) | Price climbs on unchanged volume while net volume leaves; the rise has no buying behind it. | Take partial profits / Tighten stops |
| Rising | Flat | Flat | Idle Advance † (yellow) | Price edges up on unchanged volume with no net buying or selling behind it. | Hold longs / Trail stops |
| Falling | Increasing | Rising | Absorbed Selling † (green, tinted) | Heavy volume pushes the price down, yet net volume is arriving; buyers are soaking up the selling. | Look to Buy reversals / Wait for confirmation |
| Falling | Increasing | Falling | Strong Bearish Trend (red, solid) | Institutional distribution; aggressive liquidation. | Avoid buying / Short |
| Falling | Increasing | Flat | Churning Decline † (yellow) | Price falls on heavy volume but net buying and selling cancel out; sellers have not taken control and buyers have not stepped in. | Avoid buying / Wait for base |
| Falling | Decreasing | Rising | Bullish Divergence (green, solid) | Smart money is absorbing panic selling and accumulating dips. | Look to Buy reversals |
| Falling | Decreasing | Falling | Selling Exhaustion (yellow) | Price drops but sellers are running out of shares/conviction. | Watch for baseline support |
| Falling | Decreasing | Flat | Illiquid Drift (yellow) | Price bleeds down purely due to a lack of buyers, not heavy selling. | Stay sidelines / Wait for base |
| Falling | Flat | Rising | Dip Buying † (green, tinted) | Price slips on unchanged volume while net volume builds; the dips are being bought. | Watch for a reversal |
| Falling | Flat | Falling | Steady Decline † (red, tinted) | Price falls on unchanged volume with selling running ahead of buying. | Avoid buying / Stay out |
| Falling | Flat | Flat | Idle Decline † (yellow) | Price slips on unchanged volume with no net buying or selling behind it. | Stay sidelines / Wait for base |
| Flat | Increasing | Rising | Silent Accumulation (green, tinted) | Large blocks are bought inside a range without spiking price. | Buy the upside breakout |
| Flat | Increasing | Falling | Silent Distribution (red, tinted) | Large blocks are sold inside a range without crashing price. | Short the downside breakdown |
| Flat | Increasing | Flat | High-Volume Churn (yellow) | Massive volume trading hands, but net buying/selling cancels out. | Wait for a breakout candle |
| Flat | Decreasing | Rising | Thin-Volume Accumulation † (green, tinted) | Net buying edges ahead while volume dries up and price holds its range; a quiet build. | Watch for the upside breakout |
| Flat | Decreasing | Falling | Thin-Volume Distribution † (red, tinted) | Net selling edges ahead while volume dries up and price holds its range. | Watch for the downside breakdown |
| Flat | Decreasing | Flat | Market Apathy (yellow) | No institutional interest; dead liquidity and zero momentum. | Stay cash / Avoid |
| Flat | Flat | Rising | Slow Accumulation † (green, tinted) | Buying steadily outweighs selling on unchanged volume while price holds its range. | Watch for the upside breakout |
| Flat | Flat | Falling | Slow Distribution † (red, tinted) | Selling steadily outweighs buying on unchanged volume while price holds its range. | Watch for the downside breakdown |
| Flat | Flat | Flat | Absolute Stagnation (yellow) | Complete market freeze. Asset is heavily balanced or illiquid. | Ignore ticker / Stay cash |

**Where the rows come from.** The 13 rows without a † are the framework's own, word for word (Market Apathy is from an earlier version of the framework's table, which the later one replaced with Absolute Stagnation; it is kept because its combination is otherwise empty). The 14 rows marked † are for combinations the framework's table does not list. They are this app's own wording and actions, written in the framework's style and kept deliberately cautious: none is a strong (solid) call, and the panel says so beside any row of this kind it shows. Change them in `RULES` in [app/obv.py](app/obv.py). Colour follows the row's lean: green for the bullish ones, red for the bearish ones, yellow for the cautions and waiting calls.

### Divergence

A separate test over the last 60 sessions (`OBV_DIVERGENCE_DAYS`), whose last 10 (`OBV_DIVERGENCE_RECENT`) are "recent":

- **Bearish divergence:** the highest close of the recent sessions is above every close earlier in the window, but OBV's highest value in the recent sessions is below its earlier peak by more than 5% of the window's volume (`OBV_DIVERGENCE_GAP`). Price made a new high that the volume did not confirm.
- **Bullish divergence:** the same upside down: a new low in the close that OBV's trough did not follow.
- If both hold after a very wide swing, the one whose price extreme is more recent is reported. It needs 60 closed sessions of history, separately from the table's 40.

This is not the same test as the table's two "Divergence" rows, which compare the direction of price and OBV over 20 sessions together with volume; the panel shows both and says so when they agree.

### In the bull and bear case and the critic

Both OBV results can add a point (`obv.points`, source "OBV"), at most one per side. Strong Bullish Trend, Bullish Divergence, Silent Accumulation, Steady Advance†, Absorbed Selling† and Dip Buying† add a supporting point; Strong Bearish Trend, Bearish Divergence, Silent Distribution, Distribution Into Strength†, Unconfirmed Rally† and Steady Decline† add a cautionary one. The other 15 rows (the cautions and waiting calls, and the thin or slow accumulation and distribution rows) add none: they are not a lean either way, or too faint to count. A price/OBV divergence adds a supporting point (bullish) or a cautionary one (bearish). When the table and the divergence test point the same way they are one point with both pieces of evidence; when they disagree both are kept. Every cautionary point also appears in the bear case as a medium-rated "Price action and sentiment" concern (`app/risks.py`), so it is not listed twice. OBV is also one of the six signals in the [Institutional buying and selling](#institutional-buying-and-selling) tally, so a falling OBV can show up in both.

### API

`GET /api/chart` also returns `obv`; `/api/analyze` uses the same read (without the series) for the thesis and the critic:

```jsonc
"obv": {
  "days": 20, "status": "ok | insufficient_history | no_volume", "message": null,
  "last_bar_final": true, "newest_bar": "2026-10-20", "as_of": "2026-10-20",
  "latest": { "price_change": 0.04, "price_state": "rising | falling | flat", "atr": 0, "flat_band": 0.02,
              "volume_ratio": 1.2, "volume_state": "increasing | decreasing | flat", "avg_volume": 0, "prior_avg_volume": 0,
              "obv_share": 0.3, "obv_state": "rising | falling | flat", "obv_vs_ma": "above | below | at" },
  "conclusion": { "id": "strong_bullish", "label": "", "dynamic": "", "action": "", "text": "Strong Bullish Trend (Buy / Hold longs)",
                  "tone": "good | warn | bad | info", "strong": true, "side": "bull | bear | null", "title": "", "evidence": "", "summary": "" },
  "divergence": { "status": "ok | insufficient_history", "kind": "bearish | bullish | null", "window": 60, "recent": 10,
                  "price": { "recent": 0, "recent_date": "", "earlier": 0, "earlier_date": "" },
                  "obv": { "recent": 0, "earlier": 0, "shortfall_share": 0.08 }, "summary": "" },
  "matrix": [ { "id": "", "price": "", "volume": "", "obv": "", "label": "", "dynamic": "", "action": "", "text": "", "tone": "", "strong": false, "framework": true } ],   // 27 rows; framework false = the app's own row
  "rules": { "flat_atr": 1.0, "atr_days": 14, "flat_share": 0.1, "flat_volume": 0.1, "ma_days": 20, "divergence_gap": 0.05 },
  "series": { "ma_days": 20, "from": "2025-10-21", "obv": [0], "ma": [null] }   // one value per candle returned, OBV shifted to start at 0
}
```

The newest bar is handled as in the AVWAP read: if its session was still running when the data was fetched, the read is made on the session before.

### Limits

- OBV counts a session's whole volume as buying or selling by the direction of the close, so it infers buying pressure from price and volume; the data does not show who traded. "Institutional" and "smart money" in the table are the framework's wording for that inference.
- The table's actions are a rule-of-thumb read, not a forecast and not advice. The thresholds (20 sessions, 1 x ATR, 10% of volume, 10% change in volume, 60 sessions, 5%) are this app's choices, set in [app/config.py](app/config.py). 13 of the 27 rows are the framework's; the 14 marked † are this app's own.
- OBV is built from daily bars, so a session that closes up on a gap but trades down all day still counts as an up day.

---

## Institutional buying and selling

Yes. No single indicator shows what institutions are doing, so the app combines **reported holdings and trades** (what they did, with a delay) with **flow indicators** (what money appears to be doing now). A panel under the chart, in the same tab, shows both, with a net **Accumulation / Neutral / Distribution** read.

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
| **On-Balance Volume (OBV)** | Running total of volume, added on up days and subtracted on down days. Rising OBV with flat price suggests accumulation. It is drawn in its own pane of the price chart, with its own read under the chart, so this panel does not draw it a second time. | Computed from daily K-lines |
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

**Morningstar fair value estimate.** Morningstar's number is its analysts' own and has no free data source, so the app cannot fetch it. The Fair value section has a box where you type the figure from morningstar.com; the app then shows it with price / fair value and the margin of safety. It is saved per symbol in your browser only, and it is not used in any score.

The **Valuation** tab shows an **app-built fair value estimate**. It is **not GuruFocus's GF Value**. GF Value is a proprietary number from GuruFocus's own model, and its only legitimate programmatic source is the GuruFocus API, which is for Premium members and needs an API token. The documentation could not be read from here (the site returned 403), so no GuruFocus integration was built. If you later get a token, a GF Value card can be added next to this one.

The estimate uses three methods, all from the financial statements and the quote (`app/valuation.py`):

| Method | How it works | Fits |
|---|---|---|
| **Discounted cash flow** (headline) | Free cash flow (TTM: operating cash flow less capex, before any debt repaid or raised; the figure that counts the debt is [free cash flow to equity](#free-cash-flow-to-equity)). Stage-1 growth for 5 years, fading linearly to 3% terminal growth over 5 more years, discounted at the cost of equity (risk-free rate + beta x equity premium; beta is assumed 1.0 because the data has none). Stage-1 growth is the **median** of the available growth rates (FCF, net income and revenue 3-year CAGR, and consensus EPS growth), floored at 0 and **capped at 20%**. | Companies with positive free cash flow |
| **Earnings power value** | Normalised operating earnings (average margin x TTM revenue, after tax) divided by the cost of capital, plus cash, less debt. Gives no credit for growth, so it is a floor-style value. | Companies with positive operating earnings |
| **Graham number** | sqrt(22.5 x EPS x book value per share), from the quoted P/E and P/B. | Steady, asset-backed companies; far below the price of most growth stocks |

- **Per-share value needs no share count.** It is price x (equity value / market cap), so the share count cancels. The statements carry none.
- **Sensitivity grid.** Fair value at three discount rates (-1, 0, +1 point) and three growth rates (-5, 0, +5 points). The range shown beside the headline is the grid's minimum and maximum.
- **Reverse DCF.** The page also solves for the stage-1 growth that makes the DCF equal today's price, for example "today's price implies free cash flow growing about 19% a year for 5 years". This needs no growth assumption from the app, so it is the most robust line in the section.
- **Rating bands** on price / fair value: up to 0.80 undervalued, 0.80 to 0.95 modestly undervalued, 0.95 to 1.15 roughly fair, 1.15 to 1.40 modestly overvalued, above 1.40 overvalued. These cut-offs are the app's own.
- **Thesis.** Only the extremes add a point: price at or below 0.80 of the DCF value is a bull point and at or above 1.40 is a bear point, both labelled "(app model)". The DCF value also appears in the **reference price levels**, a table under the fair value in the same tab: the analyst mean target, the volume profile's value area and point of control, Graham's buy line, the DCF value and the 52-week high and low, each with its distance from the price. They are reference points from different methods, not forecasts.
- **Not applicable** to banks, insurers and REITs, and the DCF is skipped (with the reason) when free cash flow is not positive. Nothing is estimated for missing inputs.

Known biases, stated on the page: free cash flow is before stock-based compensation (which flatters software and chip companies), a beta of 1.0 is too high for defensive stocks such as KO (so their DCF is understated), and the 20% growth cap is deliberately conservative for very fast growers. Change `TERMINAL_GROWTH` and `DCF_MAX_GROWTH` in `app/config.py` to test other assumptions.

---

## Stock quality scorecard

The **Quality Scorecard** panel, at the top of the Fundamentals tab, shows well-known, published scoring systems side by side. The app uses these instead of its own invented composite score. Each shows its score, the published thresholds, and the inputs behind it, so the reader can check the arithmetic. The Buffett five-dimension breakdown and the Graham cigar-butt analysis behind two of the rows are in a box under the table that starts closed.

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
- **Sources.** Each row names its source (Longbridge fields used) and the fiscal period, and appears in the Data sources table with the formula.
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

## Two-stage stock filter

The **Two-stage filter** tab applies the Reset & Invest *Two-Stage Stock Filter* (v2.1, October 2026) to the stock: three mandatory pass/fail checks, then eight questions worth one point each, with 7 of 8 to make the list. The rules are the checklist's own, in [SKILL.md](.agents/skills/two-stage-stock-filter/SKILL.md), unpacked like the Longbridge skills from the `.skill` zip it arrived in (the zip and its [install note](.agents/skills/The-Two-Stage-Stock-Filter_2026-10-21/INSTALL-claude-skill.md) for Claude's own skills setting are still in `.agents/skills/The-Two-Stage-Stock-Filter_2026-10-21/`). The app computes them in [app/twostage.py](app/twostage.py) and [app/pricehist.py](app/pricehist.py) and the page only draws the result.

It is a screen, not advice. "Makes the list" means *qualifies for further research under this checklist*. The panel opens with the checklist's own research notice, puts a source, a period and a kind (`REPORTED`, `CALCULATED`, `CONSENSUS ESTIMATE`, `USER INPUT`, `ANALYST JUDGMENT`) beside every figure, and closes with its own "before you trust this" line. Nothing is loosened to let a company through and nothing is estimated: **a figure that cannot be verified is UNKNOWN, with what is missing, and a company the checklist does not fit has the affected tests NOT APPLICABLE and the screen INCOMPLETE.**

### The checklist as built

| Stage | Test | Rule (the comparison is exactly as the checklist words it) | Where the figure comes from |
|---|---|---|---|
| 1. **Can it survive?** | Cash pool above debt, **or** net debt / TTM EBITDA under 1.5x | Either route is enough. "Above" and "under" are strict. EBITDA at zero or below fails the leverage route | Latest quarter's balance sheet; EBITDA = operating income + D&A, TTM |
| 1. **Does it make cash?** | TTM FCF margin at least 15% | FCF = operating cash flow − capex | Cash flow statement, four quarters |
| | TTM ROIC above 15% | NOPAT = operating income × (1 − tax / pretax income) over the average of the latest invested capital and the one 12 months earlier (equity + debt − cash pool). Not meaningful when pretax income or the average capital is not positive, or the tax rate is outside 0–100% | Income statement and balance sheets, the latest quarter and the quarter four back |
| | TTM SBC / revenue under 10% | SBC is not subtracted from FCF | Cash flow statement (`stock_based_cf`) |
| 1. **Is the price fair?** | Forward P/E at or below its own five-year average forward P/E | Forward P/E = close / consensus EPS | EPS: `forecast-eps`; the average is **yours** (see below) |
| | PEG at or below 1.5 | Forward P/E / expected annual EPS growth as a percentage number (30 / 20 = 1.5). 1.5 to 2.0 is a **review**, not a fail; above 2.0 fails; growth or EPS that is not positive fails as not meaningful | The growth is **yours** |
| | Three-month price rise under 50% | Latest completed session against the session on or just before three calendar months earlier, on closes adjusted for **splits only** | Unadjusted daily bars and the split list |
| 2. Q1 | Growth and margins | Revenue growth over 10%, TTM gross margin over 50% (software) or 35% (hardware), TTM operating margin over 20%. All three 1, two 0.5, else 0 | Eight quarters of revenue; the statements |
| Q2 | Few investors betting against it | Short interest under 5% of float is 1, otherwise 0 | `short-positions`, US and Hong Kong |
| Q3 | Rewarding shareholders | Five-year total return (dividends reinvested) above the S&P 500's over the same dates; TTM diluted shares no higher than five years earlier. Both 1, one 0.5 | Daily bars and quarterly summary |
| Q4 | Written down what would change your mind | Pending until **you** confirm a thesis and its invalidation conditions | You |
| Q5–Q8 | Moat and CEO, runway, culture, retention | Judgment ratings 1 to 3 (3 is a point, 2 half, 1 none). Too little evidence is UNKNOWN, never a neutral 2 | You, beside the evidence the app has |

**Stage one decides stage two.** Any FAIL fails the stage; with no FAIL, anything UNKNOWN or NOT APPLICABLE makes it INCOMPLETE; otherwise it passes (a PEG in the review band travels with the result as PASS WITH PEG REVIEW). Stage two runs only after a pass, unless you tick **Run stage two even if stage one does not pass**, which does not change the stage-one result or make the stock eligible.

**Totals.** Complete scores: 7 or more of 8 makes the list, 6.5 is a close call, 6 or less misses. While question 4 is pending the other seven are scored as if it will be 1: 6 or more of 7 makes the list *once question 4 is confirmed*, 5.5 is a close call once confirmed, 5 or less misses (confirming cannot lift it to 7). With any question UNKNOWN the result is PROVISIONAL / INCOMPLETE: the confirmed points and the possible range out of 8 are shown, nothing is awarded on a guess, and the denominator is not rescaled. Below the table the panel lists **where the points went** (each failed condition with its shortfall, in percentage points where it is a gap between percentages; the unknowns apart from the failures; the unscored customer-concentration flag from the SEC text search), **what to verify first** (the figures the result is waiting for, then the ones within 30% of their cut-off, each with the filing statement or market-data field to check), and, only for a stock that passed both stages, the **timing reference points**: the latest completed upward swing (a pivot high strictly above the two sessions each side, with the pivot low before it), its 61.8–78.6% retracement zone, the 200-day EMA of split-adjusted closes on at least two years of bars, and the next earnings date. These are reference points, not predicted support or entry points.

### What you type in

Longbridge has no five-year history of *forward* P/E (its `valuation --history` is trailing P/E, which the checklist forbids substituting, and `forecast-eps` held about six months for AAPL) and no three-to-five-year EPS growth estimate. Those two tests therefore stay UNKNOWN until you enter the figures from a data vendor, with the source and date, which the row then shows. The page tells you what would settle each one ("passes if the average is at least 37.9x"; "PEG is at most 1.5 if growth is at least 25.3% a year"). Also yours: a **software or hardware** choice for question 1 (the app reads it from the industry's name, in English or Chinese, and leaves a business that fits neither, or both, unresolved rather than give it the easier cut-off), the **thesis** for question 4 (the app drafts one from the measures that pass, labelled *proposed*; it scores only after you confirm both texts), and the **ratings** for questions 5 to 8, shown beside what the Buffett read in the quality scorecard says about moat, management and visibility. Everything is kept in this browser per symbol (`tsf:SYMBOL`), as the Morningstar number is, and goes to the server with each request; **Clear my inputs** removes it.

### How the data is read

- **Trailing twelve months** are the last four quarters when each has the line and they run on from one another, else the last fiscal year, and the period is printed on every row. A ratio never mixes the two.
- **Debt and leases.** Debt is interest-bearing borrowings (short-term borrowings, the current portion of long-term debt, long-term debt). Lease liabilities are shown apart. Longbridge's standardized balance sheet puts a company's finance leases in its lease lines (MSFT's 69B) and, in some layouts, its operating leases too (AMZN's single combined line), and the checklist counts only finance leases as debt. So any test that depends on debt (check 1, ROIC) is evaluated **both ways**, with and without the lease lines, and the result stands only if both agree; if they differ, it is UNKNOWN and says the lease note decides it. MSFT's "cash above debt" is UNKNOWN for that reason while its leverage route passes either way.
- **Quarterly history.** The statements hold five quarters, so revenue growth (eight quarters) and the share count five years back come from `financial-report`'s quarterly summary (43 quarters). Its EPS is already adjusted for later splits (NVDA's fiscal 2021 shows 0.173, not 6.90), so net profit / EPS summed over four quarters is a split-adjusted diluted share count, compared with the four quarters ending exactly five years earlier. Companies report EPS to the cent in the shares of their day, so the rounding allowance for each quarter is divided by the splits since: without that, NVDA's 3.2% fall in shares was called unknowable. A change inside the allowance is UNKNOWN.
- **Prices.** The forward-adjusted bars the rest of the app uses fold dividends in, which makes them a total-return series: right for the five-year return, wrong for the price tests. The filter fetches unadjusted bars and applies the splits from `corp-action` (a split's ratio is read from its description, in one fixed wording; one that cannot be read is an error, not a guess). It then checks its split-only closes against Longbridge's adjusted ones: they may differ by dividends alone, and if they differ by more, something the splits do not account for (a bonus or rights issue) is missing and the test is UNKNOWN. The five-year return divides forward-adjusted closes, which matches dividend reinvestment at each ex-date (MSFT: raw 309.16 against 297.26 adjusted is a 3.85% dividend adjustment, +76.5% total against +69.7% price).
- **The S&P 500 comparison** uses SPY's forward-adjusted close as the total-return proxy (the index `.SPX.US` is price-only; SPY runs about 0.1 point a year below it), so a gap under one point of cumulative return is too close to call and UNKNOWN. It is made only for stocks that trade in USD: the app does not convert currencies. Longbridge's quote has no currency (it is empty for AAPL and for Tencent), so a listing is taken to trade in its market's currency.
- **Short interest** is Longbridge's rate (shares short over circulating shares) with its settlement date. A report older than 60 days is not "the latest reported" and leaves question 2 UNKNOWN: Longbridge's history for JPM.US and KO.US stops at 2022-06-15.
- **Forward EPS** is `forecast-eps`'s consensus mean. Longbridge does not say which fiscal year it covers (its current forecast year), whether it is GAAP or adjusted, or, for a company that reports in one currency and trades in another, which currency it is in; the row says so and the figures to verify first include it.
- **Framework fit.** Banks, insurers and REITs (matched by industry name in English and Chinese) have check 1 and check 2 marked NOT APPLICABLE; a company with no trailing revenue has margin tests NOT APPLICABLE. Both leave the screen INCOMPLETE; the price tests and the market questions still run.
- **Comparisons are unrounded**, with a tie guard of a billionth against floating-point noise (440 / 400 − 1 is 0.10000000000000009, and growth of exactly 10% is not "over 10%").
- **Calls.** One request makes about nine CLI calls beyond the ones the other panels already made: `financial-report` (quarterly and annual), `short-positions` and `static`, `kline --adjust none` (600 bars), `corp-action --all --lang zh-CN`, `kline history SPY.US --adjust forward` twice, and `finance-calendar report`. They run one after another, because the data service answers a burst with its one-second limit (code 429002), and each is optional: one that fails leaves only the tests that need it UNKNOWN. With nothing cached, the filter alone (statements and prices included) took 8 to 10 seconds for each of AAPL, NVDA, MSFT, AMZN, 700.HK, JPM and QUBT.

### API

`GET /api/filter?symbol=MSFT.US` with optional `avg_fwd_pe` (a positive number), `avg_fwd_pe_src`, `eps_cagr` (a percentage number, 12 for 12%), `eps_cagr_src`, `category` (`auto`, `software`, `hardware`), `stage2` (run stage two anyway), `q4` (the thesis is confirmed) and `q5` to `q8` (1 to 3). Values outside their range are a 422.

```jsonc
{
  "symbol": "MSFT.US", "status": "ok",                   // "unavailable" (statements fail reconciliation) comes with a "message"
  "notice": "Research notice: ...", "closing": "Before you trust this: ...",
  "dates": { "research": "2026-10-24", "financials": "TTM to 2026-06-30 (Q4 2026)", "market": "2026-10-23 close" },
  "fit": { "notes": [], "category": { "value": "software", "source": "the industry's name", "industry": "" } },
  "verdict": { "line": "MSFT.US (...): stage one PASS WITH PEG REVIEW; stage two ...", "stage_one": "", "stage_two": "", "tone": "good" },
  "stage_one": { "status": "PASS | PASS WITH PEG REVIEW | FAIL | INCOMPLETE", "decision": "", "checks": [
    { "id": "survive | cash | price", "title": "", "status": "PASS | FAIL | UNKNOWN | NOT APPLICABLE | PASS WITH REVIEW", "workings": [], "where": "",
      "rows": [ { "id": "", "label": "", "figure": "", "period": "", "threshold": "", "status": "", "kind": "", "source": "", "gap": null, "note": null } ] } ] },
  "stage_two": { "run": true, "on_request": false, "questions": [ { "n": 1, "title": "", "status": "SCORED | UNKNOWN | PENDING", "points": 1,
                 "low": 1, "high": 1, "evidence": "", "conditions": [ { "id": "", "label": "", "figure": "", "threshold": "", "status": "", "gap": null } ] } ],
                 "total": { "confirmed": 6.5, "out_of": 7, "low": 6.5, "high": 6.5, "unknown": [], "pending_q4": true, "label": "", "note": "" } },
  "points_lost": { "failed": [], "below_full": [], "unknown": [] },
  "verify": [ { "title": "", "value": "", "why": "", "where": "" } ],
  "timing": null,                                        // only for a stock that passed both stages
  "proposal": { "thesis": "", "conditions": [], "note": "" }, "judgment": [ { "n": 5, "title": "", "assess": "" } ], "inputs": {}
}
```

### Limits

- The two market figures (five-year average forward P/E, expected EPS growth) have to be typed in, so the filter reaches INCOMPLETE for almost every stock until they are. That is the honest result of what Longbridge offers, not a missing feature.
- The checklist's "next fiscal year" EPS cannot be confirmed from Longbridge's unlabelled forecast, so the forward P/E carries that caveat on its row.
- Software versus hardware is read from the industry's name only; the checklist asks for a mixed business to be classed from its segment revenue, which the app does not do. Tencent (internet content) and AMZN (retail) came out unresolved, and question 1 then stays UNKNOWN until you choose.
- The five-year return is compared with the S&P 500 for US-dollar stocks only, using SPY as a proxy.
- Question 3's share count is net profit over diluted EPS, which is exact for plain common stock and approximate where preferred dividends or participating securities separate the two.
- It always runs as of today; the checklist's historical as-of date is not supported.
- Questions 5 to 8 are judgments. The app shows the evidence it has (the Buffett read for 5 and 6; nothing measurable exists for 7 and 8) and never rates them.
- The concentration flag is the existing SEC text search (US stocks, `SEC_USER_AGENT` set): a sentence over 20% is marked, and silence is not proof that no customer is that large.
- Checked live on AAPL.US, NVDA.US, MSFT.US, AMZN.US, 700.HK, JPM.US and QUBT.US in October 2026. What was reproduced independently: AMZN's trailing free cash flow of −11.62B (the same operating cash flow and capex the project notes recorded the day before), MSFT's five-year return from raw and adjusted closes, and the stale JPM short-interest history straight from the CLI. The other figures (share counts, margins, growth) come from Longbridge's statements and summary as they are and were not checked against the filings themselves, which is what the "what to verify first" list is for.

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

`Scan` takes a pasted list of symbols (up to 20) and runs a lighter pipeline per symbol: quote, daily bars for HV, the expiries in the window, one chain per chosen expiry, and one batched `option quote` for the candidate legs. About 8 CLI calls per symbol, within the 10 calls per second limit; results are cached as market data, for `LIVE_CACHE_MINUTES`.

The results table has one row per candidate across all symbols: symbol, structure, expiry, legs, credit or debit, max loss, return on risk, probability of profit, IV/HV, flags. Optional filters:

- IV/HV above or below a level,
- defined risk only (on by default),
- **quality filter**: only symbols that pass at least N of the stock quality scorecards, so premium-selling candidates are not ranked on stocks with weak fundamentals (this runs the full analysis, so it is slower).

### UI

The **Options** tab. It loads after the other panels (the scan makes about 20 CLI calls and takes 10 to 30 seconds on live data), and the **Scan** button re-runs it with the current controls:

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

A ticker that was fetched recently is not fetched again. Its data is saved in the `data/` folder, one JSON file per ticker, and the page is built from that file instead of from Longbridge. The file holds two kinds of data, kept for different lengths of time:

| Kind | What is in it | Reused for | Setting |
|---|---|---|---|
| **Fundamentals**: what changes a quarter at a time | Statements and the 43-quarter summary, analyst ratings and estimates, dividends, insider trades, holders, short interest, the list of companies in the industry, the S&P 500 proxy's close on a past date, a news article's text, the latest 10-K and 10-Q text | As long as the file lives | `DISK_CACHE_MINUTES` (default 30) |
| **Market data**: what changes by the hour | The quote (price, market cap, P/E, P/B, 52-week range), daily bars, capital flow, option chains and quotes, news, the SEC filing list, community posts, market temperature, the industry's P/E, P/B and P/S medians, splits, the next earnings date | An hour, and never longer than the file | `LIVE_CACHE_MINUTES` (default 60) |

So a file can be kept for a week and the price on the page is still current: set `DISK_CACHE_MINUTES=10080` and the market data is fetched again whenever it is an hour old, while the statements, ratings and holders, which are the slow part of a fetch, are not. Which call belongs to which kind is `LIVE_CALLS` and `STATIC_CALLS` in [app/config.py](app/config.py). A call that is in neither counts as market data, and a test fails until every data call is in exactly one of them.

| When you search a ticker | What happens |
|---|---|
| There is no file for it | Everything is fetched as usual and saved to `data/TICKER-TIMESTAMP.json`, for example `data/AAPL.US-20261020T143210+0800.json`. |
| The file is younger than `DISK_CACHE_MINUTES` and its market data is under an hour old | Nothing is fetched. The whole page (analysis, chart, flows, news and the option scan) is built from the file by the same code that builds it from live data, so it is the same page. A line under the search box says *Data from cache. Fundamentals (statements, ratings, holders) as of 22 Oct 2026, 9:35 pm (2 days ago); fetched again after 29 Oct 2026, 9:35 pm. Market data (prices, bars, options, news) as of 24 Oct 2026, 12:49 pm (12 min ago); fetched again once it is 1 h old.* |
| The file is younger than `DISK_CACHE_MINUTES`, but some of its market data is an hour old or more | Only that market data is fetched again, and it replaces the old rows in the same file, which keeps its name and time. The fundamentals are not touched. |
| The file is `DISK_CACHE_MINUTES` old or more | Everything is fetched again and saved to a new file named with the current time. The old file is deleted. |

How it works:

- **The file name is the clock for the fundamentals.** The timestamp is local time with its UTC offset (`+0800`), and it alone decides whether a file is fresh, so renaming a file makes it older or younger. A time in the future is not trusted. The lifetime is `DISK_CACHE_MINUTES` in `.env` (default 30; 0 turns the cache off).
- **Market data carries its own time.** Each call in the file is stored with the time it was fetched (`"at"`), and market data is used while that is less than `LIVE_CACHE_MINUTES` ago (default 60; never more than `DISK_CACHE_MINUTES`; 0 fetches it on every search). It is counted from the call, not from the file. A file written before these times were stored is read as it always was: every call is as old as the file.
- **The file holds the fetched data, not the analysis.** It is a log of the data calls made for the ticker (`quote`, `kline`, `statements`, `option_chain` and so on), one per line, with what each returned. Scores, charts and option candidates are recomputed from it every time, which is why a cached page cannot differ from a live one. In the Data sources table each figure shows when the calls it rests on were fetched, the oldest of them: a P/E is as old as the statements under it, the moving averages as old as the bars.
- **What the file does not hold is fetched and added.** An option scan that needs an expiry that was not scanned before, or **Show more** news, is fetched live and merged into the file, which keeps its name and time.
- **Market data nobody can use any more is dropped.** Whenever the file is saved, market data older than its lifetime is removed (the chain of an expiry that was not asked for again, a set of option quotes for a price the stock has left), so a file kept for a week does not fill up.
- **A failed call is tried again.** It is not saved, so a hiccup such as the SEC rate limit on insider trades is not frozen in. The exception is a call the app already treats as unimportant: its failure is saved, or every search would repeat it. Today that is the news summary: `longbridge news detail` answers in Markdown, not JSON (checked on CLI 0.26.0 in October 2026), so every summary fails after three attempts, about 2 seconds per article. The news panel therefore shows headlines only and takes about 20 seconds on a live fetch, but a cached one is instant. A saved failure counts as a fundamental (an article's text does not change), so each article is tried once while the file lives, not once an hour.
- **Only live data is cached.** Demo mode never writes files. The IV history (`data/iv_history.json`) is added to only when the option chains were fetched live, so a cached page does not stamp today's date on old IV figures.
- **The peers' figures are kept separately, and for longer.** What was computed for each company used as a peer (a dozen figures, not its raw statements) is in `data/peers.json` for 7 days (`PEER_CACHE_DAYS`), because a peer takes 11 CLI calls and a company's fundamentals change a quarter at a time. It is by company, not by search, so it is shared by every stock in the industry. The figures that use a peer's price (EV / EBITDA and the free-cash-flow yields) are therefore as old as that, not an hour. See [Industry comparison](#industry-comparison).
- **A file that cannot be read or written never breaks the page.** An unreadable file (one whose call times are not times is unreadable too), or one written by another version of the code (`VERSION` in [app/snapshot.py](app/snapshot.py), to be bumped when a data call changes what it returns), is ignored and replaced. If `data/` cannot be written, the page is built from live data and a warning goes to the server log.
- **Refresh data button.** The **Refresh data** button next to Analyze (and "refresh now" in the cache note) deletes the ticker's cache file and its in-memory entries, then searches again, so everything, fundamentals included, is fetched live without waiting for either lifetime to pass. It is `POST /api/refresh?symbol=...`.
- **Two settings.** `DISK_CACHE_MINUTES` is how long a file and its fundamentals live, `LIVE_CACHE_MINUTES` how long market data does. Both are minutes: a number, or plain arithmetic such as `60*24*7` for a week (a value that is neither is reported on start-up and the default is used). The in-memory cache never keeps a call longer than that call's disk lifetime (and 15 minutes at most), so lowering either is never undercut by an older copy in memory. With the disk cache off, live data is held in memory for only a few seconds, so panels loading together share one fetch and every new search fetches live data.
- **The files are git-ignored** (`data/*.json`). To force a fresh fetch for one ticker, delete its file.

Measured on AAPL.US with the real CLI: the first search took 29 seconds and 65 CLI calls. The same search after restarting the server took 0.2 seconds and 1 CLI call (the SEC-rate-limited insider-trades call, retried by design), and all five panels were identical.

With a week for the file and an hour for market data (CLI 0.26.0, Saturday 24 October 2026, so prices did not move): the first search of AAPL.US (analysis, chart, flows, news, option scan and the two-stage filter) took 45 seconds and 52 CLI calls. The same search straight away took 1.5 seconds and no CLI calls. Two hours later it took 12 seconds and 23 calls, all of them market data and none for statements, ratings, holders or short interest, and all six panels were identical to the cold fetch.

Limits:

- Market data can be up to `LIVE_CACHE_MINUTES` old (an hour by default), so the price is not live. **Refresh data** fetches everything again; a lower `LIVE_CACHE_MINUTES` keeps the price fresher at the cost of more fetching.
- Fundamentals can be as old as `DISK_CACHE_MINUTES`. A company that reports earnings in that time shows its old statements, ratings and estimates until the file expires or you click **Refresh data**.
- A data call that returns a degraded result without failing (for example a statement section that came back empty) is saved as it came, and stays until its lifetime is over.
- Searches are cached per ticker, not per set of option controls: changing the expiry window reuses everything the file holds and fetches only what is new.

---

## Skills used

The skills live in [.agents/skills/](.agents/skills/). The four Longbridge skills are locked in [skills-lock.json](skills-lock.json), which pins skills installed from a GitHub repository; the two-stage skill is a local download from Reset & Invest, so it is not in the lock file.

| Skill | Role in the app |
|---|---|
| [longbridge](.agents/skills/longbridge/SKILL.md) | Data layer. CLI/SDK commands for quotes, K-lines, financial statements, valuation rank, analyst estimates, news, filings, insider trades and 13F holders. |
| [longbridge-value-investing](.agents/skills/longbridge-value-investing/SKILL.md) | Core scoring. Buffett five-dimension moat analysis and Graham 100-point NCAV analysis. These supply most of the bull and bear arguments. |
| [longbridge-quant](.agents/skills/longbridge-quant/SKILL.md) | Quant overlay. Volatility regime, seasonality, multi-factor z-scores, statistical checks. |
| [two-stage-stock-filter](.agents/skills/two-stage-stock-filter/SKILL.md) | The Reset & Invest checklist (v2.1). Its rules are implemented in `app/twostage.py`; see [Two-stage stock filter](#two-stage-stock-filter). It is not a Longbridge skill and feeds no bull or bear point. |
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
longbridge industry-valuation dist AAPL.US --format json                      # industry median and rank for P/E, P/B, P/S
longbridge valuation AAPL.US --format json                                    # `symbols`: the companies in the industry, with market caps (the peers)
longbridge institution-rating AAPL.US --format json                          # Buy/Hold/Sell distribution + target price
longbridge forecast-eps AAPL.US --format json                                 # EPS consensus
longbridge kline AAPL.US --period day --count 1500 --format json
longbridge dividend AAPL.US --format json
longbridge shareholder AAPL.US --count 10 --format json                       # institutional holders
longbridge insider-trades AAPL.US --count 40 --format json                    # US only
longbridge capital AAPL.US --flow --format json                               # net flow by order size
longbridge short-positions AAPL.US --count 1 --format json                    # US and HK only
longbridge static AAPL.US --format json                                       # circulating shares: the float short interest is measured against
longbridge financial-report AAPL.US --report qf --format json                 # 43 quarters of revenue, EPS, net profit (and --report af); two-stage filter
longbridge kline AAPL.US --period day --adjust none --count 600 --format json  # unadjusted bars; two-stage filter
longbridge kline history SPY.US --adjust forward --start 2021-10-12 --end 2021-10-22 --format json   # S&P 500 proxy; two-stage filter
longbridge corp-action AAPL.US --all --lang zh-CN --format json               # splits and reverse splits; two-stage filter
longbridge market-temp US --format json
longbridge news AAPL.US --count 20 --format json
longbridge filing AAPL.US --count 8 --format json
longbridge topic AAPL.US --count 20 --format json
```

**Use `financial-statement`, not `financial-report`, for the statements.** On US accounts `financial-report` ignores `--kind` and returns only a summary, so it cannot supply the full income statement, balance sheet and cash flow the scorecards need. The two-stage filter reads that summary only for what the five-quarter statements cannot give: eight quarters of revenue and the share count five years back (43 quarters of revenue, operating income, net profit, EPS and gross margin).

Use WebSearch (or another web source) only for gaps Longbridge cannot fill, such as industry outlook, management track record or regulatory news. Tag each such figure `[Source: WebSearch, publisher, date, url]` so it never mixes silently with Longbridge data.

---

## Architecture

```
Browser (HTML + JS)
      │  GET /api/analyze?symbol=AAPL.US
      ▼
FastAPI backend (Python)
  ├─ data.py        runs longbridge CLI (or Python SDK), parses JSON, caches results in memory and, per ticker, on disk
  ├─ snapshot.py    per-ticker cache files in data/: TICKER-timestamp.json; fundamentals reused for DISK_CACHE_MINUTES, market data for LIVE_CACHE_MINUTES
  ├─ reconcile.py   cross-statement checks (IS↔BS↔CF, market cap, period alignment)
  ├─ buffett.py     five-dimension moat scoring
  ├─ graham.py      NCAV + static/dynamic scoring + value-trap rules
  ├─ quant.py       volatility regime, momentum, factor z-scores
  ├─ metrics.py     P/E TTM, PEG, FCF growth, Debt/EBITDA, ratings, op. margin, ROIC, ROE, NI growth + extras
  ├─ benchmarks.py  P/E, P/B and P/S against the industry median (Longbridge industry-valuation dist); the peer medians
  ├─ peers.py       the peer group: nearest companies in size, each measured by metrics.py and the reconciliation gate
  ├─ peerstore.py   the peers' computed figures, kept for 7 days in data/peers.json
  ├─ chart.py       EMA 20/50/200, volume profile (POC, VAH, VAL), trend signals
  ├─ avwap.py       anchored VWAP over a configurable number of days, and the six-rule price/volume matrix
  ├─ obv.py         OBV read: price/volume/OBV 27-row table, divergence, chart series
  ├─ twostage.py    the Two-Stage Stock Filter: three checks, eight questions, totals, what to verify first, timing points
  ├─ pricehist.py   calendar months, split adjustment, returns between two dates, swings (for the filter)
  ├─ sessions.py    exchange clocks: a daily bar's local trading date, and whether its session had ended
  ├─ flows.py       13F and insider activity, capital flow, OBV/A-D/CMF, accumulation/distribution read
  ├─ news.py        latest news (10, expandable to 25), dedupe, filings, market mood facts
  ├─ quality.py     Piotroski, Altman, Beneish, Sloan, Magic Formula, ROIC vs. WACC
  ├─ valuation.py   DCF with growth fade, reverse DCF, earnings power value, Graham number
  ├─ options.py     option chain and quote parsing, OCC symbols, Black-Scholes price and Greeks, IV vs HV, term structure, skew
  ├─ strategies.py  strategy matrix: builds spreads, condors, straddles, calendars from live strikes; payoff, breakevens, model POP
  ├─ option_scan.py market view from the analysis, filters and flags, multi-symbol scan
  ├─ risks.py       the bear case: concerns with their figures (merged with thesis.py's rule-based points), and the risks the data cannot show
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

If a check fails, the app **does not score**. It shows which figure failed and by how much. A pass shows as a single summary line in the Data sources box.

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
SEC_USER_AGENT=Your Name you@example.com   # optional; lets the app read the latest 10-K / 10-Q from SEC EDGAR (US only)
GEMINI_API_KEY=...                # optional; GEMINI_MODEL overrides the model (default gemini-2.5-flash)
DATA_MODE=cli                     # cli | demo (synthetic data, no login needed)
DISK_CACHE_MINUTES=30             # how long a ticker's cache file and its fundamentals are reused (see Data cache); 10080 = a week, and 60*24*7 works too; 0 = off
LIVE_CACHE_MINUTES=60             # optional; how long its market data (prices, bars, options, news) is reused, never more than DISK_CACHE_MINUTES; 0 = every search
AVWAP_DAYS=20                     # optional; trading days back the anchored VWAP starts (5 to 500); the chart has a box to change it
PEER_CACHE_DAYS=7                 # optional; how long a peer's computed figures are reused (data/peers.json); 0 = never reused
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

The tests use demo mode and cover the EMA, volume profile, growth rules, flow indicators, news de-duplication, field-mapping layouts, the reconciliation gate (including a failing case), the API endpoints and input validation. `tests/test_cache.py` covers the per-ticker cache: file naming, the freshness boundary, replacing and renaming an old file, merging calls the file lacks, retrying failed calls, unreadable files, an unwritable folder, requests arriving together, and that a cached page equals the live one. It also covers the two lifetimes: market data fetched again after an hour inside a file kept for a week, every data call belonging to exactly one of the two kinds, a stale row replaced and dropped, the time each Data sources row shows, a bar refreshed mid-session being judged by its own fetch, files without per-call times, reading the two settings (including `60*24*7`), and the note the page shows (under Node). `tests/test_avwap.py` covers the anchored VWAP (against hand-computed values and the plain formula), each of the six conclusions reached from price and volume, the decision table including its precedence, the days setting and its limits, and the page controls; `tests/test_sessions.py` covers bar dates and session ends, including summer and winter time. `tests/test_obv.py` covers the OBV read: the framework's rows word for word, every one of the 27 combinations having exactly one row and each reached from price, volume and OBV, the rows that are the app's own being marked, the edges of the three readings, divergence in both directions (and when OBV confirms, falls short by less than the gap, or price makes no new extreme), the chart series, how the read joins the bull case and the critic, and the panel the real `web/app.js` draws (under Node). `tests/test_benchmarks.py` covers the industry comparison: the arithmetic and its edge cases (no positive multiple, outside the industry's range, a rank for a different figure, a thin sample), reading the CLI's answer (including the out-of-range and no-industry replies, taken from real output), the analysis with and without the feed, the cards and the Peer group panel the real `web/app.js` draws, and how the page loads the peers (the request it makes, a response for a stock the user has left, a failed one, and that a search does not wait for them), all run under Node (those tests are skipped when Node is not installed). `tests/test_peers.py` covers the peer group: the figures compared and their edge cases (negative equity, another currency, too few peers, ties), choosing peers (nearest in size, never the company's own other lines, using the real Hong Kong list), the store (freshness boundary, restart, an unreadable or unwritable file, threads), measuring a peer by the same code as the company, gathering with spares, what is and is not kept when a peer fails, reading the CLI's industry list, the statement-call spacing, and the service and `/api/peers` endpoint. `tests/test_twostage.py` covers the Two-Stage filter: the statement lines it reads (and that the existing debt figure is unchanged), the CLI answers it parses (the quarterly summary, splits and reverse splits, short interest in both markets, the adjusted close), every cut-off with its strict or inclusive edge, debt counted both ways and the check going UNKNOWN when leases decide it, ROIC and when it is not meaningful, the PEG bands, the split-adjusted three-month rise (a split in the window, dividends in Longbridge's series, a split the list does not know), the question scores and the totals table including question 4 pending and unknowns, framework fit, the endpoint and its failure modes, and the section the real `web/app.js` draws under Node (escaping, stored inputs becoming the request, the proposal, the concentration flag). `tests/test_pricehist.py` covers month arithmetic, splits, returns and pivots. `tests/test_layout.py` covers the page layout: that every tab has its button and its panel and every section sits in the tab the script names, what a click and the arrow keys do, and what each tab holds (the verdict before the evidence, the price levels under the fair value, the warnings above the tabs, and no blank tab when the reconciliation fails), against the real `web/index.html` and `web/app.js` (the behaviour under Node, with the harnesses `tabs.js` and `panels.js`).

---

## Suggested project layout

```
OptionScanner/
├── README.MD
├── skills-lock.json
├── .agents/skills/          # longbridge, longbridge-value-investing, longbridge-quant, longbridge-derivatives, two-stage-stock-filter
├── app/
│   ├── main.py              # FastAPI app and /api/analyze
│   ├── data.py
│   ├── reconcile.py
│   ├── buffett.py
│   ├── graham.py
│   ├── quant.py
│   ├── metrics.py           # key and additional metrics
│   ├── benchmarks.py        # P/E, P/B, P/S against the industry median; the peer medians
│   ├── peers.py             # the peer group: choosing, measuring and gathering peers
│   ├── peerstore.py         # the peers' figures, kept for days
│   ├── chart.py             # EMAs, volume profile, chart signals
│   ├── avwap.py             # anchored VWAP and the volume matrix
│   ├── obv.py               # OBV read: the 27-row table, divergence, chart series
│   ├── twostage.py          # the Two-Stage Stock Filter
│   ├── pricehist.py         # split adjustment, returns between dates, swings
│   ├── sessions.py          # exchange clocks: bar dates, session ends
│   ├── flows.py             # institutional buying/selling indicators
│   ├── news.py              # latest news and market mood
│   ├── quality.py           # published quality scorecards
│   ├── valuation.py         # app-built fair value estimate
│   ├── options.py           # chain/quote parsing, OCC symbols, Black-Scholes, IV metrics
│   ├── strategies.py        # strategy builders, payoff, breakevens
│   ├── option_scan.py       # view, filters, risk flags, multi-symbol scan
│   ├── snapshot.py          # per-ticker disk cache
│   ├── config.py            # bull/bear thresholds
│   ├── risks.py             # the critic: dangers and concerns with facts and figures
│   └── thesis.py
├── data/                    # everything in here is git-ignored
│   ├── iv_history.json      # daily ATM IV snapshots, builds the IV percentile over time
│   ├── peers.json           # the peers' computed figures, kept for 7 days
│   └── AAPL.US-20261020T143210+0800.json   # one cache file per ticker, kept for DISK_CACHE_MINUTES
├── tests/                   # test_app.py, test_options.py, test_valuation.py, test_cache.py, test_avwap.py, test_obv.py, test_sessions.py, test_benchmarks.py, test_peers.py, test_fcfe.py, test_twostage.py, test_pricehist.py, test_layout.py (+ cards.js, peers_flow.js, obv_card.js, filter_card.js, cache_note.js, tabs.js and panels.js, the Node harnesses)
└── web/
    ├── index.html           # single page: search box, the tab row, one panel for each tab
    ├── app.js
    └── styles.css
```

---

## API

The option endpoints (`/api/options`, `/api/options/payoff`, `/api/options/scan`) are described in [Option trade scanner](#option-trade-scanner), and `/api/filter` in [Two-stage stock filter](#two-stage-stock-filter).

When the data cache is on (live mode), every JSON response also carries `cache`, which says where its data came from:

```jsonc
"cache": { "symbol": "AAPL.US", "from_cache": true, "as_of": "2026-10-22T13:35:00+00:00", "age_seconds": 187200, "ttl_seconds": 604800,
           "market_as_of": "2026-10-24T04:49:00+00:00", "market_age_seconds": 720, "market_ttl_seconds": 3600, "live_calls": 0 }
```

`from_cache` is true when the cache file supplied any of the response's data. `as_of` is when the file was made (UTC), which is how old its fundamentals are (`age_seconds`), and `ttl_seconds` is how long they are kept. `market_as_of` is the oldest market data the file supplied to this response, or `null` when it supplied none because all of it was fetched for this response, and `market_ttl_seconds` is how long that is kept. `live_calls` counts the calls this response fetched and added to the file: market data that had gone stale, and calls the file did not hold. The page reads this only from the four requests a search makes (analyze, chart, flows, news): later requests such as a chart range or **Show more** read the file those just wrote, and would make a live search look like a cached one.

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
      "fcfe": { "ttm": 0, "basis": "ttm | fy", "ocf": 0, "capex": 0, "debt_issued": 0, "debt_repaid": 0, "net_borrowing": 0,
                "margin": 0, "yield": 0, "history": [], "applicable": true, "note": null },   // ocf + capex + debt_issued + debt_repaid = ttm
      "altman_z": 0, "piotroski_f": 0, "dividend": {}, "share_count_change_3y": 0,
      "insider_net": {}, "short_interest": {}, "beta": 0, "relative_strength": {}
    }
  },
  "benchmarks": {              // null when the industry feed has nothing; P/E, P/B and P/S only (see Industry comparison)
    "pe": { "median": 20.71, "low": 0.78, "high": 52.49, "company": 38.43, "times_median": 1.86, "better": false,
            "percentile": 0.74, "rank": 15, "n": 20, "outside": null, "thin": false }
    // "pb" and "ps" have the same fields. "outside" is "above" or "below" when the stock is beyond the industry's
    // range (no rank, so percentile, rank and n are null); "company" and "times_median" are null for a multiple that is not positive.
    // "better": true when the multiple is below the median (cheaper), false when above, null when equal or not comparable
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

`GET /api/peers?symbol=AAPL.US` (add `&refresh=true` to measure the peers again). The first search of an industry takes about 40 seconds; see [Industry comparison](#industry-comparison).

```jsonc
{
  "symbol": "AAPL.US", "status": "ok",          // "unavailable" comes with a "message" and the "excluded" list, and nothing else
  "ttl_days": 7.0, "min_peers": 3, "as_of": "2026-10-21T13:28:46+00:00",   // as_of: when the oldest peer's figures were fetched
  "company": { "operating_margin": 0.3317, "roic": 0.927 },                // the stock's own figures, 19 in all (benchmarks.PEER_METRICS)
  "peers": [ { "symbol": "SNDK.US", "name": "", "market_cap": 2e11, "fetched_at": "", "values": { "operating_margin": 0.616 } } ],
  "excluded": [ { "symbol": "DELL.US", "name": "", "reason": "its statements fail this app's checks: BS: current assets sum off by -15.6% (tolerance 2%)" } ],
  "metrics": {
    "operating_margin": { "company": 0.3317, "median": 0.0986, "q1": 0.05, "q3": 0.3, "n": 10,   // n: peers that have the figure
                          "diff": 0.2331, "ratio": 3.36, "above": 7,                           // above: peers the stock is above
                          "better": true, "higher_is_better": true }                           // the verdict the page colours by
    // the median, quartiles, diff, ratio, above and better are null when fewer than min_peers peers have the figure, or the stock has none;
    // better is also null when the stock equals the median. higher_is_better comes from benchmarks.HIGHER_IS_BETTER
  }
}
```

---

## Notes and limits

- Longbridge covers US, HK, CN (SH/SZ), SG and crypto, but coverage varies. Tested live: US and HK stocks work end to end. **A-shares (SH/SZ):** the statements endpoint returned `openapi error 2601400` for 600519.SH, so those symbols show the chart, flows and news but no metrics or scorecards. SG (D05.SG) returned no price in testing.
- The statements carry no EPS, share count or interest expense for some companies, so P/E uses the quoted TTM P/E, share-based items show n/a (or use market cap / price for the latest period, with a warning), and PEG growth comes from the consensus EPS estimate against trailing EPS.
- Debt includes short-term borrowings, the current portion of long-term debt, long-term debt and long-term leases.
- The kline API returns at most 1000 candles per request, so longer history is fetched in date-range chunks, forward-adjusted.
- **Daily bar dates.** Longbridge stamps a daily bar with local midnight written in UTC: the Hong Kong bar for Wednesday 21 October 2026 arrives as `2026-10-20T16:00:00Z`. The app used to take the date off the front of that string, so every Hong Kong, A-share and Singapore bar was dated a day early, and each Monday bar landed on a Sunday, which the chart's weekend gap hides: those charts had no Monday candles. Bars are now dated by their local trading day (`app/sessions.py`). That needs a time zone database; on Windows the `tzdata` package in `requirements.txt` supplies it, and without one the old dating is kept. The cache format version was bumped, so cache files written before the change are ignored. US bars (stamped 04:00 or 05:00 UTC) were never affected.
- **Cache format version 4.** The two-stage filter needs statement lines the app did not keep before (stock-based compensation, and the debt and lease lines apart) and the date of the consensus EPS, so `VERSION` in `app/snapshot.py` went from 3 to 4 and cache files written before are ignored once. The existing combined `st_debt` and `lt_debt` are unchanged, so Debt / EBITDA and the scorecards read the same.
- **Two lifetimes in the cache.** What changes by the hour is fetched again after `LIVE_CACHE_MINUTES`; everything else stays for `DISK_CACHE_MINUTES`. Three calls are market data for a reason other than how fast they move. `industry_valuation`: the page drops the industry rank unless Longbridge's own P/E, P/B or P/S for the company is within 10% of the figure on the metric card, so the two have to be as old as each other, or a week-old rank would vanish as the price drifted. `splits`: the unadjusted bars the two-stage filter reads are adjusted with this list, and a new split would leave fresh bars and an old list disagreeing by the split ratio. `earnings_date`: the answer is the next date from the day it was asked, so a week-old one has gone by. The CLI provider also used to hold `institution-rating` for as long as the process ran, which pinned the analyst ratings however often the cache fetched them again; it now holds it for a minute (`RATING_TTL`), long enough for the quote (industry name) and the analyst call of one search to share a fetch, and **Refresh data** clears it. The cache format version was not bumped: a file from before the per-call times were stored is read as every call being as old as the file.
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
- **Unavailable is not the same as none.** If the insider (Form 4) feed fails, for example from an SEC rate limit, the insider figures show n/a, the Buffett insider test shows n/a, and Graham says its insider adjustment was not applied. Before this, a failed lookup was read as "no insider selling", which removed Graham's penalty and passed the Buffett test; a genuinely empty feed (no trades) is still a real zero.
- Buffett star averages round half up (4.5 stars is 5); Python's default rounding sent 4.5 to 4 and 3.5 to 4.
- The CLI is limited to 10 API calls per second. The backend batches calls and caches them: in memory for 15 minutes at most, and per ticker on disk (fundamentals for `DISK_CACHE_MINUTES`, market data for `LIVE_CACHE_MINUTES`; see [Data cache](#data-cache-per-ticker)).
- The Graham and Buffett frameworks suit long-horizon investors. For a holding period under 3 years, the app shows a warning that these are not the right lenses.
- The verdict wording is neutral and factual (for example, "meets Graham NCAV criteria"). It is not a buy or sell recommendation.
- **Industry median for P/E, P/B and P/S; peer median for the rest.** The multiples use Longbridge's industry medians, the only ones the CLI offers. Margins, returns, growth and leverage are set against ten peers that the app measures with its own code (see [Industry comparison](#industry-comparison)). Longbridge's own peer table (`compare`) is not used for them because its definitions differ from the app's: for AAPL its P/E is 44.2 (last fiscal year's EPS) against the app's 38.4 (trailing twelve months), and its ROE is 148.8% against 171.4%. The `industry-rank` totals (revenue, net profit and their growth) are size-weighted, so a dominant company is mostly compared with itself (Apple is about three-quarters of its own industry's net profit). Checked live: the multiples on AAPL.US, MSFT.US, 700.HK and QUBT.US (a loss-maker), and the peer comparison on AAPL.US, MSFT.US, NVDA.US and 700.HK.
- **The Magic Formula peer ranks still show n/a.** That row is built by the main analysis request, which cannot wait 40 seconds for peers, and the peer group's figures are kept for later searches, not handed to it. They could feed it (the earnings yield and return on capital it ranks are computed by the same `metrics`/`quality` code), but the scorecard would then change between a first and a repeat search; that is a decision for later, not part of the peer comparison.

**Disclaimer:** For reference only. Not investment advice. Make investment decisions independently, based on your own risk tolerance.
