# Investment Intelligence Platform (prototype)

Evidence-led research for US-listed equities. It covers specialist analysts, an investment committee, persistent theses, monitoring and portfolio context.

**Status: Milestone 0 (skeleton).** Not investment advice. No live market data, news or LLM is connected yet. See [`docs/PLAN.md`](docs/PLAN.md) for the architecture, milestone checklist, assumptions and risks.

## Setup

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env.example .env   # then set SEC_USER_AGENT="Your Name you@example.com"
uv run pytest
uv run uvicorn backend.app.main:app --reload
```

Then open http://127.0.0.1:8000/api/health. It reports which providers are configured and which are not yet implemented.

## What is real vs. mocked

| Capability | State |
|---|---|
| Health endpoint and config | Real |
| SEC EDGAR | Network access confirmed; adapter arrives in Milestone 1 |
| Market prices | Provider not yet chosen (pending your decision) |
| News | Not implemented |
| LLM agents | Not implemented (Milestone 3+) |
