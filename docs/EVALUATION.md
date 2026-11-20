# Evaluation report

Generated 2026-11-20. Model/provider: none configured at evaluation time (rule-based mode); configured default
`claude-opus-5-5` via the Anthropic adapter. Versions: `thresholds-2026.11-v1`, `rubrics-2026.11-v1`,
`decision-policy-2026.11-v1`, prompts `*-v1` (SHA-256 recorded per model run).

## 1. Automated tests

| Suite | Result |
|---|---|
| Backend unit + integration (`uv run pytest`) | **108 passed**, 1 skipped (live) — SQLite |
| Backend API + service integration on PostgreSQL 16 (`TEST_DATABASE_URL=… pytest backend/tests/test_api.py backend/tests/test_services_extra.py`) | **29 passed**, 1 skipped (live) |
| Live provider test (`RUN_LIVE=1`) | **passed** (yfinance, SEC, Wikipedia) |
| Browser E2E (`cd frontend && npx playwright test --trace off`) on synthetic fixtures | **6 passed** |
| Fresh clone (`git clone` → `cp .env.example .env` → `uv sync` → start → live MSFT analysis) | **completed** |
| Line coverage (`pytest --cov=backend`) | **89 %** (live adapters 20–56 %: exercised by live runs below, not offline tests) |
| Lint / type-check | `ruff` clean; `tsc --noEmit` clean; `next build` succeeds |

What the tests cover (selected):

- **Calculations.** Growth, CAGR, and growth from a negative base. Tax-rate clamp, ROIC, Piotroski and Altman Z checked against known values. FCF, negative equity, TTM, SMA/EMA/RSI/MACD/ATR, drawdown, beta (the 2× levered series gives exactly 2.0), capture ratios, VaR, portfolio weights (including percentages), volatility, risk contributions, candidate impact and scenarios.
- **Data.** YTD → quarterly derivation, restatement plus split hint, point-in-time filtering, conflict → SEC priority, unit-scale mismatch, definition differences, revenue max rule, split adjustment, missing ≠ zero, stale/failed/non-equity validation, and a partial S&P 500 table being rejected.
- **Thresholds and decisions.** Peer percentiles, per-metric peer fallback, PEG guardrails, serviceable-leverage override, utility bands, bank not-applicable, growth P/E override, informational verdicts, rubric coverage. Policy cases: strong-but-expensive → Wait, weak technicals → Wait not Reject, weak quality → Reject, a blocked Buy falling back, missing scores, divergent entry methods, allocation requiring inputs.
- **Agents (scripted LLM).** Only supplied evidence IDs survive (one retry, then removal). LLM failure gives a labelled fallback. The committee cannot choose a gated recommendation, and an override needs a reason. The review loop is bounded. Untrusted text is fenced. News is deduplicated and the news score formula checked. The Anthropic request shape is checked (model, structured output, adaptive thinking, effort, server fallbacks, base URL pinned against `ANTHROPIC_BASE_URL`), as is refusal handling.
- **API.** The full journey, real ordered events and SSE replay from `Last-Event-ID`, evidence and provenance, the briefing, the rule-based assistant, comments, versioning and comparison, idempotency, provider outage (critical status and Buy Now blocked), unknown ticker with retry, the portfolio constraint gate, watchlist and alerts, and alert preferences. Thesis monitoring detects margin collapse plus an 8-K 4.02, triggers a reassessment that cannot recommend Buy Now, and does not repeat inside the cooldown. Also covered: performance horizons, the baseline requiring an LLM, voice returning 501 without a provider (and rejecting arbitrary text), the OpenAI voice adapter via a mock transport, and settings/delete-my-data. Token auth: 401 without a token, user A's data invisible to user B, logout invalidating the token.
- **E2E (Chromium).** Search → confirm → run → research log → evidence drawer (Escape closes it) → ask about a metric → committee → data quality → assistant. No speech autoplay, transcript visible, a Stop control. Microphone denial falls back to typing. Screener, no horizontal overflow at 390 px, visible keyboard focus.

Added after the first local install attempt:

- A `.gitignore` bug that excluded `backend/data/` from the repository is fixed.
- The SQLite folder is now created before migrations run on a fresh checkout.
- An OpenAI-compatible LLM adapter has 7 tests: request shape, fallback from `json_schema` to `json_object`, stripping reasoning tags and code fences, a corrective retry, the invalid-output fallback, rate-limit retry, and a full graph run through it.
- Monitoring now routes each reassessment trigger to the responsible agent.

## 2. Live verification (real data, rule-based mode)

