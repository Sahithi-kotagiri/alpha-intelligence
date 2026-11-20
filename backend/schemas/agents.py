"""Structured output schemas for LLM agents and the common report envelope.

LLM schemas contain only interpretation; every number the system relies on is computed
deterministically and passed in as evidence. Each claim carries its kind (fact,
interpretation, estimate, assumption, opinion) and the evidence IDs that support it.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

ClaimKind = Literal["fact", "interpretation", "estimate", "assumption", "opinion"]
Level = Literal["low", "medium", "high"]
Confidence = Literal["Low", "Medium", "High"]


class Claim(BaseModel):
    text: str
    kind: ClaimKind
    evidence_ids: list[str]


# ------------------------------------------------------------------ Fundamental
class MoatSource(BaseModel):
    source: Literal["switching_costs", "network_effects", "cost_advantage", "intangibles", "efficient_scale",
                    "distribution_ecosystem", "none_identified"]
    assessment: str
    evidence_ids: list[str]


class FundamentalNarrative(BaseModel):
    executive_assessment: str
    strengths: list[Claim]
    weaknesses: list[Claim]
    valuation_assessment: Claim
    moat_rating: Literal["none", "narrow", "wide", "unclear"]
    moat_sources: list[MoatSource]
    management_capital_allocation: list[Claim]
    competitor_context: list[Claim]
    key_assumptions: list[str]
    missing_information: list[str]
    confidence: Confidence
    confidence_reason: str


# ------------------------------------------------------------------ Technical
class TechnicalNarrative(BaseModel):
    trend_summary: Claim
    momentum_assessment: Claim
    relative_strength_assessment: Claim
    volume_assessment: Claim
    breakout_assessment: Claim
    entry_zone_justified: bool
    entry_zone_rationale: Claim
    invalidation_condition: Claim
    risk_level: Level
    missing_information: list[str]
    confidence: Confidence
    confidence_reason: str


# ------------------------------------------------------------------ News
class NewsClassification(BaseModel):
    evidence_id: str
    sentiment: Literal["positive", "negative", "mixed", "neutral"]
    materiality: Level
    time_horizon: Literal["short", "medium", "long"]
    affects: Literal["cash_flows", "risk", "valuation", "multiple", "none"]
    status: Literal["confirmed_fact", "company_statement", "commentary", "rumour_or_speculation"]
    summary: str
    duplicate_of: str | None = None


class NewsNarrative(BaseModel):
    classifications: list[NewsClassification]
    positive_catalysts: list[Claim]
    negative_catalysts: list[Claim]
    mixed_or_neutral: list[Claim]
    upcoming_events: list[Claim]
    priced_in_view: str
    overall_assessment: str
    coverage_limitations: list[str]
    confidence: Confidence
    confidence_reason: str


# ------------------------------------------------------------------ Risk
class RiskItem(BaseModel):
    risk: str
    category: Literal["business", "customer", "regulatory", "execution", "valuation", "balance_sheet", "market", "liquidity", "governance", "other"]
    likelihood: Level
    impact: Level
    evidence_ids: list[str]


class RiskNarrative(BaseModel):
    main_risks: list[RiskItem]
    scenario_commentary: Claim
    allocation_commentary: str
    stop_loss_considerations: str
    portfolio_commentary: str
    missing_information: list[str]
    confidence: Confidence
    confidence_reason: str


# ------------------------------------------------------------------ Devil's Advocate
class Objection(BaseModel):
    objection: str
    evidence_ids: list[str]
    mechanism: str
    likelihood: Level
    severity: Level
    what_would_weaken_it: str
    verify_before_deciding: str


class ThesisFailureEstimate(BaseModel):
    definition: str
    horizon: str
    low_pct: float
    high_pct: float
    basis: str


class DevilsAdvocateNarrative(BaseModel):
    objections: list[Objection]
    weak_assumptions: list[Claim]
    overlooked_evidence: list[Claim]
    invalidation_conditions: list[str]
    thesis_failure: ThesisFailureEstimate
    bear_case_conclusion: str


# ------------------------------------------------------------------ Committee
class DisagreementOut(BaseModel):
    topic: str
    agents: list[str]
    nature: Literal["analytical", "time_horizon", "data"]
    description: str
    evidence_ids: list[str]
    resolution: str
    resolved: bool


class ReviewQuestion(BaseModel):
    target_agent: Literal["fundamental", "technical", "news", "risk_portfolio", "devils_advocate"]
    question: str


class CommitteeNarrative(BaseModel):
    agreements: list[Claim]
    disagreements: list[DisagreementOut]
    review_questions: list[ReviewQuestion]
    evidence_adjudication: list[Claim]
    unresolved_questions: list[str]
    chosen_recommendation: Literal["Buy Now", "Wait", "Watchlist", "Reject"]
    override_reason: str
    rationale: str
    why_not_alternatives: list[str]
    limitations: list[str]


class ReviewAnswer(BaseModel):
    answer: str
    evidence_ids: list[str]
    resolved: bool
    cannot_resolve_reason: str


class FinalNarrative(BaseModel):
    executive_summary: str
    thesis: list[Claim]
    top_risks: list[Claim]
    monitoring_triggers: list[str]
    qualitative_assumptions: list[str]


class AssistantAnswer(BaseModel):
    answer: str
    citations: list[str]
    needs_fresh_data: bool
    uncertainty: str


# ------------------------------------------------------------------ envelope
class GuardrailReport(BaseModel):
    invalid_citations: list[str] = Field(default_factory=list)
    unverified_numbers: list[str] = Field(default_factory=list)
    retried: bool = False
    removed_claims: int = 0


class AgentReportOut(BaseModel):
    agent: str
    title: str
    responsibility: str
    status: str  # completed | failed | partial
    mode: str  # llm | deterministic | deterministic_fallback
    score: dict[str, Any] | None = None
    confidence: Confidence
    confidence_reason: str
    summary: str
    claims: list[Claim] = Field(default_factory=list)
    strengths: list[Claim] = Field(default_factory=list)
    weaknesses: list[Claim] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    narrative: dict[str, Any] | None = None
    deterministic: dict[str, Any] = Field(default_factory=dict)
    evidence_ids_available: int = 0
    guardrails: GuardrailReport = Field(default_factory=GuardrailReport)
    model_run: dict[str, Any] | None = None
    error: str | None = None
    data_through: dict[str, Any] = Field(default_factory=dict)
