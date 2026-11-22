# Methodology

How every number, threshold, score and decision is produced. Versions recorded with each analysis:
`rulebook-2026.11-v2` (sets the label, §7), `thresholds-2026.11-v1`, `rubrics-2026.11-v1`, `decision-policy-2026.11-v1` (files in `backend/config/`).

## 1. Sources and source hierarchy

| Data | Source | Notes |
|---|---|---|
| S&P 500 constituents | Wikipedia "List of S&P 500 companies" table, cross-referenced with SEC CIKs | Official list is licensed by S&P DJI. Refreshed weekly; additions/removals are recorded. A partial table (<450 rows) is rejected. |
| Prices, company profile, statements, estimates, analyst actions, news | Yahoo Finance via `yfinance` | Unofficial client; not a licensed feed; personal-use terms. Cached (prices 6h, statements 12h, info 24h). |
| Filings and XBRL facts | SEC EDGAR (`company_tickers_exchange.json`, submissions, companyfacts) | Primary source. ≤8 req/s with identifying User-Agent. |

Priority when sources disagree: regulatory filing > vendor data (§2.4). Every retrieval is logged as
`ok / not_found / failed / skipped`; a failed fetch is never shown as a zero.

## 2. Normalisation (`backend/data/normalisation.py`)

1. **Field mapping.** Each canonical field maps to ordered yfinance line items and SEC us-gaap concepts.
2. **Periods.** Annual = 350–380-day duration; quarterly = 80–100 days. Fiscal labels come from the company's fiscal-year-end month, and 52/53-week years ending in the first week of a month count as the previous month.
3. **Quarters from year-to-date filings.** SEC 10-Q cash flows are cumulative, so Q2 = H1 − Q1, Q3 = 9M − H1 and Q4 = FY − 9M. Derived values are labelled `derived`. EPS derived by subtraction is flagged as approximate.
4. **Cross-check and source priority** (overlapping periods, end dates within ±10 days):
   - |diff| ≤ 1% → `match`; ≤ 5% → `minor_difference` (vendor value kept).
   - > 5% on *tier-1* fields (revenue, net income, EPS, operating income, gross profit, OCF, capex, assets, liabilities, equity, cash, current assets/liabilities, diluted shares) → `conflict`: the SEC value is used and the vendor value preserved.
   - > 5% on other line items (SG&A, D&A, cost of revenue, interest, buybacks…) → `definition_difference`: filers' XBRL tags and vendor definitions differ, so the vendor value is kept for cross-company consistency and the filing value is preserved.
   - Values that differ by ×10³ or ×10⁶ → `unit_scale_mismatch`, a tagging error in the filing. The vendor value is kept, and that field's SEC-only history is rescaled with a note.
   - Revenue: per period, the largest reported revenue concept is total revenue. ASC 606 "contract revenue" smaller than the vendor total is a definition difference, not a conflict.
   - Vendor-only values are `unverified`. SEC-only periods extend history (up to 10 years).
5. **Restatements.** The latest-filed value is used and the original preserved. A ratio near 2/3/4/5/7/8/10/15/20 is annotated as a likely split adjustment.
6. **Splits.** Per-share and share-count values from filings filed *before* a split, taken from yfinance's complete split history, are split-adjusted and annotated.
7. **TTM.** The sum of four consecutive quarters (spacing checked). Share counts are averaged, TTM EPS is the sum of quarterly EPS (noted), and TTM equals the latest fiscal year when that year ends at the latest quarter.

## 3. Validation and data quality (`backend/data/validation.py`)

The checks cover:

- identity mismatch, non-equity security, financial vs price currency mismatch
- failed sources, missing critical fields (revenue, net income, diluted EPS, OCF, equity, total assets) and important fields
- stale statements (latest period more than 140 days old) and stale prices (more than 5 days old)
- short price history, missing adjusted closes, invalid prices
- duplicate periods, conflicts, restatements, definition differences and scale errors

Status:

| Status | Rule | Confidence penalty |
|---|---|---|
| critical | any critical issue | −40% |
| poor | coverage under 70%, 4 or more warnings, or a conflict in a critical field in the two latest fiscal years | −20% |
| acceptable | any warning | −8% |
| good | none of the above | 0% |

`critical` status or a material conflict caps the label at Watchlist (rulebook gate).

## 4. Thresholds: how a metric passes or fails (`backend/analysis/assessment.py`)

Thresholds are **not** fixed numbers applied to every company. Each assessed metric goes through four steps.

### 4.1 Business type (deterministic, `classification.py`)

