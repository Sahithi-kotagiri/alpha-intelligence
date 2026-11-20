"""Agent base class.

An agent = role + versioned prompt + evidence scope + output schema + deterministic core.

Run sequence:
1. `scope()` selects the evidence this role may see (and cite).
2. `deterministic()` computes the rule-based part: score, tables, rule-derived strengths/
   weaknesses. This always runs, so the system is useful (and testable) without an LLM.
3. If an LLM is configured, it interprets the same evidence into the role's schema.
   Guardrails check citations (one corrective retry) and numbers.
4. If the LLM is unavailable or fails, the report is marked `deterministic` or
   `deterministic_fallback` – never presented as LLM analysis.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from backend.agents.guardrails import claim_texts, invalid_citations, strip_invalid, unverified_numbers
from backend.agents.llm import LLMClient, LLMError
from backend.analysis.assessment import Assessment
from backend.analysis.evidence import EvidenceItem, EvidencePack, render_for_prompt
from backend.data.validation import DataQualityReport
from backend.schemas.agents import AgentReportOut, Claim, GuardrailReport

log = logging.getLogger(__name__)
PROMPT_DIR = Path(__file__).resolve().parents[1] / "prompts"

Emit = Callable[..., None]


@lru_cache
def load_prompt(name: str) -> tuple[str, str, str]:
    raw = (PROMPT_DIR / f"{name}.md").read_text()
    header, _, body = raw.partition("---\n")
    version = header.strip().split(":", 1)[1].strip() if ":" in header else name
    return body.strip(), version, hashlib.sha256(raw.encode()).hexdigest()


@dataclass
class AgentContext:
    analysis_id: str
    ticker: str
    company_name: str
    params: dict[str, Any]
    pack: EvidencePack
    dq: DataQualityReport
    assessments: list[Assessment] = field(default_factory=list)
    profile: dict[str, Any] = field(default_factory=dict)
    peer: dict[str, Any] = field(default_factory=dict)
    scores: dict[str, Any] = field(default_factory=dict)
    technicals: dict[str, Any] | None = None
    risk: dict[str, Any] | None = None
    scenarios: dict[str, Any] | None = None
    portfolio: dict[str, Any] | None = None
    reports: dict[str, AgentReportOut] = field(default_factory=dict)
    prior: dict[str, Any] | None = None
    data_through: dict[str, Any] = field(default_factory=dict)
    model_runs: list[dict[str, Any]] = field(default_factory=list)

    def assessment(self, key: str) -> Assessment | None:
        return next((a for a in self.assessments if a.metric_key == key), None)


def confidence_from(coverage: float, dq_status: str) -> tuple[str, str]:
    if coverage >= 0.85 and dq_status in ("good", "acceptable"):
        return "High", f"{coverage:.0%} of rubric weight has usable data and data quality is {dq_status}."
    if coverage >= 0.6 and dq_status != "critical":
        return "Medium", f"{coverage:.0%} of rubric weight has usable data; data quality is {dq_status}."
    return "Low", f"Only {coverage:.0%} of rubric weight has usable data; data quality is {dq_status}."


def assessment_claim(a: Assessment, kind: str = "interpretation") -> Claim:
    peer = ""
    if a.peer and a.peer.percentile is not None:
        peer = f" — better than {a.peer.percentile:.0%} of {a.peer.n} {a.peer.group} peers"
    ov = f" ({a.overrides[0]})" if a.overrides else ""
    return Claim(text=f"{a.label} {a.display}{peer}; rule verdict: {a.verdict}{ov}.", kind=kind,
                 evidence_ids=[f"asm.{a.metric_key}"] + ([a.metric_id] if a.metric_id else []))


class BaseAgent:
    name = "base"
    title = "Agent"
    responsibility = ""
    prompt_name = "common"
    schema: type[BaseModel] = BaseModel
    max_tokens: int | None = None

    def scope(self, ctx: AgentContext) -> list[EvidenceItem]:
        return list(ctx.pack.items.values())

    def deterministic(self, ctx: AgentContext) -> dict[str, Any]:
        raise NotImplementedError

    def context_text(self, ctx: AgentContext, det: dict[str, Any]) -> str:
        return ""

    def apply_narrative(self, report: AgentReportOut, narrative: dict[str, Any], ctx: AgentContext) -> None:
        """Map role-specific narrative fields onto the common envelope."""
        report.summary = narrative.get("executive_assessment") or narrative.get("overall_assessment") or report.summary
        report.confidence = narrative.get("confidence", report.confidence)
        report.confidence_reason = narrative.get("confidence_reason", report.confidence_reason)
        report.missing_information = sorted(set(report.missing_information + narrative.get("missing_information", [])))

    # -------------------------------------------------------------- run
    def build_user_prompt(self, ctx: AgentContext, items: list[EvidenceItem], det: dict[str, Any]) -> str:
        return (f"Company: {ctx.company_name} ({ctx.ticker}). Investment horizon: {ctx.params.get('horizon')}. "
                f"Benchmark: {ctx.params.get('benchmark')}.\nData through: {json.dumps(ctx.data_through, default=str)}\n\n"
                f"Deterministic context (computed by tested Python rules):\n{self.context_text(ctx, det)}\n\n"
                f"Evidence you may cite ({len(items)} items):\n{render_for_prompt(items)}")

    def run(self, ctx: AgentContext, llm: LLMClient | None, emit: Emit) -> AgentReportOut:
        emit("agent", "retrieving_evidence", f"{self.title}: assembling role-scoped evidence", agent=self.name)
        items = self.scope(ctx)
        allowed = {i.id for i in items}
        det = self.deterministic(ctx)
        conf, conf_reason = det.get("confidence", ("Medium", ""))
        report = AgentReportOut(
            agent=self.name, title=self.title, responsibility=self.responsibility, status="completed",
            mode="deterministic", score=det["score"].model_dump() if det.get("score") is not None else None,
            confidence=conf, confidence_reason=conf_reason, summary=det.get("summary", ""), claims=det.get("claims", []),
            strengths=det.get("strengths", []), weaknesses=det.get("weaknesses", []),
            missing_information=det.get("missing", []), deterministic=det.get("tables", {}),
            evidence_ids_available=len(items), data_through=ctx.data_through,
        )
        if llm is None:
            report.summary = "[Rule-based report; no LLM configured] " + report.summary
            emit("agent", "completed", f"{self.title}: rule-based report completed (no LLM configured)", agent=self.name,
                 detail={"score": report.score.get("score") if report.score else None, "mode": report.mode})
            return report
        emit("agent", "analysing", f"{self.title}: LLM interpretation of {len(items)} evidence items", agent=self.name)
        system_common, v_common, sha_common = load_prompt("common")
        system_role, v_role, sha_role = load_prompt(self.prompt_name)
        system = f"{system_common}\n\n{system_role}"
        user = self.build_user_prompt(ctx, items, det)
        guard = GuardrailReport()
        try:
            res = llm.structured(system, user, self.schema, self.max_tokens)
            bad = invalid_citations(res.parsed, allowed)
            if bad:
                guard.retried = True
                fix = (f"\n\nYour previous answer cited evidence IDs that do not exist in the evidence list: {bad[:20]}. "
                       "Answer again citing only IDs from the list above.")
                res2 = llm.structured(system, user + fix, self.schema, self.max_tokens)
                ctx.model_runs.append(self._run_meta(res, v_role, sha_role, "retried", bad))
                res = res2
                bad = invalid_citations(res.parsed, allowed)
            narrative, unsupported = strip_invalid(res.parsed, allowed)
            guard.invalid_citations = bad
            guard.removed_claims = unsupported
            guard.unverified_numbers = unverified_numbers(claim_texts(narrative), items)
            ctx.model_runs.append(self._run_meta(res, v_role, sha_role, "ok", bad))
            report.mode = "llm"
            report.narrative = narrative
            report.model_run = {"provider": res.provider, "model": res.model, "prompt_version": v_role,
                                "prompt_sha256": sha_role, "common_prompt_version": v_common,
                                "input_tokens": res.input_tokens, "output_tokens": res.output_tokens, "latency_ms": res.latency_ms}
            report.guardrails = guard
            self.apply_narrative(report, narrative, ctx)
            emit("agent", "completed", f"{self.title}: completed", agent=self.name,
                 detail={"score": report.score.get("score") if report.score else None, "mode": "llm",
                         "invalid_citations": len(bad), "unverified_numbers": len(guard.unverified_numbers)})
        except LLMError as exc:
            log.warning("%s LLM failed: %s", self.name, exc)
            ctx.model_runs.append({"agent": self.name, "status": "failed", "error": str(exc), "prompt_version": v_role,
                                   "prompt_sha256": sha_role, "stop_reason": exc.stop_reason})
            report.mode = "deterministic_fallback"
            report.status = "partial"
            report.error = f"LLM interpretation failed: {exc}"
            report.summary = "[Rule-based report; LLM step failed] " + report.summary
            emit("agent", "failed", f"{self.title}: LLM step failed ({exc}); rule-based report kept", agent=self.name)
        return report

    def _run_meta(self, res, version, sha, status, bad) -> dict[str, Any]:
        return {"agent": self.name, "provider": res.provider, "model": res.model, "prompt_name": self.prompt_name,
                "prompt_version": version, "prompt_sha256": sha, "input_tokens": res.input_tokens,
                "output_tokens": res.output_tokens, "latency_ms": res.latency_ms, "status": status,
                "stop_reason": res.stop_reason, "guardrail_violations": bad}

    def answer_review(self, ctx: AgentContext, question: str, llm: LLMClient | None, emit: Emit) -> dict[str, Any]:
        """Bounded committee clarification. Deterministic mode returns the relevant rule evidence."""
        items = self.scope(ctx)
        allowed = {i.id for i in items}
        emit("agent", "reviewing", f"{self.title}: answering committee question", agent=self.name)
        if llm is None:
            return self.deterministic_review(ctx, question)
        from backend.schemas.agents import ReviewAnswer

        system = load_prompt("common")[0] + "\n\n" + load_prompt(self.prompt_name)[0] + "\n\n" + load_prompt("review")[0]
        report = ctx.reports.get(self.name)
        user = (f"Committee question: {question}\n\nYour earlier report summary: {report.summary if report else ''}\n\n"
                f"Evidence:\n{render_for_prompt(items)}")
        try:
            res = llm.structured(system, user, ReviewAnswer, 4000)
            data, _ = strip_invalid(res.parsed, allowed)
            ctx.model_runs.append(self._run_meta(res, load_prompt("review")[1], load_prompt("review")[2], "ok", []))
            data["mode"] = "llm"
            return data
        except LLMError as exc:
            return {"answer": f"Could not answer: LLM error ({exc}).", "evidence_ids": [], "resolved": False,
                    "cannot_resolve_reason": "LLM call failed", "mode": "failed"}

    def deterministic_review(self, ctx: AgentContext, question: str) -> dict[str, Any]:
        return {"answer": "No rule-based answer is defined for this question.", "evidence_ids": [], "resolved": False,
                "cannot_resolve_reason": "outside rule coverage", "mode": "deterministic"}
