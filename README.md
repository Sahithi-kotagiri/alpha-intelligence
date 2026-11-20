# Investment Intelligence Platform

Evidence-led research for S&P 500 equities: deterministic financial analysis, five specialist analyst agents, a Devil's
Advocate, an investment committee with decision gates, persistent theses with monitoring, portfolio-aware assessment,
recommendation tracking, and a restrained web research terminal with optional voice.

> Not investment advice. Scores and thresholds are analytical conventions that have not been statistically validated (see
> `docs/EVALUATION.md`). Market data comes from yfinance, an unofficial Yahoo Finance client intended for personal use.

## Quick start

Requires Python 3.12+, [uv](https://docs.astral.sh/uv/) and Node 20+.

```bash
uv sync
```
```bash
cp .env.example .env
```

Edit `.env` and set at least `SEC_USER_AGENT="Your Name you@example.com"` (SEC requires a real contact).

**Optional LLM interpretation.** Without an LLM, the platform runs in labelled rule-based mode: every number, threshold,
score and decision still works. To add interpretation (news sentiment, moat, management, bear case, open-ended
questions), choose one:

- **Claude (paid, best quality):** `LLM_PROVIDER=anthropic`, `ANTHROPIC_API_KEY=...`
- **Free, local (Ollama):** install Ollama, run `ollama pull qwen3:8b`, then set `LLM_PROVIDER=openai_compatible`,
  `LLM_BASE_URL=http://localhost:11434/v1`, `LLM_MODEL=qwen3:8b`. Needs about 8 GB RAM.
- **Free tiers (Gemini, Groq, OpenRouter):** see `.env.example`. These are rate-limited, and free-tier data may be used by the provider.

Smaller free models follow instructions less reliably. Every reply is schema-validated and citation-checked, and any
agent whose output is still invalid after one retry falls back to its rule-based report and says so.

```bash
uv run uvicorn backend.app.main:app --port 8000
```
```bash
cd frontend && npm ci && npx next build && npx next start -p 3000
```

Open http://localhost:3000, choose **New analysis**, search a company, confirm it, and start. Progress shown is the real
backend state. Use **S&P 500 screener → Refresh universe metrics** once (about 1 minute) to screen all constituents.

## How it works

1. **Data:**
   - S&P 500 constituents come from the maintained public table (refreshed weekly, with changes recorded).
   - Prices, statements, estimates and news come from yfinance; filings and XBRL facts from SEC EDGAR.
   - Every value carries source, period, filing date and retrieval time.
   - Vendor statements are cross-checked against SEC filings: conflicts use the filing, and definition differences and XBRL scale errors are detected.
   - Splits, restatements and multi-class shares are handled.
2. **Deterministic core:**
   - About 450 metrics are calculated from the statements.
   - Each is judged against its GICS sub-industry peers (with per-metric fallback to sector or universe), sourced guardrails such as PEG ≈ 1 and Altman zones, and business-type rules for banks, REITs, utilities, growth, cyclical, unprofitable and negative-equity companies.
   - Every verdict comes with a written explanation. See `docs/METHODOLOGY.md`.
3. **Agents (LangGraph):**
   - Fundamental, Technical, News & Sentiment, and Risk & Portfolio run in parallel, then the Devil's Advocate, then the Investment Committee with a bounded review loop, then the Final Decision.
   - Agents cite only evidence IDs they were given; guardrails remove invalid citations and flag unverified numbers.
   - Without an LLM, every agent produces a clearly labelled rule-based report.
4. **Decision:** exactly one of Buy Now / Wait / Watchlist / Reject, chosen through explicit gates and a documented policy, never an average. Confidence reflects evidence sufficiency, not the probability of profit.
5. **After the decision:**
   - The thesis is saved as monitorable assumptions with materiality bands.
   - Scheduled monitoring records events and alerts and starts a versioned reassessment on material change.
   - Outcomes are tracked at 1, 3, 6 and 12 months against the benchmark.

## Repository layout

```
backend/
  app/            FastAPI app, config, auth, API routes
  data/           providers (yfinance, SEC EDGAR, S&P 500), cache, normalisation, validation, ingestion
  tools/          deterministic metrics, valuation, technicals, risk, portfolio, scenarios
  analysis/       peers, classification, thresholds/assessment, scoring, evidence pack, decision rules
  agents/         LLM adapter, guardrails, five agents, committee, final decision
  orchestration/  LangGraph workflow and compute stage
  intelligence/   thesis, monitoring, performance, evaluation/backtest
  services/       jobs, events, universe/screener, assistant, briefing, compare
  voice/          speech provider adapters
  config/         thresholds.yaml, rubrics.yaml, decision_policy.yaml (versioned)
  prompts/        versioned agent prompts
  database/       models, session, Alembic migrations
  tests/          unit, agent, API tests and synthetic fixtures
frontend/         Next.js app (pages, components, hooks, E2E tests)
scripts/          fetch_company.py, run_backtest.py, run_fixture_server.py
docs/             PLAN (requirements checklist), METHODOLOGY, EVALUATION, DEPLOYMENT
```

## Tests

```bash
uv run pytest
```
```bash
RUN_LIVE=1 uv run pytest -k live
```
```bash
TEST_DATABASE_URL=postgresql+psycopg://user@host/db uv run pytest backend/tests/test_api.py
```
```bash
cd frontend && npx playwright test --trace off
```

Current results: 108 backend tests pass, 29 pass on PostgreSQL 16, 6 browser E2E tests pass, and the live provider test
passes. Details are in `docs/EVALUATION.md`.

## What is real and what is not

| Capability | State |
|---|---|
| S&P 500 universe, screening, search | Live |
| yfinance prices/statements/info/news/estimates, SEC EDGAR filings and XBRL | Live (cached) |
| All calculations, thresholds, scores, gates, decisions, thesis, monitoring, performance | Live, deterministic |
| LLM agent interpretation, LLM committee, LLM assistant, single-LLM baseline | Implemented for Claude and any OpenAI-compatible API (Ollama, Gemini, Groq, OpenRouter); tested with a scripted LLM and mock HTTP servers; **not yet run against a real model** |
| Voice | Browser speech (on-device) works without keys; OpenAI server adapter needs `OPENAI_API_KEY` |
| Synthetic fixtures | Used only by tests and `scripts/run_fixture_server.py`; names marked "(synthetic)" |

See `docs/DEPLOYMENT.md` for Docker Compose, security hardening, backups and costs.
