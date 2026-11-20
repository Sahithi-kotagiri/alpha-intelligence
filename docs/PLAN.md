# Plan and requirements checklist

Status key: ✅ complete and tested · 🟡 partial (limitation stated) · ⬜ outstanding.
"Tested" means covered by automated tests and/or verified on live data in this session (see EVALUATION.md).

## Architecture as built

```
Next.js (app router, TypeScript)  ── pages, charts, voice UI; no investment logic
   │ same-origin /api/* → runtime proxy route (streams SSE)
FastAPI ── auth, validation, background JobRunner (thread pool), scheduler (monitoring, performance)
   │
LangGraph workflow:  ingest → validate → compute ─┬─ fundamental ─┐
                                                  ├─ technical ───┤
                                                  ├─ news ────────┼→ devil's advocate → committee ⇄ review (≤2 rounds) → final → thesis
                                                  └─ risk/portfolio┘
   │
Deterministic core: providers (yfinance, SEC EDGAR, Wikipedia S&P 500) → normalisation/cross-check → validation
                    → tools (metrics, valuation, technicals, risk, portfolio, scenarios) → thresholds/peers → scores → evidence pack
LLM adapter (provider-agnostic; Anthropic implemented; FakeLLM for tests) — optional; rule-based mode is labelled
Persistence: SQLAlchemy + Alembic (SQLite locally, PostgreSQL tested); immutable versioned analyses
```

## Your additional requirements

| # | Requirement | Status |
|---|---|---|
| 1 | Cover all S&P 500 companies; maintain the list (refresh + change detection); search/select any stock; analyse individuals; screen the universe | ✅ (503 constituents; full screen 503/503 in 51 s) |
| 2 | yfinance for prices, company info, statements, valuation metrics; modular provider | ✅ (`MarketDataProvider` protocol; fixtures implement the same interface) |
| 3 | Revenue growth (annual/quarterly YoY, multi-year), EPS growth, PEG, trailing/forward P/E, D/E, plus margins, ROE, ROIC, FCF, liquidity, leverage, dilution, EV/EBITDA, P/FCF, moat/management proxies and more | ✅ (about 450 metrics per company; METHODOLOGY §4.5) |
| 4 | Context-aware, configurable thresholds by sector/industry/growth/business type, with explanations; documented methodology | ✅ (peer percentiles with per-metric fallback, sourced guardrails, business-type rules and overrides; every verdict explained) |
| 5 | Calculate from statements; show periods, formulas, sources, dates; flag unavailable/stale/inconsistent data; never invent values; cross-check against SEC | ✅ (tiered cross-check, restatements, splits, scale errors, multi-class shares) |

## Master prompt requirements

| Section | Item | Status |
|---|---|---|
| §4 journey | Ticker → identity confirmation → options → retrieval → validation → calculations → 4 analysts → Devil's Advocate → committee → one recommendation → save → watchlist/portfolio → monitoring → performance → revisit and ask | ✅ |
| §6 data | Identity, statements (annual/quarterly), EPS, shares, prices (adjusted), benchmark and sector ETFs, dividends/splits, news, SEC filings, analyst actions (vendor), estimates (labelled) | ✅ |
| | Segment data, guidance text, call transcripts, debt maturities | ⬜ not available from current free sources (stated in reports) |
| §6.3–6.4 | Provenance on every data point; validation rules; data-quality summary; confidence reduction | ✅ |
| §7 agents | Fundamental, Technical, News & Sentiment, Risk & Portfolio, Devil's Advocate; evidence-ID citations; fact/interpretation labels; missing info; schema-validated outputs | ✅ |
| | LLM interpretation running live | 🟡 implemented for Claude and OpenAI-compatible providers (free: Ollama, Gemini, Groq, OpenRouter); tested with a scripted LLM and mock HTTP servers, including a full graph run through the OpenAI-compatible adapter; **not yet run against a real model** |
| §8 committee | Agreement/disagreement, evidence tracing, time-horizon vs analytical, consistency checks, bounded review loop with log, unresolved questions preserved | ✅ |
| §9 final | Exactly one of 4 outcomes; confidence (not profit probability); thesis; risks; entry price (or explicit refusal); allocation (or required inputs); monitoring plan; executive summary; metadata | ✅ |
| §10 scoring | Rubrics with 0/5/10 meaning, weights, missing-data handling, direction, versions; decision gates, not averages | ✅ |
| §11 history and performance | Immutable versions, full snapshots, user comments/actions attributed; 21/63/126/252-session outcomes vs benchmark; hit rates; by category, score band and sector | ✅ |
| | By market regime | ⬜ (sample too small to be meaningful) |
| §12 thesis monitoring | Testable assumptions, materiality bands, statuses, events, alerts, versioned reassessment, history preserved, no continuous LLM runs | ✅ |
| | Reassessment routed to the relevant agent | ✅ each triggering change goes to the responsible agent (assumptions → Fundamental, trend/price → Technical, leverage → Risk, filings → News), which must address it; the committee then re-runs and a new version is saved |
| §13 portfolio | Weights, sector exposure, correlation, volatility, drawdown, beta, risk contributions, candidate impact, stress, constraints (excluded sectors, max position) | ✅ |
| | Currency exposure | 🟡 listing-currency only; revenue-currency exposure unavailable |
| §14 interface | Per-agent "Ask about this report" follow-ups; overview, analysis, committee, watchlist and alerts, thesis monitor, history and compare, portfolio, performance and evaluation, screener, settings and data sources; restrained design; dark/light; responsive | ✅ |
| §15 progress | Real persisted events, SSE with Last-Event-ID reconnect and polling fallback, failures with retry, waiting state | ✅ |
| §16 voice | Mic with states, editable transcript, briefing from the saved report, agent summaries, alerts and thesis changes read aloud on request, stop/pause/speed, voice on/off and selection, no autoplay, no audio storage, denial handling, text fallback, provider abstraction | ✅ (browser speech tested; OpenAI server adapter tested via mock transport, not live) |
| §17 charts | Revenue, EPS, margins, cash flow, debt and cash, valuation history, price + MAs + volume, RSI, relative performance, scenarios, weights, correlation; units, sources, dates, gaps not interpolated, table views, ask-about-chart | ✅ |
| | Texture fills for forced-colours/print | ⬜ |
| §18 assistant | Grounded answers with citations; fresh-data flag; history; voice or text | ✅ (LLM mode tested with scripted LLM; rule-based mode answers 8 question types) |
| §19 API | All listed endpoints plus universe, screener, metrics, evidence, compare, ask, monitoring, performance, evaluation, settings | ✅ |
| §20 database | All listed tables plus universe, screening, monitoring, assistant tables; FKs, indexes, uniqueness; migrations | ✅ (SQLite and PostgreSQL 16) |
| §21 security | Env config, no secrets in code/frontend, input validation, timeouts and retries, rate limits, caching, JSON logs, idempotency, prompt-injection fencing, token auth + isolation, retention/delete, security headers | ✅ |
| | TLS, encryption at rest, backups | 🟡 documented in DEPLOYMENT.md (deployment-time) |
| §22 evaluation | Unit, agent, committee, API, E2E, PostgreSQL; point-in-time backtest; single-LLM baseline harness; evaluation report | ✅ harness and tests · 🟡 baseline not run live (needs key) |
| §23 milestones | M0–M12 | ✅ (see above for the partial items) |
| Deployment | Dockerfiles + compose | 🟡 written; Docker unavailable here, so not executed |
