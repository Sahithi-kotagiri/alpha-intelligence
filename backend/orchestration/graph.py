"""LangGraph workflow for one analysis.

Why a graph: the four primary analysts are independent and run in parallel (fan-out);
the Devil's Advocate depends on all four (fan-in); the committee can loop back to agents
for a bounded number of clarification rounds. LangGraph makes these dependencies and the
loop bound explicit and testable. Each node is a small function over shared state, and
every node emits real progress events.
"""

from __future__ import annotations

import operator
from datetime import date
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph

from backend.agents.base import AgentContext
from backend.agents.committee import InvestmentCommittee
from backend.agents.devils_advocate import DevilsAdvocate
from backend.agents.final_decision import FinalDecisionAgent
from backend.agents.fundamental import FundamentalAnalyst
from backend.agents.llm import LLMClient
from backend.agents.news import NewsAnalyst
from backend.agents.risk_portfolio import RiskPortfolioAnalyst
from backend.agents.technical import TechnicalAnalyst
from backend.analysis.config_loader import policy
from backend.data.ingestion import CompanyIdentity, ingest_company
from backend.data.validation import validate
from backend.intelligence.thesis import extract
from backend.orchestration.compute import run_compute

PRIMARY = {"fundamental": FundamentalAnalyst(), "technical": TechnicalAnalyst(), "news": NewsAnalyst(),
           "risk_portfolio": RiskPortfolioAnalyst()}
DEVIL = DevilsAdvocate()


def merge_dicts(a: dict, b: dict) -> dict:
    return {**(a or {}), **(b or {})}


class State(TypedDict, total=False):
    analysis_id: str
    params: dict[str, Any]
    identity: CompanyIdentity
    ingestion: Any
    dq: Any
    computed: dict[str, Any]
    ctx: Any
    reports: Annotated[dict[str, Any], merge_dicts]
    review_round: int
    review_log: Annotated[list[dict[str, Any]], operator.add]
    committee: dict[str, Any]
    committee_history: Annotated[list[dict[str, Any]], operator.add]
    final: dict[str, Any]
    thesis: list[dict[str, Any]]


