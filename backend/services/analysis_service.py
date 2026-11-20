"""Background analysis jobs: create, run (LangGraph), persist immutably, recover after restart."""

from __future__ import annotations

import json
import logging
import math
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from backend.agents.base import load_prompt
from backend.agents.llm import build_llm
from backend.app.config import Settings
from backend.data.ingestion import CompanyIdentity
from backend.database import models as M
from backend.orchestration.graph import build_graph
from backend.services import universe as U
from backend.services.events import EventWriter

log = logging.getLogger(__name__)
HORIZONS = [(21, "1m (21 sessions)"), (63, "3m (63 sessions)"), (126, "6m (126 sessions)"), (252, "12m (252 sessions)")]


def jsonable(obj: Any) -> Any:
    def default(o):
        if hasattr(o, "model_dump"):
            return o.model_dump(mode="json")
        if isinstance(o, (date, datetime)):
            return o.isoformat()
        if hasattr(o, "__dict__"):
            return o.__dict__
        return str(o)

    def clean(x):
        if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
            return None
        if isinstance(x, dict):
            return {str(k): clean(v) for k, v in x.items()}
        if isinstance(x, list):
            return [clean(v) for v in x]
        return x

    return clean(json.loads(json.dumps(obj, default=default)))


def create_analysis(db: Session, user_id: int, ticker: str, params: dict[str, Any], trigger: str = "user",
                    idempotency_key: str | None = None, parent_id: str | None = None) -> M.AnalysisRun:
    if idempotency_key:
        existing = db.scalar(select(M.AnalysisRun).where(M.AnalysisRun.idempotency_key == idempotency_key))
        if existing:
            return existing
    t = ticker.upper()
    prev = db.scalar(select(func.max(M.AnalysisRun.version)).where(M.AnalysisRun.user_id == user_id, M.AnalysisRun.ticker == t)) or 0
    if parent_id is None:
        last = db.scalars(select(M.AnalysisRun).where(M.AnalysisRun.user_id == user_id, M.AnalysisRun.ticker == t,
                                                      M.AnalysisRun.status == "completed")
                          .order_by(M.AnalysisRun.created_at.desc()).limit(1)).first()
        parent_id = last.id if last else None
    run = M.AnalysisRun(id=str(uuid.uuid4()), user_id=user_id, ticker=t, version=prev + 1, parent_id=parent_id,
                        trigger=trigger, idempotency_key=idempotency_key, status="queued", params=params)
    db.add(run)
    db.commit()
    return run


