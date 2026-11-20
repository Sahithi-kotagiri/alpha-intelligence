"""Deterministic business-type classification used to choose applicable metrics and thresholds."""

from __future__ import annotations

from pydantic import BaseModel, Field

from backend.analysis.config_loader import thresholds
from backend.tools.financial_metrics import latest_metric
from backend.tools.types import Metric


class BusinessProfile(BaseModel):
    primary: str  # financial | reit | utility | growth | cyclical | mature
    flags: list[str] = Field(default_factory=list)  # high_growth, unprofitable, negative_equity, cyclical, balance_sheet_financial
    rationale: list[str] = Field(default_factory=list)

    def is_(self, t: str) -> bool:
        return self.primary == t or t in self.flags


def classify(sector: str | None, sub_industry: str | None, metrics: list[Metric], net_income_ttm: float | None,
             equity_latest: float | None) -> BusinessProfile:
    cfg = thresholds()["business_types"]
    flags: list[str] = []
    why: list[str] = []
    primary = "mature"
    if sub_industry in cfg["financial_sub_industries"]:
        primary = "financial"
        why.append(f"GICS sub-industry '{sub_industry}' is a financial business model.")
        if sub_industry in cfg["balance_sheet_financials"]:
            flags.append("balance_sheet_financial")
            why.append("Balance sheet is the operating asset (deposits/float); leverage and working-capital ratios do not apply.")
    elif sector == cfg["reit_sector"]:
        primary = "reit"
        why.append("Real Estate sector: REIT economics (depreciation distorts EPS; FFO preferred, but not available from sources).")
    elif sector == cfg["utility_sector"]:
        primary = "utility"
        why.append("Utilities sector: regulated returns support structurally higher leverage.")
    cagr = latest_metric(metrics, "revenue_cagr_3y")
    if cagr is not None and cagr.ok and cagr.value >= cfg["growth_revenue_cagr_3y"]:
        if primary == "mature":
            primary = "growth"
        flags.append("growth")
        why.append(f"3-year revenue CAGR {cagr.value:.1%} ≥ {cfg['growth_revenue_cagr_3y']:.0%} growth threshold.")
        if cagr.value >= cfg["high_growth_revenue_cagr_3y"]:
            flags.append("high_growth")
    if sector in cfg["cyclical_sectors"] or sub_industry in cfg["cyclical_sub_industries"]:
        flags.append("cyclical")
        if primary == "mature":
            primary = "cyclical"
        why.append("Cyclical sector/sub-industry: trailing multiples can mislead at earnings peaks or troughs.")
    if net_income_ttm is not None and net_income_ttm < 0:
        flags.append("unprofitable")
        why.append("TTM net income is negative: earnings-based multiples are not meaningful.")
    if equity_latest is not None and equity_latest <= 0:
        flags.append("negative_equity")
        why.append("Shareholders' equity is negative (often from buybacks): D/E, ROE and P/B are not meaningful.")
    if not why:
        why.append("No special business-model rules triggered; default thresholds apply.")
    return BusinessProfile(primary=primary, flags=flags, rationale=why)
