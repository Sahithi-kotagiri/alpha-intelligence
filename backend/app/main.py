"""FastAPI application: wiring, lifecycle (migrations, job recovery, scheduler), errors and health."""

from __future__ import annotations

import json
import logging
import threading
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.agents.llm import build_llm
from backend.app.api import routes_analysis, routes_core, routes_tracking
from backend.app.config import Settings, get_settings
from backend.data.registry import get_providers
from backend.database.session import get_sessionmaker, run_migrations
from backend.services.analysis_service import JobRunner
from backend.voice.providers import build_voice


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps({"ts": datetime.now(UTC).isoformat(), "level": record.levelname, "logger": record.name,
                           "msg": record.getMessage(), **({"exc": self.formatException(record.exc_info)} if record.exc_info else {})})


def configure_logging(settings: Settings) -> None:
    root = logging.getLogger()
    if not any(isinstance(h.formatter, JsonFormatter) for h in root.handlers):
        h = logging.StreamHandler()
        h.setFormatter(JsonFormatter())
        root.handlers = [h]
    root.setLevel(logging.INFO)
    for noisy in ("httpx", "httpcore", "yfinance", "urllib3", "peewee", "curl_cffi"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def scheduler_loop(app: FastAPI, settings: Settings, stop: threading.Event) -> None:
    """Scheduled monitoring and performance updates. LLM reassessments only start on material change."""
    from backend.intelligence.monitor import run_monitoring
    from backend.intelligence.performance import update_performance

    log = logging.getLogger("scheduler")
    next_mon = time.time() + 60
    next_perf = time.time() + 120
    while not stop.wait(30):
        try:
            if time.time() >= next_mon:
                log.info("scheduled thesis monitoring: %s", run_monitoring(get_sessionmaker(), app.state.providers, app.state.runner))
                next_mon = time.time() + settings.monitoring_interval_hours * 3600
            if time.time() >= next_perf:
                log.info("scheduled performance update: %s", update_performance(get_sessionmaker(), app.state.providers))
                next_perf = time.time() + settings.performance_interval_hours * 3600
        except Exception:
            log.exception("scheduler iteration failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings)
    run_migrations()
    app.state.providers = get_providers()
    app.state.llm_factory = lambda: build_llm(settings)
    app.state.voice = build_voice(settings)
    app.state.runner = JobRunner(settings, get_sessionmaker(), app.state.providers, app.state.llm_factory)
    requeued = app.state.runner.recover()
    logging.getLogger(__name__).info("startup complete; re-queued %d analyses", requeued)
    stop = threading.Event()
    if settings.scheduler_enabled:
        threading.Thread(target=scheduler_loop, args=(app, settings, stop), daemon=True, name="scheduler").start()
    yield
    stop.set()
    app.state.runner.pool.shutdown(wait=True, cancel_futures=True)  # finish running jobs; queued ones resume via recover()


app = FastAPI(title="Investment Intelligence API", version="0.2.0", lifespan=lifespan)
_settings = get_settings()
app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in _settings.cors_origins.split(",") if o.strip()],
                   allow_credentials=False, allow_methods=["*"], allow_headers=["*"])
app.include_router(routes_core.router)
app.include_router(routes_analysis.router)
app.include_router(routes_tracking.router)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    logging.getLogger("api").exception("unhandled error on %s", request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal error. The failure was logged for developers."})


@app.get("/api/health")
def health(settings: Annotated[Settings, Depends(get_settings)]) -> dict:
    return {
        "status": "ok",
        "environment": settings.environment,
        "capabilities": {
            "sec_edgar": "configured" if settings.sec_configured else "not_configured",
            "market_prices": settings.market_data_provider,
            "universe": "S&P 500 (Wikipedia constituents table)",
            "news": "yfinance news feed + SEC 8-K filings",
            "llm": "configured" if settings.llm_configured else "not_configured",
            "voice_server": "configured" if settings.voice_configured else "browser_only",
            "auth_mode": settings.auth_mode,
        },
    }
