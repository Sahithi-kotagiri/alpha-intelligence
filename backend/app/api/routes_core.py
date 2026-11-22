"""Auth, universe, companies, screener, settings and data-source routes."""

from __future__ import annotations

import re
import threading
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from backend.app.config import Settings, get_settings
from backend.app.security import (
    DB,
    CurrentUser,
    hash_password,
    issue_token,
    token_hash,
    valid_ticker,
    verify_password,
)
from backend.data.providers.base import ProviderError
from backend.database import models as M
from backend.database.session import get_sessionmaker
from backend.services import universe as U

router = APIRouter(prefix="/api")


# ------------------------------------------------------------------ auth
class Credentials(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=255)
    password: str = Field(min_length=10, max_length=200)
    display_name: str | None = Field(default=None, max_length=120)


@router.post("/auth/register")
def register(body: Credentials, db: DB, settings: Settings = Depends(get_settings)):
    if settings.auth_mode != "token":
        raise HTTPException(400, "Registration is only used when AUTH_MODE=token")
    if not settings.allow_registration:
        raise HTTPException(403, "Registration disabled")
    if db.scalar(select(M.User).where(M.User.email == body.email)):
        raise HTTPException(409, "Email already registered")
    u = M.User(email=body.email, password_hash=hash_password(body.password), display_name=body.display_name)
    db.add(u)
    db.commit()
    return {"token": issue_token(db, u, settings), "user": {"id": u.id, "email": u.email}}


@router.post("/auth/login")
def login(body: Credentials, db: DB, settings: Settings = Depends(get_settings)):
    if settings.auth_mode != "token":
        raise HTTPException(400, "Login is only used when AUTH_MODE=token")
    u = db.scalar(select(M.User).where(M.User.email == body.email))
    if u is None or not verify_password(body.password, u.password_hash):
        raise HTTPException(401, "Invalid email or password")
    return {"token": issue_token(db, u, settings), "user": {"id": u.id, "email": u.email}}


@router.post("/auth/logout")
def logout(request: Request, db: DB, user: CurrentUser):
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        db.execute(delete(M.AuthSession).where(M.AuthSession.token_hash == token_hash(auth.split(" ", 1)[1].strip())))
        db.commit()
    return {"ok": True}


@router.get("/auth/me")
def me(user: CurrentUser, settings: Settings = Depends(get_settings)):
    return {"id": user.id, "email": user.email, "display_name": user.display_name, "auth_mode": settings.auth_mode}


@router.delete("/user/data")
def delete_my_data(db: DB, user: CurrentUser):
    """Retention control: delete all of the current user's portfolios, watchlist, analyses, theses, alerts and messages."""
    for model in (M.AssistantMessage, M.Alert, M.WatchlistItem, M.Portfolio, M.InvestmentThesis, M.Recommendation,
                  M.AnalysisRun, M.UserPreference, M.VoicePreference):
        db.execute(delete(model).where(model.user_id == user.id))
    db.commit()
    return {"deleted": True}


# ------------------------------------------------------------------ universe & companies
@router.get("/universe")
def universe(request: Request, db: DB, user: CurrentUser):
    rows = U.ensure_universe(db, request.app.state.providers)
    changes = db.scalars(select(M.UniverseChange).order_by(M.UniverseChange.detected_at.desc()).limit(50)).all()
    return {"count": len(rows), "rows": rows, "source": request.app.state.providers.universe.name,
            "source_url": request.app.state.providers.universe.url,
            "retrieved_at": rows[0]["retrieved_at"] if rows else None,
            "changes": [{"ticker": c.ticker, "change": c.change, "detected_at": c.detected_at.isoformat()} for c in changes]}


@router.post("/universe/refresh")
def refresh(request: Request, db: DB, user: CurrentUser):
    try:
        return U.refresh_universe(db, request.app.state.providers, force=True)
    except ProviderError as exc:
        raise HTTPException(502, f"Constituent source unavailable: {exc}") from exc


@router.get("/companies/search")
def search(q: str, request: Request, db: DB, user: CurrentUser):
    if len(q) > 60:
        raise HTTPException(422, "Query too long")
    return {"results": U.search(db, request.app.state.providers, q)}


@router.get("/companies/{ticker}")
def company(ticker: str, request: Request, db: DB, user: CurrentUser):
    return U.resolve(db, request.app.state.providers, valid_ticker(ticker))


@router.get("/companies/{ticker}/history")
def company_history(ticker: str, db: DB, user: CurrentUser):
    t = valid_ticker(ticker)
    runs = db.scalars(select(M.AnalysisRun).where(M.AnalysisRun.user_id == user.id, M.AnalysisRun.ticker == t)
                      .order_by(M.AnalysisRun.created_at.desc())).all()
    return {"ticker": t, "analyses": [run_summary(r) for r in runs]}


def run_summary(r: M.AnalysisRun) -> dict[str, Any]:
    sc = ((r.result or {}).get("final") or {}).get("scores") or {}
    return {"id": r.id, "ticker": r.ticker, "company_name": r.company_name, "version": r.version, "status": r.status,
            "trigger": r.trigger, "mode": r.mode, "created_at": r.created_at.isoformat() if r.created_at else None,
            "completed_at": r.completed_at.isoformat() if r.completed_at else None, "recommendation": r.recommendation,
            "confidence": r.confidence, "data_quality": r.data_quality_status, "reference_price": r.reference_price,
            "price_date": r.price_date.isoformat() if r.price_date else None, "parent_id": r.parent_id, "error": r.error,
            "scores": sc, "sector": ((r.result or {}).get("identity") or {}).get("sector")}


