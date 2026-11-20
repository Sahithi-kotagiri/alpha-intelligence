"""Portfolio, watchlist, thesis, alerts, monitoring, performance, evaluation and voice routes."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from backend.analysis.evidence import EvidenceItem
from backend.app.api.routes_analysis import completed_result, owned_run
from backend.app.security import DB, CurrentUser, valid_ticker
from backend.database import models as M
from backend.database.session import get_sessionmaker
from backend.intelligence import evaluation as EV
from backend.intelligence import performance as P
from backend.intelligence.monitor import run_monitoring
from backend.services.briefing import agent_summary, build_briefing
from backend.tools import portfolio_metrics as PM
from backend.voice.providers import VoiceError

router = APIRouter(prefix="/api")


# ------------------------------------------------------------------ portfolio
class HoldingIn(BaseModel):
    ticker: str
    quantity: float | None = Field(default=None, ge=0)
    market_value: float | None = Field(default=None, ge=0)
    weight: float | None = Field(default=None, ge=0)
    cost_basis: float | None = None


class PortfolioIn(BaseModel):
    name: str = Field(default="My portfolio", max_length=120)
    base_currency: str = Field(default="USD", max_length=10)
    cash: float = Field(default=0.0, ge=0)
    risk_tolerance: str | None = Field(default=None, pattern="^(conservative|moderate|aggressive)$")
    horizon: str | None = Field(default=None, max_length=40)
    benchmark: str = Field(default="SPY", max_length=10)
    excluded_sectors: list[str] = Field(default_factory=list)
    max_position: float | None = Field(default=None, gt=0, le=1)
    liquidity_needs: str | None = Field(default=None, max_length=1000)
    holdings: list[HoldingIn] = Field(default_factory=list, max_length=200)


def _pf_out(p: M.Portfolio) -> dict[str, Any]:
    return {"id": p.id, "name": p.name, "base_currency": p.base_currency, "cash": p.cash, "risk_tolerance": p.risk_tolerance,
            "horizon": p.horizon, "benchmark": p.benchmark, "excluded_sectors": p.excluded_sectors, "max_position": p.max_position,
            "liquidity_needs": p.liquidity_needs, "updated_at": p.updated_at.isoformat() if p.updated_at else None,
            "holdings": [{"ticker": h.ticker, "quantity": h.quantity, "market_value": h.market_value, "weight": h.weight,
                          "cost_basis": h.cost_basis} for h in p.holdings]}


@router.get("/portfolio")
def get_portfolio(db: DB, user: CurrentUser):
    rows = db.scalars(select(M.Portfolio).where(M.Portfolio.user_id == user.id).order_by(M.Portfolio.id)).all()
    return {"portfolios": [_pf_out(p) for p in rows]}


@router.post("/portfolio")
def save_portfolio(body: PortfolioIn, db: DB, user: CurrentUser, portfolio_id: int | None = None):
    if portfolio_id is not None:
        p = db.get(M.Portfolio, portfolio_id)
        if p is None or p.user_id != user.id:
            raise HTTPException(404, "Portfolio not found")
        p.holdings.clear()
    else:
        p = db.scalars(select(M.Portfolio).where(M.Portfolio.user_id == user.id).order_by(M.Portfolio.id)).first()
        if p is None:
            p = M.Portfolio(user_id=user.id)
            db.add(p)
        else:
            p.holdings.clear()
    for k in ("name", "base_currency", "cash", "risk_tolerance", "horizon", "excluded_sectors", "max_position", "liquidity_needs"):
        setattr(p, k, getattr(body, k))
    p.benchmark = valid_ticker(body.benchmark)
    for h in body.holdings:
        if h.quantity is None and h.market_value is None and h.weight is None:
            raise HTTPException(422, f"{h.ticker}: supply quantity, market value or weight")
        p.holdings.append(M.PortfolioHolding(ticker=valid_ticker(h.ticker), quantity=h.quantity, market_value=h.market_value,
                                             weight=h.weight, cost_basis=h.cost_basis))
    db.commit()
    return _pf_out(p)


@router.get("/portfolio/risk")
def portfolio_risk(request: Request, db: DB, user: CurrentUser, candidate: str | None = None, portfolio_id: int | None = None):
    q = select(M.Portfolio).where(M.Portfolio.user_id == user.id)
    if portfolio_id:
        q = q.where(M.Portfolio.id == portfolio_id)
    p = db.scalars(q.order_by(M.Portfolio.id)).first()
    if p is None or not p.holdings:
        return {"available": False, "reason": "No portfolio holdings saved."}
    prov = request.app.state.providers
    from backend.services.universe import ensure_universe

    ensure_universe(db, prov)
    tickers = sorted({h.ticker for h in p.holdings} | ({valid_ticker(candidate)} if candidate else set()))
    price_map, missing = {}, []
    for t in tickers:
        try:
            price_map[t] = prov.market.get_price_history(t)
        except Exception as exc:
            missing.append({"ticker": t, "error": str(exc)})
    last = {t: float(df["Close"].iloc[-1]) for t, df in price_map.items()}
    holdings = [{"ticker": h.ticker, "quantity": h.quantity, "market_value": h.market_value, "weight": h.weight} for h in p.holdings]
    w = PM.weights_from_holdings(holdings, last, p.cash)
    rets = PM.returns_frame(price_map, 252)
    sectors = {u.ticker: u.sector for u in db.scalars(select(M.UniverseMember).where(M.UniverseMember.ticker.in_(tickers)))}
    bench = None
    try:
        b = prov.market.get_price_history(p.benchmark)
        bench = (b["Adj Close"] if "Adj Close" in b else b["Close"]).pct_change()
    except Exception as exc:
        missing.append({"ticker": p.benchmark, "error": str(exc)})
    out = {"available": True, "portfolio": _pf_out(p), "weights": w["weights"], "cash_weight": w["cash_weight"],
           "total_value": w["total_value"], "weight_method": w["method"], "missing_prices": w["missing_prices"] + missing,
           "sector_exposure": PM.exposures(w["weights"], sectors), "hhi": PM.hhi(w["weights"]),
           "effective_positions": (1 / PM.hhi(w["weights"])) if w["weights"] else None,
           "largest_position": max(w["weights"].items(), key=lambda kv: kv[1]) if w["weights"] else None,
           "volatility": PM.portfolio_vol(w["weights"], rets), "max_drawdown_1y": PM.portfolio_drawdown(w["weights"], rets),
           "beta": PM.portfolio_beta(w["weights"], rets, bench) if bench is not None else None,
           "risk_contributions": PM.risk_contributions(w["weights"], rets), "correlation": PM.correlation_matrix(rets),
           "currency_exposure": {"USD (S&P 500 constituents)": sum(v for t, v in w["weights"].items() if t in sectors),
                                 "unclassified": sum(v for t, v in w["weights"].items() if t not in sectors),
                                 "note": "Underlying revenue currency exposure is not available from current sources."},
           "lookback": f"{len(rets)} daily sessions", "method": PM.__doc__,
           "limitations": ["Historical correlations and volatility do not guarantee future behaviour.",
                           "Constant weights assumed (no rebalancing drift); cash treated as zero volatility."]}
    if candidate:
        c = valid_ticker(candidate)
        cw = {"conservative": 0.03, "moderate": 0.05, "aggressive": 0.08}.get(p.risk_tolerance or "", 0.05)
        out["candidate_impact"] = PM.candidate_impact(w["weights"], w["cash_weight"], c, cw, rets, sectors)
        imp = out["candidate_impact"]
        if bench is not None:
            after_w = {t: v * (1 - cw) for t, v in w["weights"].items()}
            after_w[c] = after_w.get(c, 0.0) + cw
            b_after = PM.portfolio_beta(after_w, rets, bench)
            imp["market_stress"] = [{"market_move": s, "portfolio_before": out["beta"] * s if out["beta"] is not None else None,
                                     "portfolio_after": b_after * s if b_after is not None else None,
                                     "method": "portfolio beta × market move (linear approximation)"} for s in (-0.10, -0.20, -0.35)]
        if p.excluded_sectors and sectors.get(c) in p.excluded_sectors:
            imp["constraint_breach"] = f"{c} is in excluded sector {sectors.get(c)}"
    return out


# ------------------------------------------------------------------ watchlist
class WatchIn(BaseModel):
    ticker: str
    note: str | None = Field(default=None, max_length=2000)
    target_price: float | None = Field(default=None, gt=0)
    alert_prefs: dict[str, bool] = Field(default_factory=lambda: {"thesis": True, "filings": True, "price": True, "earnings": True})
    remove: bool = False


@router.get("/watchlist")
def watchlist(db: DB, user: CurrentUser):
    items = db.scalars(select(M.WatchlistItem).where(M.WatchlistItem.user_id == user.id).order_by(M.WatchlistItem.added_at)).all()
    out = []
    for w in items:
        last = db.scalars(select(M.AnalysisRun).where(M.AnalysisRun.user_id == user.id, M.AnalysisRun.ticker == w.ticker,
                                                      M.AnalysisRun.status == "completed").order_by(M.AnalysisRun.created_at.desc()).limit(1)).first()
        th = db.scalar(select(M.InvestmentThesis).where(M.InvestmentThesis.user_id == user.id, M.InvestmentThesis.ticker == w.ticker,
                                                        M.InvestmentThesis.status == "active"))
        nxt = None
        if last and last.result:
            news_tables = ((last.result.get("reports") or {}).get("news") or {}).get("deterministic") or {}
            cal = news_tables.get("upcoming") or []
            nxt = cal[0] if cal else None
        out.append({"ticker": w.ticker, "note": w.note, "target_price": w.target_price, "alert_prefs": w.alert_prefs,
                    "review_status": w.review_status, "added_at": w.added_at.isoformat(),
                    "last_analysis": None if not last else {"id": last.id, "recommendation": last.recommendation,
                                                            "confidence": last.confidence, "reference_price": last.reference_price,
                                                            "price_date": last.price_date.isoformat() if last.price_date else None,
                                                            "company_name": last.company_name},
                    "thesis_status": th.overall_status if th else None, "next_event": nxt})
    return {"items": out}


@router.post("/watchlist")
def watch(body: WatchIn, db: DB, user: CurrentUser):
    t = valid_ticker(body.ticker)
    w = db.scalar(select(M.WatchlistItem).where(M.WatchlistItem.user_id == user.id, M.WatchlistItem.ticker == t))
    if body.remove:
        if w:
            db.delete(w)
            db.commit()
        return {"removed": True}
    if w is None:
        w = M.WatchlistItem(user_id=user.id, ticker=t)
        db.add(w)
    w.note, w.target_price, w.alert_prefs = body.note, body.target_price, body.alert_prefs
    db.commit()
    return {"ticker": t, "saved": True}


@router.delete("/watchlist/{ticker}")
def unwatch(ticker: str, db: DB, user: CurrentUser):
    return watch(WatchIn(ticker=ticker, remove=True), db, user)


# ------------------------------------------------------------------ thesis & alerts
def _thesis_out(th: M.InvestmentThesis) -> dict[str, Any]:
    return {"id": th.id, "ticker": th.ticker, "analysis_id": th.analysis_id, "version": th.version, "status": th.status,
            "overall_status": th.overall_status, "summary": th.summary, "created_at": th.created_at.isoformat(),
            "last_checked_at": th.last_checked_at.isoformat() if th.last_checked_at else None,
            "assumptions": [{"id": a.id, "key": a.key, "description": a.description, "kind": a.kind, "metric_key": a.metric_key,
                             "comparator": a.comparator, "threshold": a.threshold, "review_threshold": a.review_threshold,
                             "original_value": a.original_value, "current_value": a.current_value, "unit": a.unit,
                             "status": a.status, "materiality": a.materiality, "invalidation": a.invalidation,
                             "evidence_ids": a.evidence_ids,
                             "last_checked_at": a.last_checked_at.isoformat() if a.last_checked_at else None}
                            for a in th.assumptions]}


@router.get("/thesis")
def theses(db: DB, user: CurrentUser):
    rows = db.scalars(select(M.InvestmentThesis).where(M.InvestmentThesis.user_id == user.id, M.InvestmentThesis.status == "active")
                      .order_by(M.InvestmentThesis.created_at.desc())).all()
    out = []
    for th in rows:
        run = db.get(M.AnalysisRun, th.analysis_id)
        d = _thesis_out(th)
        d.update(recommendation=run.recommendation if run else None, company_name=run.company_name if run else None)
        out.append(d)
    return {"theses": out}


@router.get("/thesis/{ticker}")
def thesis(ticker: str, db: DB, user: CurrentUser):
    t = valid_ticker(ticker)
    rows = db.scalars(select(M.InvestmentThesis).where(M.InvestmentThesis.user_id == user.id, M.InvestmentThesis.ticker == t)
                      .order_by(M.InvestmentThesis.version.desc())).all()
    if not rows:
        raise HTTPException(404, "No thesis for this ticker")
    hist = []
    for th in rows:
        run = db.get(M.AnalysisRun, th.analysis_id)
        hist.append({"version": th.version, "status": th.status, "analysis_id": th.analysis_id,
                     "recommendation": run.recommendation if run else None, "created_at": th.created_at.isoformat()})
    return {"current": _thesis_out(rows[0]), "versions": hist}


@router.get("/thesis/{ticker}/events")
def thesis_events(ticker: str, db: DB, user: CurrentUser):
    t = valid_ticker(ticker)
    ids = [th.id for th in db.scalars(select(M.InvestmentThesis).where(M.InvestmentThesis.user_id == user.id,
                                                                        M.InvestmentThesis.ticker == t))]
    evs = db.scalars(select(M.ThesisEvent).where(M.ThesisEvent.thesis_id.in_(ids)).order_by(M.ThesisEvent.created_at.desc())).all()
    return {"events": [{"id": e.id, "type": e.event_type, "title": e.title, "materiality": e.materiality, "detail": e.detail,
                        "status_before": e.status_before, "status_after": e.status_after, "source_url": e.source_url,
                        "created_at": e.created_at.isoformat(), "reassessment_analysis_id": e.reassessment_analysis_id}
                       for e in evs]}


@router.get("/alerts")
def alerts(db: DB, user: CurrentUser, unread: bool = False):
    q = select(M.Alert).where(M.Alert.user_id == user.id)
    if unread:
        q = q.where(M.Alert.read_at.is_(None))
    rows = db.scalars(q.order_by(M.Alert.created_at.desc()).limit(200)).all()
    return {"alerts": [{"id": a.id, "ticker": a.ticker, "kind": a.kind, "severity": a.severity, "title": a.title,
                        "message": a.message, "detail": a.detail, "created_at": a.created_at.isoformat(),
                        "read": a.read_at is not None} for a in rows]}


@router.post("/alerts/{alert_id}/read")
def alert_read(alert_id: int, db: DB, user: CurrentUser):
    a = db.get(M.Alert, alert_id)
    if a is None or a.user_id != user.id:
        raise HTTPException(404, "Alert not found")
    a.read_at = datetime.now(UTC)
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ monitoring & performance
@router.post("/monitoring/run")
def monitoring_run(request: Request, user: CurrentUser):
    return run_monitoring(get_sessionmaker(), request.app.state.providers, request.app.state.runner, user_id=user.id)


@router.get("/monitoring/runs")
def monitoring_runs(db: DB, user: CurrentUser):
    rows = db.scalars(select(M.MonitoringRun).order_by(M.MonitoringRun.id.desc()).limit(20)).all()
    return {"runs": [{"id": r.id, "kind": r.kind, "status": r.status, "started_at": r.started_at.isoformat(),
                      "completed_at": r.completed_at.isoformat() if r.completed_at else None, "checked": r.checked,
                      "events_created": r.events_created, "error": r.error} for r in rows]}


@router.get("/performance")
def performance(db: DB, user: CurrentUser):
    recs = db.scalars(select(M.Recommendation).where(M.Recommendation.user_id == user.id)
                      .order_by(M.Recommendation.created_at.desc())).all()
    rows = []
    for r in recs:
        obs = db.scalars(select(M.PerformanceObservation).where(M.PerformanceObservation.recommendation_id == r.id)
                         .order_by(M.PerformanceObservation.horizon_sessions)).all()
        rows.append({"analysis_id": r.analysis_id, "ticker": r.ticker, "recommendation": r.recommendation,
                     "confidence": r.confidence, "price_date": r.price_date.isoformat() if r.price_date else None,
                     "reference_price": r.reference_price, "benchmark": r.benchmark, "user_action": r.user_action,
                     "observations": [{"horizon": o.horizon_label, "status": o.status, "total_return": o.total_return,
                                       "benchmark_return": o.benchmark_return, "excess_return": o.excess_return,
                                       "max_drawdown": o.max_drawdown, "end_date": o.end_date.isoformat() if o.end_date else None}
                                      for o in obs]})
    return {"summary": P.summary(db, user.id), "recommendations": rows}


@router.post("/performance/update")
def performance_update(request: Request, user: CurrentUser):
    return P.update_performance(get_sessionmaker(), request.app.state.providers)


class BaselineBody(BaseModel):
    analysis_id: str
    repeats: int = Field(default=1, ge=1, le=5)


@router.post("/evaluation/baseline")
def baseline(body: BaselineBody, request: Request, db: DB, user: CurrentUser):
    r = owned_run(db, user, body.analysis_id)
    res = completed_result(r)
    llm = request.app.state.llm_factory()
    items = [EvidenceItem(**x.payload) for x in db.scalars(select(M.EvidenceItemRow).where(M.EvidenceItemRow.analysis_id == r.id))]
    plat = EV.platform_quality(res, items)
    if llm is None:
        return {"available": False, "reason": "Single-LLM baseline requires an LLM provider (set LLM_PROVIDER and ANTHROPIC_API_KEY).",
                "platform": plat}
    base = EV.run_baseline(llm, res, items, body.repeats)
    return {"available": True, "platform": plat, "baseline": base, "comparison": EV.compare(plat, base)}


# ------------------------------------------------------------------ voice
@router.get("/voice/config")
def voice_config(request: Request, user: CurrentUser):
    v = request.app.state.voice
    return {"server_provider": v.name, "server_available": v.name != "none",
            "browser_fallback": True, "stores_audio": False,
            "note": "When no server provider is configured, the browser's built-in speech recognition and synthesis are used."}


@router.post("/voice/transcribe")
async def transcribe(request: Request, user: CurrentUser, audio: UploadFile = File(...)):
    data = await audio.read()
    try:
        text = request.app.state.voice.transcribe(data, audio.content_type or "audio/webm")
    except VoiceError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    finally:
        del data  # raw audio is never persisted
    return {"text": text}


@router.post("/voice/speech")
def speech(request: Request, db: DB, user: CurrentUser, analysis_id: str = Form(...), kind: str = Form("briefing"),
           speed: float = Form(1.0), voice: str | None = Form(None)):
    """Speech only for approved text generated server-side from a saved analysis (briefing or agent summary)."""
    res = completed_result(owned_run(db, user, analysis_id))
    if kind == "briefing":
        text = build_briefing(res)["text"]
    elif kind.startswith("agent:"):
        try:
            text = agent_summary(res, kind.split(":", 1)[1])["text"]
        except KeyError as exc:
            raise HTTPException(404, "Unknown agent") from exc
    else:
        raise HTTPException(422, "kind must be 'briefing' or 'agent:<name>'")
    try:
        audio, mime = request.app.state.voice.speech(text, voice, speed)
    except VoiceError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    return Response(content=audio, media_type=mime, headers={"X-Transcript-Source": "saved-report"})