- **Financial:** GICS financial sub-industry. Balance-sheet financials such as banks and insurers are flagged.
- **REIT:** Real Estate sector.
- **Utility:** Utilities sector.
- **Growth:** 3-year revenue CAGR ≥ 15% (`high_growth` at ≥ 30%).
- **Cyclical:** Energy or Materials sector, or a listed cyclical sub-industry.
- Flags: `unprofitable` (TTM net income < 0) and `negative_equity`.

The business type decides which metrics apply. D/E, current and quick ratios, EV multiples, FCF metrics, interest coverage and Altman Z are not applicable to balance-sheet financials. Payout and Altman Z are not applicable to REITs. With negative equity, D/E, ROE and P/B are not meaningful.

### 4.2 Peer-relative band

Peers are S&P 500 members of the same GICS **sub-industry**. For each metric, if fewer than 5 peers have a valid value, the comparison falls back to the **sector** (minimum 8), then the **universe** (minimum 20, from the screen snapshot). The level used is recorded per metric.

Peer values are vendor-reported, so every company is measured the same way. Where available, the subject company's vendor value is used for the percentile, so the comparison is like for like. Our own calculation is shown alongside, with a note when the two differ by more than 15%.

Out-of-range values, such as negative or above-500 P/E, are excluded from distributions. The oriented percentile is the share of peers the company beats (lower is better for multiples). A percentile of 60% or more is **favourable**; below 30% is **unfavourable**.

### 4.3 Absolute guardrails (sourced heuristics)

| Metric | Favourable | Unfavourable | Rationale |
|---|---|---|---|
| PEG (forward, trailing) | ≤ 1.0 | ≥ 2.0 | Lynch (1989) fair-value heuristic |
| Revenue / EPS growth, 3–5y CAGR | ≥ 10% | ≤ 0% | Long-run nominal GDP / S&P 500 sales ≈ 4–6%, EPS ≈ 6–7% |
| ROIC | ≥ 15% | ≤ 8% | Heuristic bands (median US non-financial ROIC ≈ 10–15%). **Not** a cost-of-capital comparison: no defensible company WACC is available. |
| Interest coverage | ≥ 8x | ≤ 3x | Rating-agency bands |
| D/E: default / utility / REIT | ≤ 1.0 / 1.5 / 1.2 | ≥ 2.0 / 3.0 / 2.5 | Capital-structure norms by business model |
| Net debt/EBITDA: default / utility / REIT | ≤ 1.5 / 4.0 / 5.0 | ≥ 3.5 / 6.0 / 7.5 | Leveraged-finance bands |
| Piotroski F | ≥ 7 | ≤ 3 | Piotroski (2000) |
| Altman Z | ≥ 2.99 | ≤ 1.81 | Altman (1968) |
| Accruals ratio | ≤ 0% | ≥ 10% | Sloan (1996) |
| Share count change (1y) | ≤ 0% | ≥ 3% | Dilution of per-share value |
| FCF conversion | ≥ 0.9x | ≤ 0.6x | Quality of earnings |
| Current ratio | n/a | ≤ 0.8x | Liquidity warning |
| P/E vs own history (percentile) | ≤ 30% | ≥ 85% | Own fiscal-year-end P/E range |

The peer and absolute verdicts combine by sum (+1 / 0 / −1). A metric with neither reference is **informational**: shown, but not scored.

### 4.4 Business-type overrides

Each override is written into the explanation.

- **D/E high but serviceable** (coverage ≥ 8x and net debt/EBITDA ≤ 2x) → neutral.
- **Growth company with a premium P/E, forward P/E or EV/EBITDA** whose forward PEG is neutral or favourable → neutral ("premium justified by growth").
- **Cyclical company that looks cheap** while its TTM operating margin is more than 1σ above its multi-year average → neutral ("possibly peak earnings").
- **Unprofitable company:** net margin and ROE are forced unfavourable.
- **Low FCF conversion** with OCF/NI ≥ 1 and capex ≥ 15% of revenue → neutral ("investment phase").

### 4.5 Starting metrics requested, and their exact definitions

- **Revenue growth:** annual YoY (each year), quarterly YoY (quarter vs the same quarter a year earlier), quarter-on-quarter, and 3- and 5-year CAGR.
- **EPS growth:** diluted EPS on the same bases.
- **PEG:**
  - Forward PEG = trailing P/E ÷ (consensus next-FY EPS growth × 100).
  - Trailing PEG = trailing P/E ÷ (3-year diluted EPS CAGR × 100).
  - The vendor PEG is shown for reference only.
  - PEG is not meaningful when P/E is not meaningful or growth is ≤ 0.