| Ticker | Business type | Result | Observations |
|---|---|---|---|
| MSFT | mature | Wait (70%) | 251 vendor/SEC matches, 0 conflicts. FCF metrics weak vs peers (AI capex); the conversion override applied. |
| NVDA | growth, cyclical | Wait | 3 genuine conflicts, e.g. FY2023 operating income: Yahoo normalised $5.58B vs 10-K GAAP $4.22B, SEC used. |
| AAPL | mature | Wait (65%) | Valuation 0/10 vs hardware peers. Entry methods diverge ($345 support vs $102–210 valuation), so no entry range is quoted. FY2017 EPS split-adjusted to $2.30. |
| JPM | balance-sheet financial | Wait | 8 leverage/working-capital metrics marked not applicable. |
| PLD | REIT | Wait | Definition differences on operating income recorded, not treated as conflicts. |
| NEE | utility | Wait (70%) | Utility leverage bands applied. ASC 606 contract-revenue subtotal not allowed to override total revenue. |
| XOM | cyclical | Watchlist | Cheap-at-peak rule evaluated. |
| MCD | negative equity | Buy Now (70%) | D/E, ROE and P/B not meaningful. A diluted-share XBRL scale error (741.3 vs 741.3M) was detected and corrected. |
| INTC | cyclical, unprofitable | Reject | P/E and PEG not meaningful. |
| BRK.B | financial, multi-class | Watchlist | Multi-class market cap detected (price × shares off by 34%), vendor EPS for the quoted class. Data quality poor because of genuine revenue-definition conflicts (vendor includes investment gains). |
| COST | mature | Wait | P/E 52.7x and PEG 5.2x unfavourable. |
| S&P 500 screen | — | 503/503 in 51 s | Sector percentiles for all constituents. |

Robustness fixes made because of these live runs:

- conflict tiers and definition differences
- detection of XBRL unit-scale errors
- the ASC 606 revenue rule
- multi-class share handling
- split adjustment from the full split history
- refusing to quote an entry range when methods disagree
- a race when creating the local user
- the API base URL leaking from `ANTHROPIC_BASE_URL`
- portfolio sectors read before the universe was loaded

## 3. Point-in-time backtest (`scripts/run_backtest.py`, `docs/evaluation/backtest.json`)

60 random current constituents (seed 7), annual rebalance dates 30 June 2018 to 30 June 2025. Features use
only SEC filings **filed on or before** each date. Composite = mean percentile rank of revenue growth, operating margin
and earnings yield. Forward 252-session excess return vs SPY, 10 bps per side.

| Tercile | n | Mean excess | Median excess | Hit rate |
|---|---|---|---|---|
| low | 110 | +26.0% | −4.9% | 44.5% |
| mid | 105 | −0.7% | −4.1% | 45.7% |
| high | 106 | +1.2% | −0.2% | 49.1% |

**Interpretation:**

- The simple composite shows **no reliable edge**. Medians and hit rates rise slightly from the low to the high tercile, but the low tercile's mean is dominated by a few extreme winners.
- 321 observations from 60 companies are far too few, and survivorship bias inflates every tercile (current constituents only). Nothing here supports a claim that the scores predict returns.
- The platform's scores are diagnostic summaries, not validated signals. Any calibration of thresholds needs a much larger, survivorship-free universe and a dataset that keeps delisted companies, which needs a licensed provider.

## 4. Single-LLM baseline comparison

Implemented: `POST /api/evaluation/baseline` and the Performance page. It is tested with a scripted LLM: a baseline citing a non-existent ID scores 0 citation validity, and an invented multiple is flagged as an unverified number. **It has not been run live**, because no Anthropic API key is configured in this environment. Until it has run on many analyses, there is no evidence that the multi-agent design improves accuracy over a single prompt. If it does not, simplify to a single interpretation call over the same deterministic core, keeping the deterministic engine, evidence pack, guardrails and gates, which provide most of the value.

## 5. Known failure modes and limitations

- **Peer groups.** GICS sub-industries can be poor comparators: Apple against commodity hardware makers, Berkshire against insurers. The fallback is per metric and documented, but it does not choose economic peers.
- **Vendor data.** yfinance is unofficial and unlicensed, and endpoints can change. Peer metrics use Yahoo's definitions. Statement history from yfinance covers about 4–5 years; older history comes from SEC.
- **XBRL tagging varies by filer.** Secondary line items are not cross-checked strictly (definition differences). Debt maturity schedules, segment data, backlog and customer concentration are not extracted.
- **Rule-based mode.** Without an LLM there is no news sentiment, no moat or management interpretation, and no thesis-failure estimate; confidence is reduced. The rule-based assistant answers eight question types and says so otherwise.
- **Numeric guardrail.** It is a tolerant detector, matching within 2% of any evidence value at common scales. It can miss a fabricated number that coincides with an evidence value.
- **Scenarios** are illustrations under stated assumptions; the market stress test is linear in beta.
- **Not implemented:**
  - performance by market regime (sample too small)
  - forced-colours texture fills in charts
  - email or push alert delivery (in-app only)
  - fine-grained per-agent reassessment (monitoring re-runs the full versioned analysis)
