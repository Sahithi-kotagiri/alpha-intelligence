"""Relational schema. Completed analyses are immutable: re-analysis creates a new row/version."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    display_name: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Company(Base):
    __tablename__ = "companies"
    id: Mapped[int] = mapped_column(primary_key=True)
    cik: Mapped[int | None] = mapped_column(Integer, unique=True)
    name: Mapped[str] = mapped_column(String(255))
    sector: Mapped[str | None] = mapped_column(String(120))
    sub_industry: Mapped[str | None] = mapped_column(String(160))
    country: Mapped[str | None] = mapped_column(String(80))
    fiscal_year_end_month: Mapped[int | None] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    securities: Mapped[list[Security]] = relationship(back_populates="company")


class Security(Base):
    __tablename__ = "securities"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    ticker: Mapped[str] = mapped_column(String(20), unique=True)
    exchange: Mapped[str | None] = mapped_column(String(40))
    currency: Mapped[str | None] = mapped_column(String(10))
    security_type: Mapped[str | None] = mapped_column(String(40))
    in_sp500: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    company: Mapped[Company] = relationship(back_populates="securities")


class UniverseMember(Base):
    __tablename__ = "universe_members"
    id: Mapped[int] = mapped_column(primary_key=True)
    universe: Mapped[str] = mapped_column(String(40), default="SP500")
    ticker: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(255))
    sector: Mapped[str | None] = mapped_column(String(120), index=True)
    sub_industry: Mapped[str | None] = mapped_column(String(160), index=True)
    cik: Mapped[int | None] = mapped_column(Integer)
    headquarters: Mapped[str | None] = mapped_column(String(160))
    date_added: Mapped[str | None] = mapped_column(String(20))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_url: Mapped[str | None] = mapped_column(String(500))
    __table_args__ = (UniqueConstraint("universe", "ticker"),)


class UniverseChange(Base):
    __tablename__ = "universe_changes"
    id: Mapped[int] = mapped_column(primary_key=True)
    universe: Mapped[str] = mapped_column(String(40), default="SP500")
    ticker: Mapped[str] = mapped_column(String(20))
    change: Mapped[str] = mapped_column(String(20))  # added | removed
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ScreenRun(Base):
    __tablename__ = "screen_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(20), default="queued")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    total: Mapped[int] = mapped_column(Integer, default=0)
    succeeded: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)


class UniverseSnapshot(Base):
    __tablename__ = "universe_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True)
    ticker: Mapped[str] = mapped_column(String(20), unique=True)
    screen_run_id: Mapped[int | None] = mapped_column(ForeignKey("screen_runs.id", ondelete="SET NULL"))
    info: Mapped[dict[str, Any]] = mapped_column(JSON)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="ok")
    error: Mapped[str | None] = mapped_column(Text)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class DataSource(Base):
    __tablename__ = "data_sources"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    kind: Mapped[str] = mapped_column(String(60))
    url: Mapped[str | None] = mapped_column(String(500))
    licence_note: Mapped[str | None] = mapped_column(Text)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class AnalysisRun(Base):
    __tablename__ = "analysis_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    ticker: Mapped[str] = mapped_column(String(20), index=True)
    company_name: Mapped[str | None] = mapped_column(String(255))
    version: Mapped[int] = mapped_column(Integer, default=1)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("analysis_runs.id", ondelete="SET NULL"))
    trigger: Mapped[str] = mapped_column(String(40), default="user")  # user | monitoring | retry
    idempotency_key: Mapped[str | None] = mapped_column(String(80), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued|running|completed|failed
    mode: Mapped[str | None] = mapped_column(String(30))  # llm | deterministic
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    config_versions: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    price_date: Mapped[date | None] = mapped_column(Date)
    reference_price: Mapped[float | None] = mapped_column(Float)
    recommendation: Mapped[str | None] = mapped_column(String(20))
    confidence: Mapped[float | None] = mapped_column(Float)
    data_quality_status: Mapped[str | None] = mapped_column(String(20))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    __table_args__ = (Index("ix_runs_user_ticker", "user_id", "ticker"),)


class AnalysisEvent(Base):
    __tablename__ = "analysis_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    analysis_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    stage: Mapped[str] = mapped_column(String(60))
    agent: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20))
    message: Mapped[str] = mapped_column(Text)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    __table_args__ = (UniqueConstraint("analysis_id", "seq"),)


class AgentReport(Base):
    __tablename__ = "agent_reports"
    id: Mapped[int] = mapped_column(primary_key=True)
    analysis_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True)
    agent: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20))
    score: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[str | None] = mapped_column(String(10))
    mode: Mapped[str | None] = mapped_column(String(30))
    report: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint("analysis_id", "agent"),)


class EvidenceItemRow(Base):
    __tablename__ = "evidence_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    analysis_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True)
    evidence_id: Mapped[str] = mapped_column(String(160))
    category: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    __table_args__ = (UniqueConstraint("analysis_id", "evidence_id"),)


class DataPointRow(Base):
    __tablename__ = "data_points"
    id: Mapped[int] = mapped_column(primary_key=True)
    analysis_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True)
    dp_id: Mapped[str] = mapped_column(String(160))
    field: Mapped[str] = mapped_column(String(60))
    value: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(20))
    period_type: Mapped[str] = mapped_column(String(20))
    period_end: Mapped[date | None] = mapped_column(Date)
    fiscal_period: Mapped[str | None] = mapped_column(String(60))
    filing_date: Mapped[date | None] = mapped_column(Date)
    source_name: Mapped[str] = mapped_column(String(200))
    source_url: Mapped[str | None] = mapped_column(String(500))
    source_type: Mapped[str] = mapped_column(String(40))
    quality: Mapped[str] = mapped_column(String(30))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class PriceHistory(Base):
    __tablename__ = "price_history"
    id: Mapped[int] = mapped_column(primary_key=True)
    ticker: Mapped[str] = mapped_column(String(20))
    date: Mapped[date] = mapped_column(Date)
    open: Mapped[float | None] = mapped_column(Float)
    high: Mapped[float | None] = mapped_column(Float)
    low: Mapped[float | None] = mapped_column(Float)
    close: Mapped[float | None] = mapped_column(Float)
    adj_close: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(80))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint("ticker", "date"), Index("ix_price_ticker_date", "ticker", "date"))


class CommitteeReview(Base):
    __tablename__ = "committee_reviews"
    id: Mapped[int] = mapped_column(primary_key=True)
    analysis_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True)
    round: Mapped[int] = mapped_column(Integer)
    target_agent: Mapped[str] = mapped_column(String(40))
    question: Mapped[str] = mapped_column(Text)
    response: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class CommitteeDisagreement(Base):
    __tablename__ = "committee_disagreements"
    id: Mapped[int] = mapped_column(primary_key=True)
    analysis_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True)
    topic: Mapped[str] = mapped_column(String(200))
    agents: Mapped[list[Any]] = mapped_column(JSON)
    description: Mapped[str] = mapped_column(Text)
    nature: Mapped[str | None] = mapped_column(String(40))  # analytical | time_horizon | data
    resolution: Mapped[str | None] = mapped_column(Text)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    evidence_ids: Mapped[list[Any]] = mapped_column(JSON, default=list)


class Recommendation(Base):
    __tablename__ = "recommendations"
    id: Mapped[int] = mapped_column(primary_key=True)
    analysis_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id", ondelete="CASCADE"), unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    ticker: Mapped[str] = mapped_column(String(20), index=True)
    recommendation: Mapped[str] = mapped_column(String(20))
    policy_recommendation: Mapped[str | None] = mapped_column(String(20))
    overridden: Mapped[bool] = mapped_column(Boolean, default=False)
    confidence: Mapped[float] = mapped_column(Float)
    reference_price: Mapped[float | None] = mapped_column(Float)
    price_date: Mapped[date | None] = mapped_column(Date)
    horizon: Mapped[str | None] = mapped_column(String(40))
    benchmark: Mapped[str | None] = mapped_column(String(20))
    sector: Mapped[str | None] = mapped_column(String(120))
    scores: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    user_action: Mapped[str] = mapped_column(String(20), default="unknown")  # acted | not_acted | unknown
    user_comment: Mapped[str | None] = mapped_column(Text)


class InvestmentThesis(Base):
    __tablename__ = "investment_theses"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    ticker: Mapped[str] = mapped_column(String(20), index=True)
    analysis_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | superseded
    summary: Mapped[str | None] = mapped_column(Text)
    overall_status: Mapped[str] = mapped_column(String(20), default="Valid")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    assumptions: Mapped[list[ThesisAssumption]] = relationship(back_populates="thesis", cascade="all, delete-orphan")


class ThesisAssumption(Base):
    __tablename__ = "thesis_assumptions"
    id: Mapped[int] = mapped_column(primary_key=True)
    thesis_id: Mapped[int] = mapped_column(ForeignKey("investment_theses.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(20))  # quantitative | qualitative
    metric_key: Mapped[str | None] = mapped_column(String(80))
    comparator: Mapped[str | None] = mapped_column(String(10))  # >= | <= | >
    threshold: Mapped[float | None] = mapped_column(Float)
    review_threshold: Mapped[float | None] = mapped_column(Float)
    original_value: Mapped[float | None] = mapped_column(Float)
    current_value: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="Valid")
    materiality: Mapped[str] = mapped_column(String(10), default="medium")
    invalidation: Mapped[str | None] = mapped_column(Text)
    evidence_ids: Mapped[list[Any]] = mapped_column(JSON, default=list)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    thesis: Mapped[InvestmentThesis] = relationship(back_populates="assumptions")


class ThesisEvent(Base):
    __tablename__ = "thesis_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    thesis_id: Mapped[int] = mapped_column(ForeignKey("investment_theses.id", ondelete="CASCADE"), index=True)
    assumption_id: Mapped[int | None] = mapped_column(ForeignKey("thesis_assumptions.id", ondelete="SET NULL"))
    ticker: Mapped[str] = mapped_column(String(20), index=True)
    event_type: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(Text)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    materiality: Mapped[str] = mapped_column(String(10))
    status_before: Mapped[str | None] = mapped_column(String(20))
    status_after: Mapped[str | None] = mapped_column(String(20))
    source_url: Mapped[str | None] = mapped_column(String(500))
    dedupe_key: Mapped[str | None] = mapped_column(String(200))
    reassessment_analysis_id: Mapped[str | None] = mapped_column(ForeignKey("analysis_runs.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint("thesis_id", "dedupe_key"),)


class Portfolio(Base):
    __tablename__ = "portfolios"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120), default="My portfolio")
    base_currency: Mapped[str] = mapped_column(String(10), default="USD")
    cash: Mapped[float] = mapped_column(Float, default=0.0)
    risk_tolerance: Mapped[str | None] = mapped_column(String(20))  # conservative | moderate | aggressive
    horizon: Mapped[str | None] = mapped_column(String(40))
    benchmark: Mapped[str] = mapped_column(String(20), default="SPY")
    excluded_sectors: Mapped[list[Any]] = mapped_column(JSON, default=list)
    max_position: Mapped[float | None] = mapped_column(Float)
    liquidity_needs: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    holdings: Mapped[list[PortfolioHolding]] = relationship(back_populates="portfolio", cascade="all, delete-orphan")


class PortfolioHolding(Base):
    __tablename__ = "portfolio_holdings"
    id: Mapped[int] = mapped_column(primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id", ondelete="CASCADE"), index=True)
    ticker: Mapped[str] = mapped_column(String(20))
    quantity: Mapped[float | None] = mapped_column(Float)
    market_value: Mapped[float | None] = mapped_column(Float)
    weight: Mapped[float | None] = mapped_column(Float)
    cost_basis: Mapped[float | None] = mapped_column(Float)
    portfolio: Mapped[Portfolio] = relationship(back_populates="holdings")


class WatchlistItem(Base):
    __tablename__ = "watchlists"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    ticker: Mapped[str] = mapped_column(String(20))
    note: Mapped[str | None] = mapped_column(Text)
    target_price: Mapped[float | None] = mapped_column(Float)
    alert_prefs: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    review_status: Mapped[str] = mapped_column(String(30), default="monitoring")
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint("user_id", "ticker"),)


class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    ticker: Mapped[str | None] = mapped_column(String(20))
    kind: Mapped[str] = mapped_column(String(40))
    severity: Mapped[str] = mapped_column(String(10))
    title: Mapped[str] = mapped_column(Text)
    message: Mapped[str] = mapped_column(Text)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    thesis_event_id: Mapped[int | None] = mapped_column(ForeignKey("thesis_events.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PerformanceObservation(Base):
    __tablename__ = "performance_observations"
    id: Mapped[int] = mapped_column(primary_key=True)
    recommendation_id: Mapped[int] = mapped_column(ForeignKey("recommendations.id", ondelete="CASCADE"), index=True)
    horizon_sessions: Mapped[int] = mapped_column(Integer)
    horizon_label: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    start_price: Mapped[float | None] = mapped_column(Float)
    end_price: Mapped[float | None] = mapped_column(Float)
    total_return: Mapped[float | None] = mapped_column(Float)
    benchmark_return: Mapped[float | None] = mapped_column(Float)
    excess_return: Mapped[float | None] = mapped_column(Float)
    max_drawdown: Mapped[float | None] = mapped_column(Float)
    volatility: Mapped[float | None] = mapped_column(Float)
    price_basis: Mapped[str | None] = mapped_column(String(60))
    computed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("recommendation_id", "horizon_sessions"),)


class PromptVersion(Base):
    __tablename__ = "prompt_versions"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    version: Mapped[str] = mapped_column(String(40))
    sha256: Mapped[str] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint("name", "sha256"),)


class ModelRun(Base):
    __tablename__ = "model_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    analysis_id: Mapped[str | None] = mapped_column(ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True)
    agent: Mapped[str] = mapped_column(String(40))
    provider: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(80))
    prompt_name: Mapped[str | None] = mapped_column(String(80))
    prompt_sha256: Mapped[str | None] = mapped_column(String(64))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20))
    stop_reason: Mapped[str | None] = mapped_column(String(40))
    error: Mapped[str | None] = mapped_column(Text)
    guardrail_violations: Mapped[list[Any]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class UserPreference(Base):
    __tablename__ = "user_preferences"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    default_horizon: Mapped[str] = mapped_column(String(40), default="12 months")
    default_benchmark: Mapped[str] = mapped_column(String(20), default="SPY")
    prefs: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class VoicePreference(Base):
    __tablename__ = "voice_preferences"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    provider: Mapped[str] = mapped_column(String(20), default="browser")
    voice_name: Mapped[str | None] = mapped_column(String(120))
    rate: Mapped[float] = mapped_column(Float, default=1.0)
    store_audio: Mapped[bool] = mapped_column(Boolean, default=False)


class MonitoringRun(Base):
    __tablename__ = "monitoring_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(30), default="thesis")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checked: Mapped[int] = mapped_column(Integer, default=0)
    events_created: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="running")
    error: Mapped[str | None] = mapped_column(Text)


class AssistantMessage(Base):
    __tablename__ = "assistant_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    analysis_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    citations: Mapped[list[Any]] = mapped_column(JSON, default=list)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