- **Trailing P/E** = latest close ÷ TTM diluted EPS (statements). If statement EPS is inconsistent with the quoted share class (a ratio above 5x), vendor EPS is used and the reason noted.
- **Forward P/E** = latest close ÷ consensus next-FY EPS, labelled as an estimate.
- **Debt-to-equity** = total debt (including finance leases) ÷ shareholders' equity. It is cross-referenced with the vendor figure.

The full set also includes:

- **Margins:** gross, operating, net, EBITDA.
- **Returns:** ROE and ROA (on average denominators), ROIC (EBIT × (1 − effective tax, clamped to [0, 35%]) ÷ average (debt + equity − cash)).
- **Cash flow:** FCF (OCF − capex), FCF margin, conversion and growth, OCF/NI, capex intensity.
- **Liquidity:** current, quick and cash ratios, DSO, DIO.
- **Leverage:** net debt, net debt/EBITDA, debt/assets, interest coverage.
- **Dilution and capital allocation:** share change over 1 year and 3-year CAGR, SBC/revenue, payout, shareholder yield.
- **Quality scores:** Piotroski, Altman Z, accruals.
- **Moat proxies:** average and σ of gross/operating margin and ROIC over 5 years, consistency of revenue growth.
- **Valuation:** EV (with vendor cross-check), EV/EBITDA, EV/Sales, P/S, P/B, P/FCF, FCF yield, earnings yield, dividend yield, historical P/E at each fiscal-year end and the current percentile against it.
- **Market-dependent:** Altman Z.

## 5. Scores (`backend/config/rubrics.yaml`, `backend/analysis/scoring.py`)

All scores run 0–10, **higher = more favourable**; for risk, higher = lower risk. Verdict points are 1 (favourable), 0.5 (neutral) and 0 (unfavourable). Not-applicable and informational metrics are excluded. A dimension with under 50% coverage is dropped and the weights are renormalised; the drop lowers confidence. Rubric weights:

- **Business quality:** growth 25%, profitability 20%, returns 15%, health 15%, cash flow 15%, quality 10%.
- **Valuation:** multiples 45%, growth-adjusted 35%, own history 20%.
- **Fundamental agent score** = 0.65 × quality + 0.35 × valuation, shown only as a summary. The decision uses quality and valuation separately.
- **Technical** (rule points 1 / 0.5 / 0):
  - trend 35%: short, medium and long trend plus the 50/200-day cross
  - momentum 20%: RSI 50–70 constructive, above 80 or below 40 weak; MACD histogram positive and rising
  - relative strength 30%: 3/6/12-month vs benchmark and 6-month vs sector ETF, with a ±2% neutral band
  - volume 15%: whether volume confirms the trend; breakouts
- **Risk:**
  - volatility 25%: 1-year vol relative to the benchmark (≤ 1.2x low risk, ≥ 2x high)
  - drawdown 25%: 3-year max drawdown (≤ 25% low, ≥ 50% high) and opening gaps above 5%
  - leverage 25%: the assessment verdicts
  - market 15%: 2-year beta (≤ 1.1 low, ≥ 1.6 high) and downside capture
  - liquidity 10%: average dollar volume
- **News** = 5 + 5 × materiality-weighted net sentiment (high 3, medium 2, low 1; duplicates excluded). It requires LLM classification; there is **no keyword heuristic**.

## 6. Technical and event methods

- **Indicators:** SMA 20/50/100/200, RSI(14, Wilder), MACD(12/26/9), ATR(14, Wilder), realised volatility on log returns, drawdown, and relative strength on adjusted closes.
- **Support/resistance:** swing highs and lows (local extrema within ±10 sessions over 252 sessions), clustered within 1×ATR.
- **Entry zone:** the nearest support zone within 15% below the price, and only when the long-term trend is not down.
- **Breakout:** a close above the prior 50-session closing high. It is *confirmed* after 3 closes above with volume ≥ 1.5× the 50-day average.
- **8-K items** map to materiality, for example:
  - **High:** 1.03 bankruptcy, 1.05 cybersecurity, 2.04 acceleration, 2.06 impairment, 3.01 delisting, 4.01 auditor change, 4.02 non-reliance, 5.01 change of control.
  - **Medium:** 1.01, 2.02, 5.02.
  - **Low:** 7.01, 8.01, 9.01.

## 7. Team rulebook and decision (§R; `backend/config/rulebook.yaml`, `backend/analysis/factors.py`)

