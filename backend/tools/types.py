"""Common result type for every deterministic calculation."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


class Metric(BaseModel):
    id: str
    key: str
    label: str
    category: str
    value: float | None
    unit: str  # pct (fraction, 0.15 = 15%) | x (multiple) | ratio | USD | USD/share | shares | days | score | count
    period: str | None = None
    period_end: date | None = None
    formula: str
    inputs: list[str] = Field(default_factory=list)
    status: str = "ok"  # ok | unavailable | not_meaningful
    reason: str | None = None
    higher_is_better: bool | None = None
    source: str = "Calculated (deterministic Python)"
    notes: list[str] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "ok" and self.value is not None


def unavailable(mid: str, key: str, label: str, category: str, unit: str, formula: str, reason: str,
                period: str | None = None, inputs: list[str] | None = None,
                higher_is_better: bool | None = None) -> Metric:
    return Metric(id=mid, key=key, label=label, category=category, value=None, unit=unit, formula=formula,
                  status="unavailable", reason=reason, period=period, inputs=inputs or [],
                  higher_is_better=higher_is_better)


def not_meaningful(mid: str, key: str, label: str, category: str, unit: str, formula: str, reason: str,
                   period: str | None = None, inputs: list[str] | None = None,
                   higher_is_better: bool | None = None) -> Metric:
    return Metric(id=mid, key=key, label=label, category=category, value=None, unit=unit, formula=formula,
                  status="not_meaningful", reason=reason, period=period, inputs=inputs or [],
                  higher_is_better=higher_is_better)


def fmt_value(value: float | None, unit: str) -> str:
    if value is None:
        return "n/a"
    if unit == "pct":
        return f"{value * 100:.1f}%"
    if unit == "x":
        return f"{value:.1f}x"
    if unit in ("USD",):
        a = abs(value)
        if a >= 1e12:
            return f"${value / 1e12:.2f}T"
        if a >= 1e9:
            return f"${value / 1e9:.2f}B"
        if a >= 1e6:
            return f"${value / 1e6:.1f}M"
        return f"${value:,.0f}"
    if unit == "USD/share":
        return f"${value:.2f}"
    if unit == "shares":
        return f"{value / 1e6:,.1f}M sh"
    if unit == "days":
        return f"{value:.0f} days"
    return f"{value:.2f}"
