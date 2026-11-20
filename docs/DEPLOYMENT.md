# Deployment and operations

## Local (no containers)

```bash
uv sync
```
```bash
cp .env.example .env   # set SEC_USER_AGENT (name + contact email); optionally LLM_PROVIDER=anthropic + ANTHROPIC_API_KEY
```
```bash
uv run uvicorn backend.app.main:app --port 8000
```
```bash
cd frontend && npm ci && npx next build && npx next start -p 3000
```

Open http://localhost:3000. Migrations run automatically at API start-up (`alembic upgrade head`).

## Docker Compose (PostgreSQL + API + web)

```bash
docker compose up --build
```

Requires `POSTGRES_PASSWORD` and the variables above in `.env`. Ports are bound to 127.0.0.1 only. Docker was not
available in the build environment, so the compose file has **not** been executed here. The same images' components
(PostgreSQL 16 migrations and API tests, `next build` standalone output) were verified separately.

## Before exposing beyond localhost

- [ ] `AUTH_MODE=token` (registration/login; scrypt password hashes; hashed, expiring session tokens; per-user isolation is tested). Set `ALLOW_REGISTRATION=false` after creating accounts.
- [ ] TLS termination in front of the web container (reverse proxy), HSTS.
- [ ] Encrypt the database volume or use a managed PostgreSQL with encryption at rest.
- [ ] Restrict `CORS_ORIGINS`; the browser only talks to the Next server, which proxies `/api`.
- [ ] Secrets only in the environment / secret manager; never in images, logs or the frontend bundle.
- [ ] Review data licensing: yfinance/Yahoo terms restrict commercial use and redistribution. A commercial deployment needs a licensed market-data provider (the `MarketDataProvider` interface is the swap point).

## Backups and recovery

- PostgreSQL: nightly `pg_dump -Fc ii > ii-$(date +%F).dump`, retained 30 days off-host. Restore with `pg_restore -d ii`.
- SQLite (local): copy `data/investment_intelligence.db` while the API is stopped, or use `sqlite3 .backup`.
- The cache directory is disposable.

## Monitoring and logs

- Structured JSON logs on stdout, with analysis IDs in job and event logs.
- `GET /api/health` reports configured capabilities. `GET /api/data-sources` gives last success and failure per source. `GET /api/monitoring/runs` shows scheduler history.
- After a restart, running analyses are marked failed and retryable; queued ones are re-queued.

## Data retention

- `DELETE /api/user/data` (Settings → "Delete all my data") removes the user's analyses, theses, portfolios, watchlist, alerts and messages.
- Raw audio is never stored. Browser speech stays on the device; server speech providers receive only text generated from the saved report.

## Cost

- **Data:** SEC and yfinance are free, with rate limits.
- **LLM:** each analysis with Claude makes about 8–10 structured calls (5 agents, committee with up to 2 rounds, review answers, final). Token usage is recorded per call in `model_runs`. At Claude Opus 5.5 list prices ($4 / $20 per million tokens), expect very roughly $0.30–$1 per analysis depending on evidence size. Measure it from `model_runs` before relying on the estimate.
- **Monitoring** re-runs an analysis only on material change, with a 3-day cooldown per ticker.