The label is set by one versioned rulebook (`rulebook-2026.11-v2`). The same formulas are applied to the company being
analysed and to every S&P 500 constituent in the universe screen, so the subject is ranked against peers measured
identically. Agents interpret; they cannot raise the label.

### 7.1 Four pillars (25% each), scored 0–100 as sector percentiles

| Pillar | Measures (equal weight within the pillar) | Source |
|---|---|---|
| Quality | ROIC 5-year average = operating income × (1 − tax rate, clamped 0–35%) ÷ (total assets − current liabilities − cash); ROIC stability = standard deviation of the yearly ROIC (lower is better); FCF conversion = Σ(OCF − capex − SBC) ÷ Σ net income over 3 years | SEC 10-K XBRL |
| Value | FCF yield = latest FY FCF ÷ market cap; growth gap = achieved revenue CAGR (up to 5 years) − growth implied by today's EV (reverse DCF on the same normalised FCF base as the fair value); P/E percentile within the company's own 10-year history (split-adjusted SEC EPS × month-end close; lower is better) | SEC + Yahoo prices |
| Momentum | 12-month return excluding the most recent month (adjusted closes); trend = average of the scores for price ÷ 200-day average and the 1-month slope of that average | Yahoo prices |
| Revisions | 90-day change in consensus EPS (this and next fiscal year); average surprise of the last four reports (clipped ±50%) | Yahoo consensus (vendor estimates) |

- **Percentile basis:** same GICS sector with at least 8 values; otherwise the S&P 500 (≥ 20 values); otherwise documented absolute anchors in `rulebook.yaml`. The basis and n are shown per measure.
- **Missing data:** missing measures are skipped. If more than half of a pillar's applicable measures are missing, the pillar counts as 50 and confidence falls. Two such pillars → *Insufficient data* (recorded as Watchlist, no buy or reject call).
- **Financials** (banks, insurers): ROE replaces ROIC and earnings yield replaces FCF yield; FCF conversion and the reverse DCF are *not applicable* (not counted as missing).
- **Composite** = average of the four pillars.

### 7.2 Fair value, buy-below and size

- **Fair value** = mean of (a) a two-stage 10-year DCF on normalised FCF after SBC (the 3-year average, so a peak year does not set the base; the latest year if fewer than 3): the 5-year revenue CAGR (clamped to −5%…20%) for years 1–5, fading linearly to the 2.5% terminal growth by year 10, discount rate 9%, less net debt, per share; and (b) TTM EPS × the company's own median P/E over 10 years. Methods that cannot be computed are left out. When the two methods differ by more than 2×, the fair value is flagged as low precision in the report and the screener.
- **Buy-below** = fair value × 0.85 (× 0.90 when quality ≥ 90).
- **Position size:** 5% for Strong Buy, 3% for Buy; halved when 1-year volatility exceeds 1.5× the S&P 500's, and halved again when the 3-year maximum drawdown exceeds 40%; capped by the mandate's maximum position.

### 7.3 Labels (first matching rule)

1. **Reject:** composite < 40 or quality < 25.
2. **Strong Buy:** composite ≥ 85, every pillar ≥ 50, price ≤ fair value.
3. **Buy:** composite ≥ 75, no pillar < 25, price ≤ fair value × 1.05.
4. **Wait:** quality ≥ 70 but value < 50, momentum < 40, or price above fair value (alert at buy-below).
5. **Watchlist:** otherwise (composite 40–75).

### 7.4 Gates (can only lower the label)

| Gate | Cap |
|---|---|
| 8-K item 4.01 auditor change, 4.02 non-reliance, 2.06 impairment or 1.03 bankruptcy in the last 90 days | Reject |
| 8-K item 3.01 listing notice in the last 90 days (often a transfer between exchanges; read the filing) | Wait |
| Net debt/EBITDA above the sector limit (4×; Utilities 6.5×, Real Estate 8×) or EBITDA ≤ 0, **and** negative FCF (non-financials) | Reject |
| Data quality poor or critical, or core financials missing | Watchlist |
| Stock split in the last 30 days, or a vendor trailing P/E below 2 (per-share data likely on different share bases) | Watchlist |
| Diluted share count rising > 5% a year (2-year CAGR; split-sized jumps ignored) | Watchlist |
| Consensus EPS falling over 90 days **and** price below a falling 200-day average | Wait |
| Portfolio breach (excluded sector, position above maximum) | Wait |
| Unresolved high-severity Devil's Advocate risk; other high-materiality 8-K (cyber, acceleration, change of control) | Wait |
| Devil's Advocate has no credible, evidenced answer to "what does the market believe, and why are we different?" | Buy (no Strong Buy) |