class JobRunner:
    def __init__(self, settings: Settings, session_factory, providers, llm_factory=None):
        self.settings = settings
        self.sf = session_factory
        self.providers = providers
        self.llm_factory = llm_factory or (lambda: build_llm(settings))
        self.pool = ThreadPoolExecutor(max_workers=settings.job_workers, thread_name_prefix="analysis")
        self.active: set[str] = set()
        self._lock = threading.Lock()

    def submit(self, analysis_id: str):
        with self._lock:
            if analysis_id in self.active:
                return None
            self.active.add(analysis_id)
        return self.pool.submit(self._run_safe, analysis_id)

    def recover(self) -> int:
        """After a restart: running jobs cannot resume mid-graph → mark failed (retryable); re-queue queued jobs."""
        with self.sf() as db:
            db.execute(update(M.AnalysisRun).where(M.AnalysisRun.status == "running")
                       .values(status="failed", error="Interrupted by server restart; use Retry."))
            queued = [r.id for r in db.scalars(select(M.AnalysisRun).where(M.AnalysisRun.status == "queued"))]
            db.commit()
        for q in queued:
            self.submit(q)
        return len(queued)

    def _run_safe(self, analysis_id: str) -> None:
        try:
            self.run(analysis_id)
        except Exception as exc:  # last-resort guard: record honestly
            log.exception("analysis %s failed", analysis_id)
            with self.sf() as db:
                run = db.get(M.AnalysisRun, analysis_id)
                run.status, run.error, run.completed_at = "failed", f"{type(exc).__name__}: {exc}", datetime.now(UTC)
                db.commit()
            EventWriter(self.sf, analysis_id)("analysis", "failed", f"Analysis failed: {type(exc).__name__}: {exc}")
        finally:
            with self._lock:
                self.active.discard(analysis_id)

    def run(self, analysis_id: str) -> None:
        emit = EventWriter(self.sf, analysis_id)
        with self.sf() as db:
            run = db.get(M.AnalysisRun, analysis_id)
            run.status, run.started_at, run.error = "running", datetime.now(UTC), None
            params = dict(run.params or {})
            ticker, user_id = run.ticker, run.user_id
            db.commit()
            emit("resolve", "started", f"Resolving company identity for {ticker}")
            res = U.resolve(db, self.providers, ticker)
            if not res["resolved"]:
                raise ValueError(res["message"])
            universe = U.universe_rows(db)
            snaps = U.snapshot_infos(db)
            prior = self._prior(db, user_id, ticker, analysis_id)
        ident = CompanyIdentity(**res["identity"])
        emit("resolve", "completed", f"{ident.name} · {ident.exchange or 'exchange n/a'} · {ident.currency or ''} · "
             f"{ident.security_type or 'type n/a'} · CIK {ident.cik or 'n/a'}", detail={"warnings": res["warnings"]})
        llm = self.llm_factory()
        mode = "llm" if llm else "deterministic"
        emit("mode", "completed", "LLM interpretation enabled" if llm else
             "No LLM configured: agents produce rule-based reports only (clearly labelled)")
        params["prior"] = prior
        graph = build_graph(self.providers, llm, emit, universe, snaps)
        state = graph.invoke({"analysis_id": analysis_id, "params": params, "identity": ident, "review_round": 0,
                              "review_log": [], "reports": {}, "committee_history": []})
        emit("save", "started", "Saving analysis snapshot, evidence and thesis")
        self.persist(analysis_id, user_id, ident, params, state, mode)
        emit("save", "completed", "Analysis saved")
        emit("analysis", "completed", f"Analysis complete: {state['final']['recommendation']}")

    @staticmethod
    def _prior(db: Session, user_id: int, ticker: str, analysis_id: str) -> dict[str, Any] | None:
        last = db.scalars(select(M.AnalysisRun).where(M.AnalysisRun.user_id == user_id, M.AnalysisRun.ticker == ticker,
                                                      M.AnalysisRun.status == "completed", M.AnalysisRun.id != analysis_id)
                          .order_by(M.AnalysisRun.created_at.desc()).limit(1)).first()
        if not last:
            return None
        return {"analysis_id": last.id, "version": last.version, "recommendation": last.recommendation,
                "confidence": last.confidence, "created_at": last.created_at.isoformat(), "reference_price": last.reference_price}

    def persist(self, analysis_id: str, user_id: int, ident: CompanyIdentity, params: dict[str, Any], st: dict[str, Any],
                mode: str) -> None:
        ctx = st["ctx"]
        comp = st["computed"]
        res = st["ingestion"]
        final = st["final"]
        dq = st["dq"]
        reports = {k: v.model_dump(mode="json") for k, v in st["reports"].items()}
        result = jsonable({
            "identity": ident.__dict__, "params": {k: v for k, v in params.items() if k != "prior"}, "prior": params.get("prior"),
            "final": final, "committee": st["committee"], "committee_history": st.get("committee_history", []),
            "review_log": st.get("review_log", []), "reports": reports, "data_quality": dq.model_dump(mode="json"),
            "assessments": [a.model_dump() for a in comp["assessments"]],
            "metrics": [m.model_dump(mode="json") for m in comp["metrics"]],
            "scores": {k: (v.model_dump() if v is not None else None) for k, v in comp["scores"].items()},
            "profile": comp["profile"].model_dump(), "peer": comp["peer"], "technicals": comp["technicals"],
            "risk": comp["risk"], "scenarios": comp["scenarios"], "portfolio": comp["portfolio"], "charts": comp["charts"],
            "thesis": st["thesis"], "data_through": ctx.data_through, "model_runs": ctx.model_runs, "mode": mode,
            "fetches": [f.__dict__ for f in res.fetches], "cross_checks": (res.financials.cross_checks if res.financials else [])[:400],
        })
        with self.sf() as db:
            run = db.get(M.AnalysisRun, analysis_id)
            run.company_name = ident.name
            run.status = "completed"
            run.mode = mode
            run.completed_at = datetime.now(UTC)
            run.result = result
            run.recommendation = final["recommendation"]
            run.confidence = final["confidence"]
            run.data_quality_status = dq.status
            pd_ = ctx.data_through.get("prices")
            run.price_date = date.fromisoformat(pd_) if pd_ else None
            run.reference_price = ctx.data_through.get("price")
            from backend.analysis.config_loader import versions

            run.config_versions = versions() | {"llm_model": ctx.model_runs[0]["model"] if ctx.model_runs and ctx.model_runs[0].get("model") else None}
            self._upsert_company(db, ident)
            for name, rep in reports.items():
                db.add(M.AgentReport(analysis_id=analysis_id, agent=name, status=rep["status"],
                                     score=(rep.get("score") or {}).get("score"), confidence=rep["confidence"],
                                     mode=rep["mode"], report=rep))
            for item in ctx.pack.items.values():
                db.add(M.EvidenceItemRow(analysis_id=analysis_id, evidence_id=item.id, category=item.category, title=item.title,
                                         payload=jsonable(item.model_dump(mode="json"))))
            if res.financials:
                for p in res.financials.all_points():
                    db.add(M.DataPointRow(analysis_id=analysis_id, dp_id=p.id, field=p.field, value=p.value, unit=p.unit,
                                          period_type=str(p.period_type.value), period_end=p.period_end,
                                          fiscal_period=p.fiscal_period, filing_date=p.filing_date, source_name=p.source_name[:200],
                                          source_url=p.source_url, source_type=str(getattr(p.source_type, "value", p.source_type)),
                                          quality=str(getattr(p.quality, "value", p.quality)), payload=jsonable(p.model_dump(mode="json"))))
            for r in st.get("review_log", []):
                db.add(M.CommitteeReview(analysis_id=analysis_id, round=r["round"], target_agent=r["target_agent"],
                                         question=r["question"], response=jsonable(r["response"]),
                                         resolved=bool(r["response"].get("resolved"))))
            for d in st["committee"].get("disagreements", []):
                db.add(M.CommitteeDisagreement(analysis_id=analysis_id, topic=d["topic"][:200], agents=d.get("agents", []),
                                               description=d["description"], nature=d.get("nature"), resolution=d.get("resolution"),
                                               resolved=bool(d.get("resolved")), evidence_ids=d.get("evidence_ids", [])))
            rec = M.Recommendation(analysis_id=analysis_id, user_id=user_id, ticker=ident.ticker,
                                   recommendation=final["recommendation"], policy_recommendation=final["policy_recommendation"],
                                   overridden=final["overridden"], confidence=final["confidence"],
                                   reference_price=run.reference_price, price_date=run.price_date,
                                   horizon=params.get("horizon"), benchmark=params.get("benchmark"), sector=ident.sector,
                                   scores=jsonable(final["scores"]))
            db.add(rec)
            db.flush()
            for n, label in HORIZONS:
                db.add(M.PerformanceObservation(recommendation_id=rec.id, horizon_sessions=n, horizon_label=label))
            # thesis: new version; previous active thesis superseded, never overwritten
            prev = db.scalars(select(M.InvestmentThesis).where(M.InvestmentThesis.user_id == user_id,
                                                               M.InvestmentThesis.ticker == ident.ticker,
                                                               M.InvestmentThesis.status == "active")).all()
            version = (db.scalar(select(func.max(M.InvestmentThesis.version)).where(
                M.InvestmentThesis.user_id == user_id, M.InvestmentThesis.ticker == ident.ticker)) or 0) + 1
            for p in prev:
                p.status = "superseded"
            th = M.InvestmentThesis(user_id=user_id, ticker=ident.ticker, analysis_id=analysis_id, version=version,
                                    status="active", summary=final["executive_summary"][:4000], overall_status="Valid")
            db.add(th)
            db.flush()
            for a in st["thesis"]:
                db.add(M.ThesisAssumption(thesis_id=th.id, key=a["key"], description=a["description"], kind=a["kind"],
                                          metric_key=a["metric_key"], comparator=a["comparator"], threshold=a["threshold"],
                                          review_threshold=a["review_threshold"], original_value=a["original_value"],
                                          current_value=a["current_value"], unit=a["unit"], status=a["status"],
                                          materiality=a["materiality"], invalidation=a["invalidation"],
                                          evidence_ids=a["evidence_ids"]))
            prior = params.get("prior")
            if prior:
                db.add(M.ThesisEvent(thesis_id=th.id, ticker=ident.ticker, event_type="reassessment",
                                     title=f"New analysis v{run.version}: {prior.get('recommendation')} → {final['recommendation']}",
                                     detail={"previous_analysis_id": prior["analysis_id"], "new_analysis_id": analysis_id,
                                             "trigger": run.trigger},
                                     materiality="medium" if prior.get("recommendation") != final["recommendation"] else "low",
                                     status_before=prior.get("recommendation"), status_after=final["recommendation"],
                                     dedupe_key=f"reassess:{analysis_id}"))
            for mr in ctx.model_runs:
                if mr.get("prompt_name") and mr.get("prompt_sha256"):
                    if not db.scalar(select(M.PromptVersion).where(M.PromptVersion.name == mr["prompt_name"],
                                                                   M.PromptVersion.sha256 == mr["prompt_sha256"])):
                        db.add(M.PromptVersion(name=mr["prompt_name"], version=mr.get("prompt_version") or "",
                                               sha256=mr["prompt_sha256"], text=load_prompt(mr["prompt_name"])[0]))
                        db.flush()
                db.add(M.ModelRun(analysis_id=analysis_id, agent=mr.get("agent", "?"), provider=mr.get("provider") or "n/a",
                                  model=mr.get("model") or "n/a", prompt_name=mr.get("prompt_name"),
                                  prompt_sha256=mr.get("prompt_sha256"), input_tokens=mr.get("input_tokens"),
                                  output_tokens=mr.get("output_tokens"), latency_ms=mr.get("latency_ms"), status=mr.get("status", "?"),
                                  stop_reason=mr.get("stop_reason"), error=mr.get("error"),
                                  guardrail_violations=mr.get("guardrail_violations") or []))
            self._source_health(db, res.fetches)
            db.commit()

    @staticmethod
    def _upsert_company(db: Session, ident: CompanyIdentity) -> None:
        sec = db.scalar(select(M.Security).where(M.Security.ticker == ident.ticker))
        comp = db.scalar(select(M.Company).where(M.Company.cik == ident.cik)) if ident.cik else None
        if comp is None and sec is not None:
            comp = sec.company
        if comp is None:
            comp = M.Company(cik=ident.cik, name=ident.name)
            db.add(comp)
        comp.name, comp.sector, comp.sub_industry, comp.country = ident.name, ident.sector, ident.sub_industry, ident.country
        db.flush()
        if sec is None:
            sec = M.Security(company_id=comp.id, ticker=ident.ticker)
            db.add(sec)
        sec.exchange, sec.currency, sec.security_type, sec.in_sp500 = ident.exchange, ident.currency, ident.security_type, ident.in_sp500

    @staticmethod
    def _source_health(db: Session, fetches) -> None:
        for f in fetches:
            row = db.scalar(select(M.DataSource).where(M.DataSource.name == f.source))
            if row is None:
                kind = "regulatory" if "SEC" in f.source else "market_data"
                note = ("Public-domain US government data; fair-access policy applies." if "SEC" in f.source else
                        "Unofficial Yahoo Finance client; Yahoo terms restrict commercial use and redistribution.")
                row = M.DataSource(name=f.source, kind=kind, licence_note=note)
                db.add(row)
                db.flush()
            if f.status in ("ok", "not_found"):
                row.last_success_at = f.retrieved_at
            elif f.status == "failed":
                row.last_failure_at, row.last_error = f.retrieved_at, (f.error or "")[:2000]
