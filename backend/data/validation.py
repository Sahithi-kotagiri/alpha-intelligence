"""Validation and the per-analysis data-quality report.

Checks implemented (brief §6.4): company mapping, annual vs quarterly periods, currency
consistency, duplicates, missing critical fields, stale statements and prices,
restatements, adjusted vs unadjusted prices, cross-provider conflicts, zero/negative
denominators (handled in tools), and vendor values that could not be verified.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from pydantic import BaseModel, Field

from backend.data.ingestion import IngestionResult
from backend.data.provenance import Quality

CRITICAL_FIELDS = ["revenue", "net_income", "diluted_eps", "operating_cash_flow", "equity", "total_assets"]
IMPORTANT_FIELDS = ["gross_profit", "operating_income", "capex", "total_debt", "cash", "diluted_shares",
                    "current_assets", "current_liabilities", "interest_expense"]
STATEMENT_STALE_DAYS = 140  # 10-Q due ≤45 days after quarter end; >140 days means a filing is overdue/missing
PRICE_STALE_DAYS = 5


class QualityIssue(BaseModel):
    severity: str  # critical | warning | info
    code: str
    message: str
    field: str | None = None


class DataQualityReport(BaseModel):
    status: str  # good | acceptable | poor | critical
    coverage_pct: float
    latest_financial_period: str | None
    latest_financial_period_end: date | None
    latest_price_date: date | None
    missing_critical_fields: list[str] = Field(default_factory=list)
    missing_important_fields: list[str] = Field(default_factory=list)
    conflicts: list[dict[str, Any]] = Field(default_factory=list)
    restatements: list[str] = Field(default_factory=list)
    stale_sources: list[str] = Field(default_factory=list)
    failed_sources: list[dict[str, Any]] = Field(default_factory=list)
    cross_check_summary: dict[str, int] = Field(default_factory=dict)
    issues: list[QualityIssue] = Field(default_factory=list)
    blocks_buy_now: bool = False
    confidence_penalty: float = 0.0  # 0..1, subtracted (scaled) from final confidence


def validate(res: IngestionResult, as_of: date | None = None) -> DataQualityReport:
    as_of = as_of or date.today()
    issues: list[QualityIssue] = []
    fin = res.financials

    # ---- identity / mapping
    info_name = (res.info.get("longName") or res.info.get("shortName") or "").lower()
    if info_name and res.identity.name:
        first = res.identity.name.lower().split()[0].strip(",.")
        if first not in info_name and info_name.split()[0].strip(",.") not in res.identity.name.lower():
            issues.append(QualityIssue(severity="warning", code="identity_name_mismatch",
                                       message=f"Vendor name '{res.info.get('longName')}' differs from "
                                               f"registry name '{res.identity.name}'. Confirm the security."))
    qt = res.info.get("quoteType")
    if qt and qt != "EQUITY":
        issues.append(QualityIssue(severity="critical", code="not_equity", message=f"Security type is {qt}, not common equity."))
    fc, pc = res.info.get("financialCurrency"), res.info.get("currency")
    if fc and pc and fc != pc:
        issues.append(QualityIssue(severity="warning", code="currency_mismatch",
                                   message=f"Financials reported in {fc} but price quoted in {pc}; price-based "
                                           "multiples require FX conversion and are marked not meaningful."))

    # ---- failed sources
    failed = [{"source": f.source, "item": f.item, "status": f.status, "error": f.error} for f in res.fetches
              if f.status in ("failed",)]
    for f in failed:
        issues.append(QualityIssue(severity="warning", code="source_failed",
                                   message=f"{f['source']} – {f['item']} could not be retrieved: {f['error']}"))

    # ---- statements coverage
    missing_crit, missing_imp = [], []
    latest_end = None
    latest_label = None
    conflicts: list[dict[str, Any]] = []
    restatements: list[str] = []
    cc_summary: dict[str, int] = {}
    if fin is None:
        issues.append(QualityIssue(severity="critical", code="no_financials", message="No financial statements available."))
        missing_crit = CRITICAL_FIELDS[:]
    else:
        for f in CRITICAL_FIELDS:
            if fin.latest(f, "annual") is None and fin.latest(f, "quarterly") is None:
                missing_crit.append(f)
        for f in IMPORTANT_FIELDS:
            if fin.latest(f, "annual") is None and fin.latest(f, "quarterly") is None:
                missing_imp.append(f)
        rev_q = fin.latest("revenue", "quarterly") or fin.latest("revenue", "annual")
        if rev_q and rev_q.period_end:
            latest_end, latest_label = rev_q.period_end, rev_q.fiscal_period
            age = (as_of - rev_q.period_end).days
            if age > STATEMENT_STALE_DAYS:
                issues.append(QualityIssue(severity="warning", code="stale_financials",
                                           message=f"Latest financial period ends {rev_q.period_end} ({age} days ago); "
                                                   "a newer filing may be missing from the data."))
        for p in fin.all_points():
            if p.quality == Quality.CONFLICT and p.cross_check:
                conflicts.append({"id": p.id, "field": p.field, "period": p.fiscal_period, "used_value": p.value,
                                  "used_source": p.source_name, "other_value": p.original_value,
                                  "other_source": p.original_source, "difference_pct": p.cross_check.difference_pct})
            if any(n.startswith("Restated") for n in p.notes):
                restatements.append(f"{p.label} {p.fiscal_period}")
        for c in fin.cross_checks:
            cc_summary[c["status"]] = cc_summary.get(c["status"], 0) + 1
        unverified = sum(1 for p in fin.all_points() if p.quality == Quality.UNVERIFIED)
        cc_summary["unverified"] = unverified
        # duplicate periods
        for fld, s in fin.quarterly.items():
            ends = [p.period_end for p in s]
            if len(ends) != len(set(ends)):
                issues.append(QualityIssue(severity="warning", code="duplicate_period", field=fld,
                                           message=f"Duplicate quarterly periods for {fld}."))
        if not fin.sec_available:
            issues.append(QualityIssue(severity="warning", code="no_sec_crosscheck",
                                       message="SEC filings unavailable; vendor statements are unverified."))
    for f in missing_crit:
        issues.append(QualityIssue(severity="critical", code="missing_critical", field=f,
                                   message=f"Critical field '{f}' unavailable."))
    for f in missing_imp:
        issues.append(QualityIssue(severity="info", code="missing_important", field=f,
                                   message=f"Field '{f}' unavailable; dependent metrics will be marked unavailable."))
    recent_ends = sorted({c["id"].rsplit(".", 1)[-1] for c in conflicts}, reverse=True)
    for c in conflicts[:12]:
        issues.append(QualityIssue(severity="info", code="source_conflict", field=c["field"],
                                   message=f"{c['field']} {c['period']}: vendor {c['other_value']:,.4g} vs SEC "
                                           f"{c['used_value']:,.4g} ({(c['difference_pct'] or 0) * 100:.1f}% diff); SEC used."))
    # conflicts in the two most recent fiscal years of critical fields are material for the decision
    material_conflicts = [c for c in conflicts if c["field"] in CRITICAL_FIELDS and "FY" in str(c.get("period"))
                          and c["id"].rsplit(".", 1)[-1] in recent_ends[:2]]
    if conflicts:
        issues.append(QualityIssue(severity="warning" if material_conflicts else "info", code="source_conflicts_summary",
                                   message=f"{len(conflicts)} vendor/SEC conflict(s) resolved in favour of the filing"
                                           + (f"; {len(material_conflicts)} affect recent critical fields." if material_conflicts else ".")))
    defn = sum(1 for c in (fin.cross_checks if fin else []) if c["status"] == "definition_difference")
    if defn:
        issues.append(QualityIssue(severity="info", code="definition_differences",
                                   message=f"{defn} secondary line-item value(s) differ in definition between vendor and filer "
                                           "XBRL tags (vendor values kept; filing values preserved)."))
    scale = sum(1 for c in (fin.cross_checks if fin else []) if c["status"] == "unit_scale_mismatch")
    if scale:
        issues.append(QualityIssue(severity="info", code="unit_scale_mismatch",
                                   message=f"{scale} SEC value(s) appear to be tagged at the wrong scale (×1,000 or ×1,000,000); vendor values kept."))
    for r in restatements[:10]:
        issues.append(QualityIssue(severity="info", code="restatement", message=f"Restated in later filing: {r}"))

    # ---- prices
    latest_px = None
    stale = []
    if res.prices is None or res.prices.empty:
        issues.append(QualityIssue(severity="critical", code="no_prices", message="No price history available."))
    else:
        latest_px = res.prices.index[-1].date()
        if (as_of - latest_px).days > PRICE_STALE_DAYS:
            stale.append(f"prices (latest {latest_px})")
            issues.append(QualityIssue(severity="warning", code="stale_prices", message=f"Latest price is {latest_px}."))
        if len(res.prices) < 200:
            issues.append(QualityIssue(severity="warning", code="short_price_history",
                                       message=f"Only {len(res.prices)} trading days of history; long-term indicators limited."))
        if "Adj Close" not in res.prices.columns:
            issues.append(QualityIssue(severity="warning", code="unadjusted_prices",
                                       message="Adjusted close unavailable; returns exclude dividends."))
        bad = res.prices[(res.prices["Close"] <= 0) | res.prices["Close"].isna()]
        if len(bad):
            issues.append(QualityIssue(severity="warning", code="bad_prices", message=f"{len(bad)} invalid price rows."))
    if latest_end and (as_of - latest_end).days > STATEMENT_STALE_DAYS:
        stale.append(f"financial statements (latest period {latest_end})")
    if res.benchmark_prices is None:
        issues.append(QualityIssue(severity="warning", code="no_benchmark",
                                   message=f"Benchmark {res.benchmark} prices unavailable; relative metrics unavailable."))

    # ---- coverage and status
    total = len(CRITICAL_FIELDS) + len(IMPORTANT_FIELDS) + 2
    have = (len(CRITICAL_FIELDS) - len(missing_crit)) + (len(IMPORTANT_FIELDS) - len(missing_imp))
    have += 1 if latest_px else 0
    have += 1 if res.news or res.filings else 0
    coverage = round(100 * have / total, 1)
    n_crit = sum(1 for i in issues if i.severity == "critical")
    n_warn = sum(1 for i in issues if i.severity == "warning")
    if n_crit:
        status = "critical"
    elif n_warn >= 4 or coverage < 70 or material_conflicts:
        status = "poor"
    elif n_warn >= 1 or coverage < 90:
        status = "acceptable"
    else:
        status = "good"
    penalty = {"good": 0.0, "acceptable": 0.08, "poor": 0.2, "critical": 0.4}[status]
    return DataQualityReport(
        status=status, coverage_pct=coverage, latest_financial_period=latest_label,
        latest_financial_period_end=latest_end, latest_price_date=latest_px,
        missing_critical_fields=missing_crit, missing_important_fields=missing_imp, conflicts=conflicts,
        restatements=restatements, stale_sources=stale, failed_sources=failed, cross_check_summary=cc_summary,
        issues=issues, blocks_buy_now=status == "critical" or bool(material_conflicts), confidence_penalty=penalty,
    )


def is_stale(d: date | None, days: int, as_of: date | None = None) -> bool:
    return d is None or ((as_of or date.today()) - d) > timedelta(days=days)
