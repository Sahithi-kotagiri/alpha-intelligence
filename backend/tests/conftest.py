from __future__ import annotations

import os
import time

import pytest


def _clear_caches():
    from backend.app.config import get_settings
    from backend.database import session as S

    get_settings.cache_clear()
    S.get_engine.cache_clear()
    S.get_sessionmaker.cache_clear()


@pytest.fixture
def env(tmp_path, monkeypatch):
    pg = os.environ.get("TEST_DATABASE_URL")
    if pg:  # e.g. postgresql+psycopg://user@host:port/db — schema is reset for every test
        from sqlalchemy import create_engine, text

        eng = create_engine(pg)
        with eng.begin() as c:
            c.execute(text("DROP SCHEMA public CASCADE"))
            c.execute(text("CREATE SCHEMA public"))
        eng.dispose()
        monkeypatch.setenv("DATABASE_URL", pg)
    else:
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("LLM_PROVIDER", "none")
    monkeypatch.setenv("VOICE_PROVIDER", "none")
    monkeypatch.setenv("AUTH_MODE", "local")
    monkeypatch.setenv("SEC_USER_AGENT", "Test test@example.com")
    monkeypatch.setenv("CACHE_DIR", str(tmp_path / "cache"))
    _clear_caches()
    yield tmp_path
    _clear_caches()


@pytest.fixture
def providers():
    from backend.tests.fixtures import fake_providers

    return fake_providers()


@pytest.fixture
def client(env, providers, monkeypatch):
    from fastapi.testclient import TestClient

    from backend.app import main

    monkeypatch.setattr(main, "get_providers", lambda: providers)
    with TestClient(main.app) as c:
        c.providers = providers
        yield c


def wait_for(client, analysis_id: str, timeout: float = 60.0) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = client.get(f"/api/analysis/{analysis_id}").json()
        if r["status"] in ("completed", "failed"):
            return r
        time.sleep(0.2)
    raise AssertionError("analysis did not finish")
