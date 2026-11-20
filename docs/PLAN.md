# Project plan

## 1. Product in one paragraph

You type a US ticker and the system first confirms which company it is. It then gathers primary-source evidence: SEC filings, XBRL financials, daily prices and dated news. Every data point carries its source, date and period. Python calculates every number. Four specialist analysts and a Devil's Advocate interpret only the evidence they are given and cite it by ID. A committee then adjudicates their disagreements under explicit decision gates and issues exactly one of Buy Now / Wait / Watchlist / Reject. Everything is saved immutably as a versioned analysis with testable thesis assumptions. Those assumptions are later monitored, and the recommendation is scored against a benchmark. The interface is a calm research terminal, not an AI demo.

## 2. Architecture (smallest practical version)

```
 Frontend (Next.js, Milestone 10)  ── shows results, no investment logic
        │  REST + SSE (real progress events)
 FastAPI API  ── starts jobs, returns IDs, streams events
        │
 Orchestrator (LangGraph)  ── order, state, retries, bounded review loop (max 2 rounds)
   │            │                     │
 Agents       Tools (deterministic)  Intelligence (thesis, monitor, performance)
 (LLM, typed   ratios, valuation,     
  outputs)     indicators, risk)      
   │            │
 Evidence pack ◀── Validation ◀── Normalisation ◀── Provider adapters (SEC, prices, news)
        │
 Database (SQLite → Postgres via SQLAlchemy + Alembic): immutable snapshots
```

**Layers in plain English**
- **Provider adapters** only fetch data and record where it came from. Each provider sits behind a common interface, so it can be swapped out.
- **Normalisation/validation** puts units, periods and currencies on one footing. It also flags missing, stale and conflicting values and produces a data-quality report.
- **Tools** are plain Python functions with unit tests. They return a value plus its formula and input IDs. No LLM touches arithmetic.
- **Evidence pack**: every validated datum or calculation gets an ID such as `ev_fin_0012`. Agents may cite only IDs that exist in the pack they received. A validator rejects any output that cites an unknown ID.
- **Agents** are Python classes sharing one `Agent` base: role, prompt (versioned file), input-scope filter, and a Pydantic output schema. They call an `LLMClient` interface, never a vendor SDK directly. That interface is what keeps the provider replaceable. Agents are testable with a fake `LLMClient` that returns fixed JSON.
- **Orchestrator**: LangGraph is a library for writing the workflow as a graph of steps with shared typed state. Its value here is the explicit fan-out (four analysts in parallel), the Devil's Advocate dependency, and a *bounded* committee→agent clarification loop. If it adds no value over plain `asyncio`, we keep plain Python.
- **Committee/Final decision**: hard decision gates live in versioned config. One example: a critical data-quality failure blocks Buy Now. These gates are applied in code *before and after* the LLM synthesis. Scores are never averaged.

## 3. Data providers and credentials

| Need | First choice | Cost / licensing | Credential |
|---|---|---|---|
| Identity, filings, financials | SEC EDGAR (`company_tickers.json`, submissions, XBRL companyfacts) | Free; public-domain data; ≤10 req/s; must send identifying User-Agent | `SEC_USER_AGENT` (name + email). **Host access enabled in this environment.** |
| Daily prices | **Decision needed**. See section 7 | Varies | Depends on choice |
| News | Phase 1: SEC 8-K filings (primary source, free). Later a licensed news API if you want one | Free / paid | None for 8-K |
| LLM | Anthropic first, behind `LLMClient` | Pay-per-token | `ANTHROPIC_API_KEY` (from Milestone 3) |
| Estimates / analyst ratings | None initially. Forward P/E etc. shown as *unavailable* | Licensed only | — |

**Buildable with no paid API:** identity, all financial statements available in XBRL, filings and 8-K events, deterministic metrics, validation, database and history, portfolio maths, UI. The LLM is the first unavoidable cost.

## 4. Main risks