# ------------------------------------------------------------------ screener
@router.get("/screener")
def screener(request: Request, db: DB, user: CurrentUser):
    U.ensure_universe(db, request.app.state.providers)
    return U.screener_table(db)


@router.get("/screener/{ticker}")
def screener_detail(ticker: str, db: DB, user: CurrentUser):
    """Rulebook drill-down for one screened constituent: measures, percentile basis, gates, trace, fair value."""
    from backend.services.rulebook_service import universe_labels

    t = ticker.strip().upper()
    if not re.fullmatch(r"[A-Z0-9.\-]{1,10}", t):
        raise HTTPException(422, "invalid ticker")
    row = universe_labels(db).get(t)
    if row is None:
        raise HTTPException(404, f"{t} has no rulebook result yet; run a universe screen")
    return row


@router.post("/screener/run")
def screener_run(request: Request, db: DB, user: CurrentUser, settings: Settings = Depends(get_settings)):
    running = db.scalar(select(M.ScreenRun).where(M.ScreenRun.status.in_(("queued", "running"))))
    if running:
        return {"id": running.id, "status": running.status, "message": "A screen is already running"}
    U.ensure_universe(db, request.app.state.providers)
    run = M.ScreenRun(user_id=user.id, status="queued")
    db.add(run)
    db.commit()
    threading.Thread(target=U.run_screen, args=(get_sessionmaker(), request.app.state.providers, run.id,
                                                settings.universe_screen_concurrency), daemon=True).start()
    return {"id": run.id, "status": "queued"}


# ------------------------------------------------------------------ settings & sources
class PrefsBody(BaseModel):
    default_horizon: str | None = Field(default=None, max_length=40)
    default_benchmark: str | None = Field(default=None, max_length=20)
    voice_enabled: bool | None = None
    voice_provider: str | None = Field(default=None, pattern="^(browser|server)$")
    voice_name: str | None = Field(default=None, max_length=120)
    voice_rate: float | None = Field(default=None, ge=0.5, le=2.0)


@router.get("/settings")
def get_prefs(db: DB, user: CurrentUser, settings: Settings = Depends(get_settings)):
    p = db.scalar(select(M.UserPreference).where(M.UserPreference.user_id == user.id))
    v = db.scalar(select(M.VoicePreference).where(M.VoicePreference.user_id == user.id))
    return {"default_horizon": p.default_horizon if p else "12 months", "default_benchmark": p.default_benchmark if p else "SPY",
            "voice": {"enabled": v.enabled if v else True, "provider": v.provider if v else "browser",
                      "voice_name": v.voice_name if v else None, "rate": v.rate if v else 1.0, "store_audio": False},
            "server": {"llm_provider": settings.llm_provider, "llm_configured": settings.llm_configured,
                       "llm_model": settings.llm_model if settings.llm_configured else None,
                       "voice_provider": settings.voice_provider, "voice_configured": settings.voice_configured,
                       "auth_mode": settings.auth_mode, "sec_configured": settings.sec_configured,
                       "market_data_provider": settings.market_data_provider}}


@router.put("/settings")
def put_prefs(body: PrefsBody, db: DB, user: CurrentUser):
    p = db.scalar(select(M.UserPreference).where(M.UserPreference.user_id == user.id)) or M.UserPreference(user_id=user.id)
    v = db.scalar(select(M.VoicePreference).where(M.VoicePreference.user_id == user.id)) or M.VoicePreference(user_id=user.id)
    if body.default_horizon:
        p.default_horizon = body.default_horizon
    if body.default_benchmark:
        p.default_benchmark = valid_ticker(body.default_benchmark)
    if body.voice_enabled is not None:
        v.enabled = body.voice_enabled
    if body.voice_provider:
        v.provider = body.voice_provider
    if body.voice_name is not None:
        v.voice_name = body.voice_name
    if body.voice_rate is not None:
        v.rate = body.voice_rate
    db.add_all([p, v])
    db.commit()
    return {"ok": True}


@router.get("/data-sources")
def data_sources(db: DB, user: CurrentUser, settings: Settings = Depends(get_settings)):
    rows = db.scalars(select(M.DataSource)).all()
    uni = db.scalar(select(M.UniverseMember).order_by(M.UniverseMember.retrieved_at.desc()).limit(1))
    return {"sources": [{"name": r.name, "kind": r.kind, "licence_note": r.licence_note,
                         "last_success_at": r.last_success_at.isoformat() if r.last_success_at else None,
                         "last_failure_at": r.last_failure_at.isoformat() if r.last_failure_at else None,
                         "last_error": r.last_error} for r in rows],
            "universe_retrieved_at": uni.retrieved_at.isoformat() if uni else None,
            "configured": {"sec_user_agent": settings.sec_configured, "llm": settings.llm_configured,
                           "voice_server": settings.voice_configured},
            "now": datetime.now(UTC).isoformat()}
