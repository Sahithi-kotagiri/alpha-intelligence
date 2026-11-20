"""Provenance model shared by every data point and calculation.

A `DataPoint` is the atomic unit of evidence. It records not only a value but where
it came from, which period it covers, when it was filed and retrieved, and whether
it was reported by a source or calculated by us (with formula and inputs).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class SourceType(StrEnum):
    REGULATORY_FILING = "regulatory_filing"
    MARKET_DATA_VENDOR = "market_data_vendor"
    CONSENSUS_ESTIMATE = "consensus_estimate"
    NEWS = "news"
    INDEX_CONSTITUENTS = "index_constituents"
    CALCULATED = "calculated"
    USER_INPUT = "user_input"


class Quality(StrEnum):
    OK = "ok"
    MISSING = "missing"
    STALE = "stale"
    CONFLICT = "conflict"
    MINOR_DIFFERENCE = "minor_difference"
    ESTIMATE = "estimate"
    UNVERIFIED = "unverified"  # vendor value with no primary-source cross-check available
    NOT_MEANINGFUL = "not_meaningful"
    DERIVED = "derived"  # e.g. Q4 = FY - (Q1+Q2+Q3)


class PeriodType(StrEnum):
    ANNUAL = "annual"
    QUARTERLY = "quarterly"
    TTM = "ttm"
    INSTANT = "instant"
    POINT_IN_TIME = "point"
    DAILY = "daily"


def utcnow() -> datetime:
    return datetime.now(UTC)


class CrossCheck(BaseModel):
    source_name: str
    value: float | None
    difference_pct: float | None
    status: str  # match | minor_difference | conflict | unavailable
    source_url: str | None = None
    filing_date: date | None = None
    accession: str | None = None


class DataPoint(BaseModel):
    id: str
    field: str
    label: str
    value: float | None
    unit: str  # USD | USD/share | shares | ratio | percent | x | days | count
    currency: str | None = None
    period_type: PeriodType
    period_start: date | None = None
    period_end: date | None = None
    fiscal_period: str | None = None
    filing_date: date | None = None
    retrieved_at: datetime = Field(default_factory=utcnow)
    source_name: str
    source_url: str | None = None
    source_type: SourceType
    reported_or_calculated: str = "reported"  # reported | calculated | derived | estimate
    calculation_formula: str | None = None
    inputs: list[str] = Field(default_factory=list)
    quality: Quality = Quality.OK
    notes: list[str] = Field(default_factory=list)
    original_value: float | None = None
    original_source: str | None = None
    cross_check: CrossCheck | None = None
    extra: dict[str, Any] = Field(default_factory=dict)

    @property
    def available(self) -> bool:
        return self.value is not None and self.quality not in (Quality.MISSING,)


def period_label(period_type: PeriodType, end: date | None, fiscal: str | None = None) -> str:
    if fiscal:
        return fiscal
    if end is None:
        return "n/a"
    prefix = {"annual": "FY", "quarterly": "Q", "ttm": "TTM"}.get(period_type.value, "")
    return f"{prefix} ending {end.isoformat()}" if prefix else end.isoformat()
