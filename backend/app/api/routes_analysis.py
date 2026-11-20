"""Analysis lifecycle, events (SSE), evidence, comparison, assistant, briefing, comments."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sse_starlette.sse import EventSourceResponse

from backend.analysis.evidence import EvidenceItem
from backend.app.api.routes_core import run_summary
from backend.app.security import DB, CurrentUser, valid_ticker
from backend.database import models as M
from backend.database.session import get_sessionmaker
from backend.services import assistant as A
from backend.services.analysis_service import create_analysis
from backend.services.briefing import agent_summary, build_briefing
from backend.services.compare import compare
from backend.services.events import event_dict

router = APIRouter(prefix="/api")


class StartBody(BaseModel):
    ticker: str
    horizon: str = Field(default="12 months", max_length=40)
    benchmark: str = Field(default="SPY", max_length=10)
    include_portfolio: bool = False
    portfolio_id: int | None = None
    idempotency_key: str | None = Field(default=None, max_length=80)
    confirmed: bool = Field(default=False, description="User confirmed the resolved company identity")


def portfolio_snapshot(db, user_id: int, pid: int | None) -> dict[str, Any] | None:
    q = select(M.Portfolio).where(M.Portfolio.user_id == user_id)
    if pid is not None:
        q = q.where(M.Portfolio.id == pid)
    p = db.scalars(q.order_by(M.Portfolio.id)).first()
    if p is None:
        return None
    return {"id": p.id, "name": p.name, "cash": p.cash, "risk_tolerance": p.risk_tolerance, "horizon": p.horizon,
            "benchmark": p.benchmark, "excluded_sectors": p.excluded_sectors, "max_position": p.max_position,
            "holdings": [{"ticker": h.ticker, "quantity": h.quantity, "market_value": h.market_value, "weight": h.weight}
                         for h in p.holdings]}


def owned_run(db, user, analysis_id: str) -> M.AnalysisRun:
    r = db.get(M.AnalysisRun, analysis_id)
    if r is None or r.user_id != user.id:
        raise HTTPException(404, "Analysis not found")
    return r


def completed_result(r: M.AnalysisRun) -> dict[str, Any]:
    if r.status != "completed" or not r.result:
        raise HTTPException(409, f"Analysis is {r.status}")
    return r.result


@router.post("/analysis", status_code=202)
def start(body: StartBody, request: Request, db: DB, user: CurrentUser):
    t = valid_ticker(body.ticker)
    if not body.confirmed:
        raise HTTPException(428, "Confirm the company identity first (GET /api/companies/{ticker}, then confirmed=true)")
    params: dict[str, Any] = {"horizon": body.horizon, "benchmark": valid_ticker(body.benchmark),
                              "include_portfolio": body.include_portfolio}
    if body.include_portfolio:
        snap = portfolio_snapshot(db, user.id, body.portfolio_id)
        if snap is None:
            raise HTTPException(422, "No portfolio found; create one first or disable portfolio context")
        params["portfolio"] = snap
    run = create_analysis(db, user.id, t, params, idempotency_key=body.idempotency_key)
    if run.status == "queued":
        request.app.state.runner.submit(run.id)
    return {"analysis_id": run.id, "status": run.status, "version": run.version}


@router.get("/analysis")
def list_runs(db: DB, user: CurrentUser, ticker: str | None = None, recommendation: str | None = None, limit: int = 100):
    q = select(M.AnalysisRun).where(M.AnalysisRun.user_id == user.id)
    if ticker:
        q = q.where(M.AnalysisRun.ticker == valid_ticker(ticker))
    if recommendation:
        q = q.where(M.AnalysisRun.recommendation == recommendation)
    rows = db.scalars(q.order_by(M.AnalysisRun.created_at.desc()).limit(min(limit, 500))).all()
    return {"analyses": [run_summary(r) for r in rows]}


@router.get("/analysis/compare")
def compare_runs(a: str, b: str, db: DB, user: CurrentUser):
    ra, rb = owned_run(db, user, a), owned_run(db, user, b)
    return compare(completed_result(ra), completed_result(rb), a, b)


@router.get("/analysis/{analysis_id}")
def get_run(analysis_id: str, db: DB, user: CurrentUser):
    r = owned_run(db, user, analysis_id)
    out = run_summary(r)
    if r.status == "completed" and r.result:
        res = r.result
        out["result"] = {k: v for k, v in res.items() if k not in ("metrics", "cross_checks")}
        out["result"]["metric_count"] = len(res.get("metrics", []))
    return out


@router.get("/analysis/{analysis_id}/metrics")
def get_metrics(analysis_id: str, db: DB, user: CurrentUser, category: str | None = None):
    res = completed_result(owned_run(db, user, analysis_id))
    ms = res.get("metrics", [])
    if category:
        ms = [m for m in ms if m["category"] == category]
    return {"metrics": ms, "cross_checks": res.get("cross_checks", [])}


@router.get("/analysis/{analysis_id}/agents")
def get_agents(analysis_id: str, db: DB, user: CurrentUser):
    owned_run(db, user, analysis_id)
    rows = db.scalars(select(M.AgentReport).where(M.AgentReport.analysis_id == analysis_id)).all()
    return {"agents": [r.report for r in rows]}


@router.get("/analysis/{analysis_id}/events")
async def events(analysis_id: str, request: Request, db: DB, user: CurrentUser, stream: bool = True):
    """SSE stream of real progress events; reconnect-safe via Last-Event-ID. stream=false returns JSON."""
    owned_run(db, user, analysis_id)
    after = int(request.headers.get("last-event-id") or request.query_params.get("after") or 0)
    sf = get_sessionmaker()
    if not stream:
        with sf() as s:
            evs = s.scalars(select(M.AnalysisEvent).where(M.AnalysisEvent.analysis_id == analysis_id, M.AnalysisEvent.seq > after)
                            .order_by(M.AnalysisEvent.seq)).all()
            run = s.get(M.AnalysisRun, analysis_id)
            return {"status": run.status, "events": [event_dict(e) for e in evs]}

    async def gen():
        last = after
        idle = 0
        while True:
            if await request.is_disconnected():
                break
            with sf() as s:
                evs = s.scalars(select(M.AnalysisEvent).where(M.AnalysisEvent.analysis_id == analysis_id,
                                                              M.AnalysisEvent.seq > last).order_by(M.AnalysisEvent.seq)).all()
                status = s.get(M.AnalysisRun, analysis_id).status
            for e in evs:
                last = e.seq
                yield {"id": str(e.seq), "event": "progress", "data": json.dumps(event_dict(e))}
            if status in ("completed", "failed") and not evs:
                yield {"event": "end", "data": json.dumps({"status": status})}
                break
            idle = 0 if evs else idle + 1
            if idle and idle % 15 == 0:
                yield {"event": "waiting", "data": json.dumps({"status": status, "message": "Still working; waiting on providers"})}
            await asyncio.sleep(1.0)

    return EventSourceResponse(gen(), ping=15)


@router.post("/analysis/{analysis_id}/retry", status_code=202)
def retry(analysis_id: str, request: Request, db: DB, user: CurrentUser):
    r = owned_run(db, user, analysis_id)
    if r.status != "failed":
        raise HTTPException(409, "Only failed analyses can be retried")
    params = {k: v for k, v in (r.params or {}).items() if k != "prior"}
    new = create_analysis(db, user.id, r.ticker, params, trigger="retry")
    request.app.state.runner.submit(new.id)
    return {"analysis_id": new.id, "status": new.status}


@router.get("/analysis/{analysis_id}/evidence")
def evidence(analysis_id: str, db: DB, user: CurrentUser, q: str | None = None, category: str | None = None,
             ids: str | None = None, limit: int = 500):
    owned_run(db, user, analysis_id)
    stmt = select(M.EvidenceItemRow).where(M.EvidenceItemRow.analysis_id == analysis_id)
    if category:
        stmt = stmt.where(M.EvidenceItemRow.category == category)
    if ids:
        stmt = stmt.where(M.EvidenceItemRow.evidence_id.in_(ids.split(",")[:200]))
    rows = db.scalars(stmt.limit(min(limit, 2000))).all()
    items = [r.payload for r in rows]
    if q:
        ql = q.lower()
        items = [i for i in items if ql in (i["title"] + " " + i["id"] + " " + (i.get("display") or "")).lower()]
    return {"items": items, "count": len(items)}


@router.get("/analysis/{analysis_id}/data-points")
def data_points(analysis_id: str, db: DB, user: CurrentUser, field: str | None = None):
    owned_run(db, user, analysis_id)
    stmt = select(M.DataPointRow).where(M.DataPointRow.analysis_id == analysis_id)
    if field:
        stmt = stmt.where(M.DataPointRow.field == field)
    return {"data_points": [r.payload for r in db.scalars(stmt)]}


@router.get("/analysis/{analysis_id}/briefing")
def briefing(analysis_id: str, db: DB, user: CurrentUser, agent: str | None = None):
    res = completed_result(owned_run(db, user, analysis_id))
    if agent:
        try:
            return agent_summary(res, agent)
        except KeyError as exc:
            raise HTTPException(404, "Unknown agent") from exc
    return build_briefing(res)


def previous_comparison(db, user, r: M.AnalysisRun) -> dict[str, Any] | None:
    prev = db.scalars(select(M.AnalysisRun).where(M.AnalysisRun.user_id == user.id, M.AnalysisRun.ticker == r.ticker,
                                                  M.AnalysisRun.status == "completed", M.AnalysisRun.created_at < r.created_at)
                      .order_by(M.AnalysisRun.created_at.desc()).limit(1)).first()
    if not prev:
        return None
    return compare(prev.result, r.result, prev.id, r.id)


class AskBody(BaseModel):
    question: str = Field(min_length=2, max_length=1000)
    input_mode: str = Field(default="text", pattern="^(text|voice)$")


@router.post("/analysis/{analysis_id}/ask")
def ask(analysis_id: str, body: AskBody, request: Request, db: DB, user: CurrentUser):
    r = owned_run(db, user, analysis_id)
    res = completed_result(r)
    items = [EvidenceItem(**x.payload) for x in db.scalars(select(M.EvidenceItemRow).where(M.EvidenceItemRow.analysis_id == analysis_id))]
    hist = [{"role": m.role, "content": m.content} for m in db.scalars(
        select(M.AssistantMessage).where(M.AssistantMessage.analysis_id == analysis_id, M.AssistantMessage.user_id == user.id)
        .order_by(M.AssistantMessage.id))]
    ans = A.answer(body.question, res, items, request.app.state.llm_factory(), previous_comparison(db, user, r), hist)
    db.add(M.AssistantMessage(user_id=user.id, analysis_id=analysis_id, role="user", content=body.question,
                              meta={"input_mode": body.input_mode}))
    db.add(M.AssistantMessage(user_id=user.id, analysis_id=analysis_id, role="assistant", content=ans["answer"],
                              citations=ans.get("citations", []), meta={k: v for k, v in ans.items() if k not in ("answer", "citations")}))
    db.commit()
    return ans


@router.get("/analysis/{analysis_id}/messages")
def messages(analysis_id: str, db: DB, user: CurrentUser):
    owned_run(db, user, analysis_id)
    rows = db.scalars(select(M.AssistantMessage).where(M.AssistantMessage.analysis_id == analysis_id,
                                                       M.AssistantMessage.user_id == user.id).order_by(M.AssistantMessage.id)).all()
    return {"messages": [{"role": m.role, "content": m.content, "citations": m.citations, "meta": m.meta,
                          "created_at": m.created_at.isoformat()} for m in rows]}


class CommentBody(BaseModel):
    comment: str | None = Field(default=None, max_length=4000)
    user_action: str | None = Field(default=None, pattern="^(acted|not_acted|unknown)$")


@router.post("/analysis/{analysis_id}/comment")
def comment(analysis_id: str, body: CommentBody, db: DB, user: CurrentUser):
    """User comments/actions are stored on the recommendation and attributed to the user; the analysis stays immutable."""
    owned_run(db, user, analysis_id)
    rec = db.scalar(select(M.Recommendation).where(M.Recommendation.analysis_id == analysis_id))
    if rec is None:
        raise HTTPException(409, "No recommendation recorded")
    if body.comment is not None:
        rec.user_comment = f"[{user.email}] {body.comment}"
    if body.user_action:
        rec.user_action = body.user_action
    db.commit()
    return {"ok": True, "user_comment": rec.user_comment, "user_action": rec.user_action}