def build_graph(providers, llm: LLMClient | None, emit, universe: list[dict[str, Any]],
                snapshot_infos: dict[str, dict[str, Any]]):
    def ev(stage, status, message, agent=None, detail=None):
        emit(stage, status, message, agent=agent, detail=detail)

    def n_ingest(s: State) -> State:
        res = ingest_company(providers, s["identity"], s["params"].get("benchmark", "SPY"), emit=lambda a, b, c: ev(a, b, c))
        return {"ingestion": res}

    def n_validate(s: State) -> State:
        ev("validate", "started", "Validating periods, units, currency, staleness and cross-source conflicts")
        dq = validate(s["ingestion"])
        ev("validate", "completed", f"Data quality {dq.status}: coverage {dq.coverage_pct}%, "
           f"{len(dq.conflicts)} conflicts, {len(dq.missing_critical_fields)} critical fields missing",
           detail={"status": dq.status, "cross_check": dq.cross_check_summary})
        return {"dq": dq}

    def n_compute(s: State) -> State:
        res, dq = s["ingestion"], s["dq"]
        comp = run_compute(providers, res, dq, s["params"], universe, snapshot_infos, ev)
        fd = res.financials
        snap = comp["snapshot"]
        rev = (fd.latest("revenue", "quarterly") or fd.latest("revenue", "annual")) if fd else None
        ctx = AgentContext(
            analysis_id=s["analysis_id"], ticker=res.identity.ticker, company_name=res.identity.name, params=s["params"],
            pack=comp["pack"], dq=dq, assessments=comp["assessments"],
            profile=comp["profile"].model_dump() | {"sector": res.identity.sector, "sub_industry": res.identity.sub_industry},
            peer=comp["peer"], scores=comp["scores"], technicals=comp["technicals"], risk=comp["risk"],
            scenarios=comp["scenarios"], portfolio=comp["portfolio"], prior=s["params"].get("prior"),
            data_through={"prices": str(snap.price_date) if snap.price_date else None, "price": snap.price,
                          "financials": f"{rev.fiscal_period} (period end {rev.period_end})" if rev else None,
                          "currency": res.info.get("financialCurrency") or res.identity.currency,
                          "eps_ttm": fd.ttm["diluted_eps"].value if fd and fd.ttm.get("diluted_eps") else None,
                          "analysis_date": date.today().isoformat()})
        for name, agent in PRIMARY.items():
            ev("agent", "queued", f"{agent.title}: queued", agent=name)
        ev("agent", "queued", f"{DEVIL.title}: queued (runs after the four primary analysts)", agent=DEVIL.name)
        return {"computed": comp, "ctx": ctx}

    def make_agent_node(name):
        agent = PRIMARY[name]

        def node(s: State) -> State:
            return {"reports": {name: agent.run(s["ctx"], llm, ev)}}

        return node

    def n_devil(s: State) -> State:
        ctx: AgentContext = s["ctx"]
        ctx.reports = dict(s["reports"])
        return {"reports": {"devils_advocate": DEVIL.run(ctx, llm, ev)}}

    def n_committee(s: State) -> State:
        ctx: AgentContext = s["ctx"]
        ctx.reports = dict(s["reports"])
        out = InvestmentCommittee().deliberate(ctx, llm, ev, s.get("review_round", 0), s.get("review_log", []))
        return {"committee": out, "committee_history": [out]}

    def route_after_committee(s: State) -> str:
        qs = s["committee"].get("review_questions") or []
        if qs and s.get("review_round", 0) < policy()["review_loop"]["max_rounds"] - 1:
            return "review"
        return "final"

    def n_review(s: State) -> State:
        ctx: AgentContext = s["ctx"]
        rnd = s.get("review_round", 0)
        log = []
        agents = PRIMARY | {"devils_advocate": DEVIL}
        for q in s["committee"]["review_questions"]:
            target = agents.get(q["target_agent"])
            if target is None:
                continue
            ev("review", "started", f"Committee question to {target.title}: {q['question']}", agent=q["target_agent"])
            ans = target.answer_review(ctx, q["question"], llm, ev)
            ev("review", "completed", f"{target.title} answered ({'resolved' if ans.get('resolved') else 'unresolved'})",
               agent=q["target_agent"], detail={"evidence_ids": ans.get("evidence_ids")})
            log.append({"round": rnd + 1, "target_agent": q["target_agent"], "question": q["question"],
                        "topic": q.get("topic"), "response": ans})
        return {"review_log": log, "review_round": rnd + 1}

    def n_final(s: State) -> State:
        ctx: AgentContext = s["ctx"]
        final = FinalDecisionAgent().build(ctx, s["committee"], llm, ev)
        return {"final": final}

    def n_thesis(s: State) -> State:
        ctx: AgentContext = s["ctx"]
        fund = s["reports"].get("fundamental")
        moat = None
        if fund and fund.narrative:
            moat = {"rating": fund.narrative.get("moat_rating"),
                    "evidence_ids": [e for m in fund.narrative.get("moat_sources", []) for e in m.get("evidence_ids", [])]}
        qual = s["final"]["monitoring_plan"].get("qualitative_assumptions") or []
        th = extract(ctx.assessments, ctx.technicals, qual, moat)
        ev("thesis", "completed", f"{len(th)} monitorable thesis assumptions extracted")
        return {"thesis": th}

    g = StateGraph(State)
    g.add_node("ingest", n_ingest)
    g.add_node("validate", n_validate)
    g.add_node("compute", n_compute)
    for name in PRIMARY:
        g.add_node(name, make_agent_node(name))
    g.add_node("devils_advocate", n_devil)
    g.add_node("committee", n_committee)
    g.add_node("review", n_review)
    g.add_node("final", n_final)
    g.add_node("thesis", n_thesis)
    g.add_edge(START, "ingest")
    g.add_edge("ingest", "validate")
    g.add_edge("validate", "compute")
    for name in PRIMARY:
        g.add_edge("compute", name)
    g.add_edge(list(PRIMARY), "devils_advocate")
    g.add_edge("devils_advocate", "committee")
    g.add_conditional_edges("committee", route_after_committee, {"review": "review", "final": "final"})
    g.add_edge("review", "committee")
    g.add_edge("final", "thesis")
    g.add_edge("thesis", END)
    return g.compile()