- **Licensing:** free price sources (Yahoo, Stooq) are not licensed for commercial redistribution. That is fine for personal research but not for a commercial product.
- **XBRL quality:** tags vary by company (e.g. `Revenues` vs `RevenueFromContractWithCustomer...`). Quarterly Q4 values often have to be derived as FY − 9M. Restatements also appear. This needs careful period logic and tests.
- **LLM reliability:** fabricated figures and citations. This is mitigated by evidence-ID validation, schema validation, and keeping numbers out of the LLM's hands.
- **Cost:** about 7 LLM calls per analysis plus review rounds. We'll log tokens per run (`model_runs`) and report the cost.
- **Security:** portfolio data is personal. Local single-user until Milestone 12 adds authentication. No raw audio is stored.
- **Overclaiming:** performance stats will report sample sizes and avoid any significance claims.

## 5. Assumptions recorded

- US-listed common stock, daily (EOD) data, default benchmark SPY, default horizon 12 months.
- SQLite locally via SQLAlchemy + Alembic, so moving to Postgres is a config change.
- Background jobs: an in-process asyncio worker with a persisted job table to start. Move to a real queue (e.g. arq/Redis) only when needed.
- Python packaging with `uv`; backend package lives in `backend/`.

## 6. Milestone checklist

- [x] **M0** Repo inspection, plan, runnable skeleton (FastAPI health endpoint, config, tests, lint)
- [ ] **M1** Ticker → company resolution (SEC), XBRL company-facts adapter, price adapter, normalisation + provenance model, validation + data-quality report, save sample company data
- [ ] **M2** Deterministic financial tools + tests
- [ ] **M3** LLM abstraction + Fundamental Analyst
- [ ] **M4** Technical indicators + Technical Analyst
- [ ] **M5** 8-K/news adapter + News & Sentiment Analyst
- [ ] **M6** Risk metrics, portfolio inputs + Risk & Portfolio Analyst
- [ ] **M7** Devil's Advocate, orchestration graph, committee, final decision
- [ ] **M8** Persistence of versioned analyses and theses
- [ ] **M9** Thesis monitoring, performance tracking, alerts
- [ ] **M10** Frontend shell, core screens, SSE progress, charts
- [ ] **M11** Design polish and voice
- [ ] **M12** Auth, deployment, evaluation report (incl. single-LLM baseline)

### Milestone 1 tasks (proposed)
1. `data/providers/base.py`: provider interface and `ProviderError` vs `NotFound` (to tell an outage apart from absent data).
2. `data/provenance.py`: `DataPoint` model with every provenance field from brief §6.3.
3. `data/providers/sec_edgar.py`: rate-limited, cached client; ticker→CIK; submissions; companyfacts. The ticker is confirmed back to the user (name, exchange, CIK) before any analysis.
4. `data/normalisation.py`: XBRL concept mapping (revenue, net income, EPS, OCF, capex, cash, debt, equity, shares). Annual/quarterly split and derived Q4, flagged as calculated.
5. `data/validation.py`: duplicates, units, staleness, missing critical fields, amendments → `DataQualityReport`.
6. Price adapter behind an interface (provider per your decision).
7. Recorded SEC fixtures (clearly labelled) for offline tests, plus one opt-in live test.
8. `GET /api/companies/{ticker}` and a `scripts/fetch_company.py` that saves one company's data.

## 7. Open decisions (need your approval)

1. **Price data provider.** Options:
   (a) **Stooq CSV**: free, no key, daily OHLCV; unclear licence, personal use only.
   (b) **Yahoo (yfinance)**: free, adjusted closes and dividends; unofficial and against Yahoo's terms for commercial use; can break.
   (c) **A licensed API** (e.g. Tiingo, Polygon, or EODHD, roughly $10–30/month at entry tiers; the prices are my approximation, so verify them on each vendor's site): clear terms, needs a key.
   Recommendation: (a) or (c) for the prototype, behind the adapter so it can be swapped later.
2. **SEC User-Agent identity**: the name and email you want sent to SEC (stored only in your local `.env`).
3. **LLM provider and budget** for Milestone 3 onward.