The market-belief answer is deterministic without an LLM: the reverse-DCF growth is what the market believes; the
difference is credible only if achieved growth exceeds it and estimates are not falling (revisions pillar ≥ 50).

### 7.5 Committee

1. Rule-based disagreement detection between the analysts (quality vs valuation, fundamentals vs trend, news vs cash flow, quality vs risk, Devil's Advocate vs bullish analysts) and consistency checks (price date, reporting period, benchmark, currency).
2. Bounded review: at most two rounds, up to three questions each, answered with cited evidence or "cannot resolve". A resolved bear-case question removes one unresolved high-severity risk.
3. The rulebook label is computed with the committee-time gates. With an LLM, the committee may choose only that label **or a more cautious one**, with a cited reason stored next to the rulebook label.

### 7.6 Confidence (evidence quality, not probability of profit)

85%, minus 8% for acceptable data or 20% for poor/critical data, 10% per pillar with more than half its measures missing,
10% if pillars disagree sharply (one ≥ 80 and another ≤ 20), and 10% without an LLM review; clamped to 10%–90%.

### 7.7 What every recommendation shows

Label, composite and pillars (with each measure's value, score, percentile basis and n); the top two reasons for and
against; fair value, its methods, buy-below and the price date; position size; the market belief and our difference;
the Devil's Advocate's top three risks; what would change the call; confidence with its reasons; review triggers
(close below the 200-day average, consensus EPS falling over two months, any thesis assumption At risk).

The earlier 0–10 agent scores (§5) are still computed and shown as diagnostics for each analyst; they do not set the label.

## 8. Portfolio analytics (`backend/tools/portfolio_metrics.py`)

- **Inputs:** weights from market value or quantity × last close, or user-supplied weights (percentages are accepted). Missing prices are listed, never guessed.
- **Return basis:** daily simple returns on adjusted closes over 252 sessions.
- **Risk measures:**
  - volatility = √(wᵀΣw)·√252 at constant weights
  - drawdown from a constant-weight, daily-rebalanced NAV
  - beta by OLS against the benchmark
  - risk contributions wᵢ(Σw)ᵢ/σ²
- **Concentration:** HHI and effective positions (1/HHI), plus sector exposure.
- **Candidate impact:** the candidate is added at an illustrative weight, funded proportionally.
- **Stress:** beta × market move (linear).

Historical correlation does not guarantee future correlation.

## 9. Thesis extraction and monitoring (`backend/intelligence/thesis.py`, `monitor.py`)

**Assumptions** come from the analysis's own assessments: revenue and EPS growth, operating and FCF margins, ROIC, net debt/EBITDA, trailing P/E and the 200-day trend. Each has a comparator plus review and at-risk thresholds:

| Metric | Review threshold | At-risk threshold |
|---|---|---|
| Growth (positive) | 75% of original | 50% of original |
| Growth (≤ 0) | original − 2.5pp | original − 5pp |
| Operating / FCF margin | −2.5pp | −5pp |
| ROIC | 85% of original | 70% of original |
| Net debt/EBITDA | +0.5x | +1.0x |
| Trailing P/E | ×1.10 | ×1.25 |
| Price vs 200-day average | −5% (or −8pp) | −10% (or −15pp) |

LLM-identified qualitative assumptions, such as a moat rating, are stored for manual review.

**Status:** `Improving`, `Valid`, `Review`, `At risk`, `Invalidated` (beyond the at-risk threshold by at least two bands) or `Unknown`. Movement inside the review band never changes status.

**Material triggers:** an assumption moving to At risk or Invalidated, a new reporting period, a high-materiality 8-K, or a price move of more than 15%. Each trigger produces a deduplicated event and an alert (subject to the watchlist's alert preferences), then a **new versioned analysis** (`trigger=monitoring`, linked to its parent). There is a 3-day cooldown per ticker. Old theses are superseded, never overwritten.

## 10. Performance tracking (`backend/intelligence/performance.py`)

- **Entry:** the adjusted close on the decision's price date (no look-ahead).
- **Horizons:** 21, 63, 126 and 252 trading sessions.
- **Measures:** total return (adjusted close as a total-return proxy), benchmark return, excess return, max drawdown and volatility.
- **Hit rate:** for Strong Buy and Buy, excess return > 0; for Reject, excess return < 0. Wait and Watchlist are reported but not hit-scored. Results are also grouped by composite band (< 50, 50–75, ≥ 75).
- **Groupings:** recommendation, quality band, sector and user action.
- With fewer than 30 observations in a group, no significance is claimed.
