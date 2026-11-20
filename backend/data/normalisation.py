"""Normalise vendor statements and SEC XBRL facts into one auditable representation.

Key rules (documented in docs/METHODOLOGY.md §2):

1. Field mapping. Each canonical field (e.g. `revenue`) maps to an ordered list of
   yfinance line items and SEC us-gaap concepts. The first available wins.
2. Periods. Annual = duration 350–380 days; quarterly = 80–100 days. SEC 10-Q cash
   flows are year-to-date, so quarters are *derived* as differences of consecutive
   cumulative values (Q2 = H1 − Q1, Q4 = FY − 9M). Derived values are labelled.
3. Restatements. When SEC has several filings for the same period, the latest filed
   value is used and the original is preserved in `original_value` with a note.
4. Cross-check and source priority. For periods both sources cover:
   |diff| ≤ 1% → match; ≤ 5% → minor difference (vendor value kept);
   > 5% → conflict: the SEC filing value is used (primary source outranks vendor),
   the vendor value is preserved, and the data point is flagged `conflict`.
   Vendor-only values are flagged `unverified`. SEC-only periods extend history.
5. Nothing is estimated silently. Missing values stay missing.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd

from backend.data.providers.sec_edgar import SecEdgarProvider
from backend.data.provenance import CrossCheck, DataPoint, PeriodType, Quality, SourceType, utcnow

# canonical field -> (label, unit, kind, yfinance rows, sec concepts, abs_value)
FieldSpec = tuple[str, str, str, list[str], list[str], bool]

FIELDS: dict[str, FieldSpec] = {
    # ---- income statement (duration)
    "revenue": ("Revenue", "USD", "flow", ["Total Revenue", "Operating Revenue"],
                ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet",
                 "RevenueFromContractWithCustomerIncludingAssessedTax"], False),
    "cost_of_revenue": ("Cost of revenue", "USD", "flow", ["Cost Of Revenue", "Reconciled Cost Of Revenue"],
                        ["CostOfRevenue", "CostOfGoodsAndServicesSold"], False),
    "gross_profit": ("Gross profit", "USD", "flow", ["Gross Profit"], ["GrossProfit"], False),
    "rnd": ("Research & development", "USD", "flow", ["Research And Development"],
            ["ResearchAndDevelopmentExpense"], False),
    "sga": ("Selling, general & admin", "USD", "flow", ["Selling General And Administration"],
            ["SellingGeneralAndAdministrativeExpense"], False),
    "operating_income": ("Operating income", "USD", "flow", ["Operating Income", "Total Operating Income As Reported"],
                         ["OperatingIncomeLoss"], False),
    "ebit": ("EBIT", "USD", "flow", ["EBIT"], [], False),
    "ebitda": ("EBITDA", "USD", "flow", ["EBITDA", "Normalized EBITDA"], [], False),
    "interest_expense": ("Interest expense", "USD", "flow", ["Interest Expense", "Interest Expense Non Operating"],
                         ["InterestExpense", "InterestExpenseNonoperating"], True),
    "pretax_income": ("Pre-tax income", "USD", "flow", ["Pretax Income"],
                      ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest"], False),
    "tax_provision": ("Income tax expense", "USD", "flow", ["Tax Provision"], ["IncomeTaxExpenseBenefit"], False),
    "net_income": ("Net income (to common)", "USD", "flow",
                   ["Net Income Common Stockholders", "Net Income", "Net Income From Continuing Operation Net Minority Interest"],
                   ["NetIncomeLoss", "NetIncomeLossAvailableToCommonStockholdersBasic"], False),
    "diluted_eps": ("Diluted EPS", "USD/share", "flow", ["Diluted EPS"], ["EarningsPerShareDiluted"], False),
    "basic_eps": ("Basic EPS", "USD/share", "flow", ["Basic EPS"], ["EarningsPerShareBasic"], False),
    "diluted_shares": ("Diluted weighted-average shares", "shares", "flow", ["Diluted Average Shares"],
                       ["WeightedAverageNumberOfDilutedSharesOutstanding"], False),
    # ---- cash flow (duration)
    "operating_cash_flow": ("Operating cash flow", "USD", "flow",
                            ["Operating Cash Flow", "Cash Flow From Continuing Operating Activities"],
                            ["NetCashProvidedByUsedInOperatingActivities",
                             "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"], False),
    "capex": ("Capital expenditure (spend, positive)", "USD", "flow", ["Capital Expenditure", "Purchase Of PPE"],
              ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"], True),
    "vendor_free_cash_flow": ("Free cash flow (vendor-reported)", "USD", "flow", ["Free Cash Flow"], [], False),
    "sbc": ("Stock-based compensation", "USD", "flow", ["Stock Based Compensation"], ["ShareBasedCompensation"], False),
    "dna": ("Depreciation & amortisation", "USD", "flow",
            ["Depreciation And Amortization", "Depreciation Amortization Depletion", "Reconciled Depreciation"],
            ["DepreciationDepletionAndAmortization", "DepreciationAndAmortization"], True),
    "dividends_paid": ("Dividends paid", "USD", "flow", ["Cash Dividends Paid", "Common Stock Dividend Paid"],
                       ["PaymentsOfDividendsCommonStock", "PaymentsOfDividends"], True),
    "buybacks": ("Share repurchases", "USD", "flow", ["Repurchase Of Capital Stock", "Common Stock Payments"],
                 ["PaymentsForRepurchaseOfCommonStock"], True),
    "stock_issuance": ("Share issuance", "USD", "flow", ["Common Stock Issuance", "Issuance Of Capital Stock"],
                       ["ProceedsFromIssuanceOfCommonStock"], True),
    "acquisitions": ("Acquisitions (cash paid)", "USD", "flow", ["Purchase Of Business"],
                     ["PaymentsToAcquireBusinessesNetOfCashAcquired"], True),
    "change_in_working_capital": ("Change in working capital", "USD", "flow", ["Change In Working Capital"], [], False),
    # ---- balance sheet (instant)
    "total_assets": ("Total assets", "USD", "instant", ["Total Assets"], ["Assets"], False),
    "total_liabilities": ("Total liabilities", "USD", "instant", ["Total Liabilities Net Minority Interest"],
                          ["Liabilities"], False),
    "equity": ("Shareholders' equity", "USD", "instant", ["Stockholders Equity", "Common Stock Equity"],
               ["StockholdersEquity"], False),
    "total_debt": ("Total debt (incl. finance leases)", "USD", "instant", ["Total Debt"], [], False),
    "long_term_debt": ("Long-term debt", "USD", "instant", ["Long Term Debt"], ["LongTermDebtNoncurrent"], False),
    "cash": ("Cash & equivalents", "USD", "instant", ["Cash And Cash Equivalents"],
             ["CashAndCashEquivalentsAtCarryingValue"], False),
    "cash_and_st_investments": ("Cash, equivalents & short-term investments", "USD", "instant",
                                ["Cash Cash Equivalents And Short Term Investments"], [], False),
    "current_assets": ("Current assets", "USD", "instant", ["Current Assets"], ["AssetsCurrent"], False),
    "current_liabilities": ("Current liabilities", "USD", "instant", ["Current Liabilities"], ["LiabilitiesCurrent"], False),
    "inventory": ("Inventory", "USD", "instant", ["Inventory"], ["InventoryNet"], False),
    "receivables": ("Receivables", "USD", "instant", ["Accounts Receivable", "Receivables"],
                    ["AccountsReceivableNetCurrent"], False),
    "goodwill_intangibles": ("Goodwill & intangibles", "USD", "instant", ["Goodwill And Other Intangible Assets"], [], False),
    "retained_earnings": ("Retained earnings", "USD", "instant", ["Retained Earnings"],
                          ["RetainedEarningsAccumulatedDeficit"], False),
    "shares_outstanding": ("Shares outstanding (balance sheet)", "shares", "instant",
                           ["Ordinary Shares Number", "Share Issued"], [], False),
}

UNIT_FOR_SEC = {"USD": "USD", "USD/share": "USD/shares", "shares": "shares"}

# Fields whose SEC concept is definitionally the same as the vendor line item: on a >5% conflict the
# filing value is used. For the remaining fields filers' XBRL tagging and vendor definitions differ
# (e.g. D&A, SG&A, cost of revenue); a difference there is recorded as a definition difference, the
# vendor value is kept for cross-company consistency, and the filing value is preserved.
TIER1_FIELDS = {"revenue", "net_income", "diluted_eps", "basic_eps", "operating_income", "gross_profit",
                "operating_cash_flow", "capex", "total_assets", "total_liabilities", "equity", "cash", "current_assets",
                "current_liabilities", "diluted_shares"}
# Revenue: several concepts can be tagged for the same period (ASC 606 contract revenue vs total revenues);
# total revenue is the largest reported revenue concept for that period.
MAX_ACROSS_CONCEPTS = {"revenue"}
ANNUAL_DAYS = (350, 380)
QUARTER_DAYS = (80, 100)


def _f(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) or math.isinf(x) else x


def _d(s: str | date | datetime | pd.Timestamp) -> date:
    if isinstance(s, pd.Timestamp):
        return s.date()
    if isinstance(s, datetime):
        return s.date()
    if isinstance(s, date):
        return s
    return date.fromisoformat(str(s)[:10])


def effective_month(d: date) -> tuple[int, int]:
    """52/53-week fiscal years end a few days into a month; treat as previous month."""
    if d.day <= 7:
        prev = d.replace(day=1) - timedelta(days=1)
        return prev.year, prev.month
    return d.year, d.month


def fiscal_label(end: date, fye_month: int | None, period_type: PeriodType) -> str:
    y, m = effective_month(end)
    if fye_month is None:
        return f"{'FY' if period_type == PeriodType.ANNUAL else 'Q'} ending {end.isoformat()}"
    fy = y if m <= fye_month else y + 1
    if period_type == PeriodType.ANNUAL:
        return f"FY{fy}"
    q = ((m - fye_month - 1) % 12) // 3 + 1
    return f"Q{q} FY{fy}"


@dataclass
class SecFact:
    start: date | None
    end: date
    value: float
    accession: str
    form: str
    filed: date
    concept: str
    original_value: float | None = None
    original_filed: date | None = None
    derived: bool = False
    derivation: str | None = None


@dataclass
class FinancialData:
    ticker: str
    currency: str | None
    fye_month: int | None
    annual: dict[str, list[DataPoint]] = field(default_factory=dict)
    quarterly: dict[str, list[DataPoint]] = field(default_factory=dict)
    ttm: dict[str, DataPoint] = field(default_factory=dict)
    latest_instant: dict[str, DataPoint] = field(default_factory=dict)
    cross_checks: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    sec_available: bool = False

    def series(self, fld: str, period: str = "annual") -> list[DataPoint]:
        return (self.annual if period == "annual" else self.quarterly).get(fld, [])

    def latest(self, fld: str, period: str = "annual") -> DataPoint | None:
        s = [p for p in self.series(fld, period) if p.value is not None]
        return s[-1] if s else None

    def all_points(self) -> list[DataPoint]:
        pts = [p for s in self.annual.values() for p in s] + [p for s in self.quarterly.values() for p in s]
        return pts + list(self.ttm.values())


# --------------------------------------------------------------------------- SEC parsing
def _sec_entries(facts: dict, concept: str, unit: str) -> list[dict]:
    node = facts.get("facts", {}).get("us-gaap", {}).get(concept)
    if not node:
        return []
    return node.get("units", {}).get(unit, [])


def _dedupe_latest(entries: list[dict], concept: str, as_of: date | None = None) -> dict[tuple[date | None, date], SecFact]:
    """Keep the latest-filed value per (start, end); remember the original if restated.

    `as_of` restricts to filings filed on or before that date (point-in-time view for backtests).
    """
    grouped: dict[tuple, list[dict]] = defaultdict(list)
    for e in entries:
        if as_of is not None and _d(e["filed"]) > as_of:
            continue
        form = str(e.get("form", ""))
        if not form.startswith(("10-K", "10-Q", "20-F", "40-F", "8-K")) or form.startswith("8-K"):
            continue
        start = _d(e["start"]) if e.get("start") else None
        grouped[(start, _d(e["end"]))].append(e)
    out = {}
    for key, rows in grouped.items():
        rows = sorted(rows, key=lambda r: (r["filed"], r.get("accn", "")))
        latest, first = rows[-1], rows[0]
        v_latest, v_first = _f(latest["val"]), _f(first["val"])
        if v_latest is None:
            continue
        restated = v_first is not None and abs(v_latest - v_first) > max(1e-9, abs(v_first) * 1e-4)
        split_hint = None
        if restated and v_first and v_latest:
            ratio = v_first / v_latest if abs(v_first) > abs(v_latest) else v_latest / v_first
            for k in (2, 3, 4, 5, 7, 8, 10, 15, 20):
                if abs(ratio - k) / k < 0.03:
                    split_hint = f"ratio ≈{k}:1, consistent with a stock split adjustment"
        out[key] = SecFact(
            start=key[0], end=key[1], value=v_latest, accession=latest["accn"], form=latest["form"],
            filed=_d(latest["filed"]), concept=concept,
            original_value=v_first if restated else None, original_filed=_d(first["filed"]) if restated else None,
            derivation=split_hint,
        )
    return out


def sec_field_series(facts: dict, spec: FieldSpec, as_of: date | None = None) -> tuple[dict[date, SecFact], dict[date, SecFact]]:
    """Return (annual_by_end, quarterly_by_end) for one canonical field (optionally point-in-time)."""
    _, unit, kind, _, concepts, absval = spec
    sec_unit = UNIT_FOR_SEC.get(unit, unit)
    merged: dict[tuple, SecFact] = {}
    field_name = next((k for k, v in FIELDS.items() if v is spec), None)
    for concept in concepts:
        for k, v in _dedupe_latest(_sec_entries(facts, concept, sec_unit), concept, as_of).items():
            if field_name in MAX_ACROSS_CONCEPTS and k in merged:
                if abs(v.value) > abs(merged[k].value):
                    merged[k] = v
                continue
            merged.setdefault(k, v)  # earlier concepts take priority for the same period
    if absval:
        for v in merged.values():
            v.value = abs(v.value)
    annual: dict[date, SecFact] = {}
    quarterly: dict[date, SecFact] = {}
    if kind == "instant":
        for (start, end), v in merged.items():
            if start is None:
                quarterly[end] = v
        return annual, quarterly

    by_start: dict[date, list[SecFact]] = defaultdict(list)
    for (start, end), v in merged.items():
        if start is None:
            continue
        days = (end - start).days
        if ANNUAL_DAYS[0] <= days <= ANNUAL_DAYS[1]:
            annual[end] = v
        if QUARTER_DAYS[0] <= days <= QUARTER_DAYS[1]:
            quarterly[end] = v
        by_start[start].append(v)
    # derive missing quarters from year-to-date cumulative values sharing a start date
    for start, facts_ in by_start.items():
        chain = sorted([f for f in facts_ if (f.end - start).days <= ANNUAL_DAYS[1]], key=lambda f: f.end)
        for prev, cur in zip(chain, chain[1:], strict=False):
            gap = (cur.end - prev.end).days
            if not (QUARTER_DAYS[0] <= gap <= QUARTER_DAYS[1]) or cur.end in quarterly:
                continue
            if spec[1] == "shares":
                continue  # share counts are averages; differences are meaningless
            val = round(cur.value - prev.value, 6 if spec[1] == "USD/share" else 2)
            quarterly[cur.end] = SecFact(
                start=prev.end + timedelta(days=1), end=cur.end, value=val, accession=cur.accession, form=cur.form,
                filed=cur.filed, concept=cur.concept, derived=True,
                derivation=f"{cur.concept} cumulative to {cur.end} minus cumulative to {prev.end}"
                + (" (EPS by subtraction is approximate: share counts differ between periods)"
                   if spec[1] == "USD/share" else ""),
            )
    return annual, quarterly


# --------------------------------------------------------------------------- vendor parsing
def _vendor_series(df: pd.DataFrame, rows: list[str], absval: bool) -> dict[date, float | None]:
    if df is None or df.empty:
        return {}
    for r in rows:
        if r in df.index:
            s = df.loc[r]
            if isinstance(s, pd.DataFrame):
                s = s.iloc[0]
            out = {}
            for col, v in s.items():
                fv = _f(v)
                out[_d(col)] = abs(fv) if (fv is not None and absval) else fv
            if any(v is not None for v in out.values()):
                return out
    return {}


def _match(end: date, candidates: dict[date, Any], tol_days: int = 10) -> date | None:
    best = None
    for c in candidates:
        diff = abs((c - end).days)
        if diff <= tol_days and (best is None or diff < abs((best - end).days)):
            best = c
    return best


def scale_mismatch(vendor: float, primary: float) -> float | None:
    """Return the factor if the two values differ by ~10^3 or ~10^6 (a units/scale error in one source)."""
    if not vendor or not primary:
        return None
    r = abs(vendor / primary)
    for f in (1e3, 1e6, 1e-3, 1e-6):
        if abs(r / f - 1) < 0.02:
            return f
    return None


def compare(vendor: float, primary: float) -> tuple[float | None, str]:
    if primary == 0:
        return None, ("match" if vendor == 0 else "conflict")
    if scale_mismatch(vendor, primary):
        return None, "unit_scale_mismatch"
    diff = abs(vendor - primary) / abs(primary)
    if diff <= 0.01:
        return diff, "match"
    if diff <= 0.05:
        return diff, "minor_difference"
    return diff, "conflict"


def build_financials(
    ticker: str,
    statements: Any,
    sec_facts: dict | None,
    cik: int | None,
    currency: str | None,
) -> FinancialData:
    """Merge vendor statements with SEC facts into labelled, cross-checked series."""
    vendor_frames = {
        ("flow", "annual"): [statements.income_annual, statements.cashflow_annual],
        ("flow", "quarterly"): [statements.income_quarterly, statements.cashflow_quarterly],
        ("instant", "annual"): [statements.balance_annual],
        ("instant", "quarterly"): [statements.balance_quarterly],
    }
    # fiscal-year-end month from vendor annual columns (fallback: SEC annual revenue)
    fye_month = None
    if statements.income_annual is not None and not statements.income_annual.empty:
        fye_month = effective_month(_d(statements.income_annual.columns[0]))[1]
    fd = FinancialData(ticker=ticker, currency=currency, fye_month=fye_month, sec_available=bool(sec_facts))
    retrieved = datetime.fromisoformat(statements.retrieved_at) if statements.retrieved_at else utcnow()

    for fld, spec in FIELDS.items():
        label, unit, kind, yrows, concepts, absval = spec
        sec_annual, sec_quarterly = sec_field_series(sec_facts, spec) if (sec_facts and concepts) else ({}, {})
        if fye_month is None and sec_annual:
            fye_month = effective_month(max(sec_annual))[1]
            fd.fye_month = fye_month
        for period in ("annual", "quarterly"):
            ptype = PeriodType.ANNUAL if period == "annual" else PeriodType.QUARTERLY
            if kind == "instant":
                ptype = PeriodType.INSTANT
            vendor: dict[date, float | None] = {}
            for frame in vendor_frames[(kind, period)]:
                for k, v in _vendor_series(frame, yrows, absval).items():
                    if vendor.get(k) is None:
                        vendor[k] = v
            sec = sec_annual if (period == "annual" and kind == "flow") else sec_quarterly
            if kind == "instant" and period == "annual":
                sec = {e: f for e, f in sec_quarterly.items() if fye_month and effective_month(e)[1] == fye_month}
            points: list[DataPoint] = []
            used_sec: set[date] = set()
            scale_fix: float | None = None
            for end in sorted(vendor):
                v = vendor[end]
                m = _match(end, sec) if sec else None
                sf = sec.get(m) if m else None
                flabel = fiscal_label(end, fye_month, PeriodType.ANNUAL if period == "annual" else PeriodType.QUARTERLY)
                dp = DataPoint(
                    id=f"fin.{fld}.{'A' if period == 'annual' else 'Q'}{end.isoformat()}",
                    field=fld, label=label, value=v, unit=unit, currency=currency if unit.startswith("USD") else None,
                    period_type=ptype, period_end=end, fiscal_period=flabel,
                    retrieved_at=retrieved, source_name=statements.source_name, source_url=statements.source_url,
                    source_type=SourceType.MARKET_DATA_VENDOR,
                )
                if v is None:
                    dp.quality = Quality.MISSING
                    dp.notes.append("Vendor returned no value for this period.")
                if sf is not None:
                    used_sec.add(m)
                    dp.filing_date = sf.filed
                    sec_url = SecEdgarProvider.filing_url(cik, sf.accession) if cik else None
                    if v is None:
                        _apply_sec(dp, sf, sec_url, "Vendor value missing; SEC filing value used.")
                    else:
                        diff, status = compare(v, sf.value)
                        dp.cross_check = CrossCheck(source_name=f"SEC EDGAR XBRL ({sf.concept})", value=sf.value,
                                                    difference_pct=diff, status=status, source_url=sec_url,
                                                    filing_date=sf.filed, accession=sf.accession)
                        if status == "unit_scale_mismatch":
                            scale_fix = scale_mismatch(v, sf.value)
                            dp.quality = Quality.UNVERIFIED
                            dp.notes.append(f"SEC value {sf.value:,.4g} differs from vendor by a factor of "
                                            f"{scale_mismatch(v, sf.value):g}: a units/scale error in the filing's XBRL "
                                            "tagging; vendor value kept.")
                        elif status == "conflict" and fld == "revenue" and sf.concept.startswith("RevenueFromContractWithCustomer") \
                                and v > sf.value:
                            status = "definition_difference"
                            dp.quality = Quality.MINOR_DIFFERENCE
                            dp.notes.append(f"SEC concept {sf.concept} ({sf.value:,.4g}) is ASC 606 contract revenue, a component "
                                            f"that can exclude other revenue (e.g. leases, derivatives); vendor total {v:,.4g} kept.")
                            dp.cross_check.status = status
                        elif status == "conflict" and fld not in TIER1_FIELDS:
                            status = "definition_difference"
                            dp.quality = Quality.MINOR_DIFFERENCE
                            dp.notes.append(f"Vendor {v:,.4g} vs SEC {sf.value:,.4g} ({(diff or 0) * 100:.1f}% apart): this line "
                                            "item is defined differently by filers' XBRL tags and the vendor; vendor value "
                                            "kept for cross-company consistency, filing value preserved in the cross-check.")
                            dp.cross_check.status = status
                        elif status == "conflict":
                            dp.original_value, dp.original_source = v, statements.source_name
                            _apply_sec(dp, sf, sec_url, f"Vendor value {v:,.4g} differs from SEC filing by "
                                       f"{(diff or 0) * 100:.1f}%; SEC value used per source-priority rule.")
                            dp.quality = Quality.CONFLICT
                        elif status == "minor_difference":
                            dp.quality = Quality.MINOR_DIFFERENCE
                            dp.notes.append(f"Vendor and SEC differ by {(diff or 0) * 100:.2f}% (≤5%); vendor value kept.")
                        fd.cross_checks.append({"field": fld, "period": flabel, "vendor": v, "sec": sf.value,
                                                "difference_pct": diff, "status": status, "accession": sf.accession})
                    if sf.original_value is not None:
                        dp.notes.append(f"Restated in SEC filings: originally {sf.original_value:,.4g} "
                                        f"(filed {sf.original_filed}), latest {sf.value:,.4g} (filed {sf.filed})"
                                        + (f"; {sf.derivation}." if sf.derivation and not sf.derived else "."))
                    if sf.derived:
                        dp.notes.append(f"SEC comparison value derived: {sf.derivation}.")
                elif v is not None:
                    dp.quality = Quality.UNVERIFIED
                    dp.notes.append("No SEC value available for cross-check." if concepts else
                                    "No SEC XBRL concept mapped for this line item; vendor value unverified.")
                points.append(dp)
            # SEC-only periods extend history (e.g. 5+ years, older quarters)
            for end, sf in sec.items():
                if end in used_sec or _match(end, vendor):
                    continue
                if period == "quarterly" and kind == "flow" and sf.start is None:
                    continue
                flabel = fiscal_label(end, fye_month, PeriodType.ANNUAL if period == "annual" else PeriodType.QUARTERLY)
                dp = DataPoint(
                    id=f"fin.{fld}.{'A' if period == 'annual' else 'Q'}{end.isoformat()}",
                    field=fld, label=label, value=None, unit=unit, currency=currency if unit.startswith("USD") else None,
                    period_type=ptype, period_end=end, fiscal_period=flabel, source_name="SEC EDGAR",
                    source_type=SourceType.REGULATORY_FILING,
                )
                _apply_sec(dp, sf, SecEdgarProvider.filing_url(cik, sf.accession) if cik else None,
                           "Period not covered by vendor; SEC filing value used.")
                if scale_fix:
                    dp.original_value, dp.value = dp.value, dp.value * scale_fix
                    dp.notes.append(f"Rescaled ×{scale_fix:g}: the same filer's XBRL values for this field were tagged at "
                                    "the wrong scale in periods both sources cover.")
                if sf.original_value is not None:
                    dp.notes.append(f"Restated: originally {sf.original_value:,.4g} (filed {sf.original_filed}).")
                points.append(dp)
            points.sort(key=lambda p: p.period_end)
            # keep a bounded history
            points = points[-10:] if period == "annual" else points[-16:]
            if points:
                (fd.annual if period == "annual" else fd.quarterly)[fld] = points

    _build_ttm(fd, currency)
    for fld, spec in FIELDS.items():
        if spec[2] == "instant":
            q = fd.latest(fld, "quarterly") or fd.latest(fld, "annual")
            if q:
                fd.latest_instant[fld] = q
    if not sec_facts:
        fd.notes.append("SEC XBRL facts unavailable; vendor statements could not be cross-checked.")
    return fd


def _apply_sec(dp: DataPoint, sf: SecFact, url: str | None, note: str) -> None:
    dp.value = sf.value
    dp.source_name = f"SEC EDGAR XBRL ({sf.concept}, form {sf.form})"
    dp.source_url = url
    dp.source_type = SourceType.REGULATORY_FILING
    dp.filing_date = sf.filed
    dp.period_start = sf.start
    dp.extra["accession"] = sf.accession
    dp.quality = Quality.DERIVED if sf.derived else Quality.OK
    if sf.derived:
        dp.reported_or_calculated = "derived"
        dp.calculation_formula = sf.derivation
    dp.notes.append(note)


def _build_ttm(fd: FinancialData, currency: str | None) -> None:
    """TTM = sum of the latest four consecutive quarters, or latest FY if it ends at the latest quarter."""
    for fld, spec in FIELDS.items():
        label, unit, kind, *_ = spec
        if kind != "flow":
            continue
        qs = [p for p in fd.quarterly.get(fld, []) if p.period_end]
        last4 = qs[-4:]
        ok = len(last4) == 4 and all(p.value is not None for p in last4)
        if ok:
            gaps = [(b.period_end - a.period_end).days for a, b in zip(last4, last4[1:], strict=False)]
            ok = all(QUARTER_DAYS[0] <= g <= QUARTER_DAYS[1] for g in gaps)
        annual_latest = fd.latest(fld, "annual")
        if ok:
            value = sum(p.value for p in last4)  # type: ignore[misc]
            end = last4[-1].period_end
            notes = []
            if unit == "USD/share":
                notes.append("TTM EPS is the sum of four quarterly EPS figures; share counts differ by quarter.")
            if unit == "shares":
                value = value / 4
                notes.append("TTM share count is the average of four quarterly weighted-average counts.")
            fd.ttm[fld] = DataPoint(
                id=f"fin.{fld}.TTM{end.isoformat()}", field=fld, label=f"{label} (TTM)", value=value, unit=unit,
                currency=currency if unit.startswith("USD") else None, period_type=PeriodType.TTM,
                period_start=last4[0].period_start, period_end=end,
                fiscal_period=f"TTM to {last4[-1].fiscal_period}", source_name="Calculated",
                source_type=SourceType.CALCULATED, reported_or_calculated="calculated",
                calculation_formula=("average" if unit == "shares" else "sum") + " of last four quarters",
                inputs=[p.id for p in last4],
                quality=Quality.DERIVED if any(p.quality == Quality.DERIVED for p in last4) else Quality.OK,
                notes=notes,
            )
        elif annual_latest and qs and annual_latest.period_end and qs[-1].period_end and \
                abs((annual_latest.period_end - qs[-1].period_end).days) <= 10:
            fd.ttm[fld] = annual_latest.model_copy(update={
                "id": f"fin.{fld}.TTM{annual_latest.period_end.isoformat()}", "period_type": PeriodType.TTM,
                "label": f"{label} (TTM = latest fiscal year)", "inputs": [annual_latest.id],
                "notes": annual_latest.notes + ["Latest fiscal year ends at the latest quarter, so TTM = FY."],
            })
