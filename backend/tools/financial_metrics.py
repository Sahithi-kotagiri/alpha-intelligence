"""Deterministic fundamental metrics computed from normalised statements.

Every function is pure and unit-tested. Conventions:
- Percentages are fractions (0.15 = 15%).
- Growth from a negative or zero base is *not meaningful* (sign flips distort it).
- Ratios with a zero/negative denominator where the ratio loses meaning return
  `not_meaningful` with a reason, never a fabricated number.
- Averages of balance-sheet items use (opening + closing) / 2 when both exist.
"""

from __future__ import annotations

import statistics
from datetime import date

from backend.data.normalisation import FinancialData
from backend.data.provenance import DataPoint
from backend.tools.types import Metric, not_meaningful, unavailable

STATUTORY_TAX_RATE = 0.21  # US federal rate, used only when the effective rate is unusable


# ---------------------------------------------------------------- primitives
def safe_div(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return a / b


def growth(curr: float | None, prev: float | None) -> tuple[float | None, str | None]:
    """Simple growth rate. Returns (value, reason_if_none)."""
    if curr is None or prev is None:
        return None, "missing input"
    if prev <= 0:
        return None, "base period value is zero or negative; growth rate not meaningful"
    return (curr - prev) / prev, None


def cagr(first: float | None, last: float | None, years: float) -> tuple[float | None, str | None]:
    if first is None or last is None:
        return None, "missing input"
    if years <= 0:
        return None, "invalid period length"
    if first <= 0 or last <= 0:
        return None, "start or end value is zero or negative; CAGR not meaningful"
    return (last / first) ** (1 / years) - 1, None


def average(a: float | None, b: float | None) -> float | None:
    if a is None and b is None:
        return None
    if a is None:
        return b
    if b is None:
        return a
    return (a + b) / 2


def effective_tax_rate(tax: float | None, pretax: float | None) -> tuple[float, str]:
    """Clamp the effective tax rate to [0, 35%]; fall back to statutory rate if unusable."""
    if tax is not None and pretax is not None and pretax > 0:
        r = tax / pretax
        if 0 <= r <= 0.35:
            return r, f"effective tax rate {r:.1%} (tax provision / pre-tax income)"
        return min(max(r, 0.0), 0.35), f"effective tax rate {r:.1%} clamped to [0%, 35%]"
    return STATUTORY_TAX_RATE, "effective tax rate unavailable; 21% US statutory rate assumed"


def roic(ebit: float | None, tax: float | None, pretax: float | None, debt_open: float | None,
         debt_close: float | None, equity_open: float | None, equity_close: float | None,
         cash_open: float | None, cash_close: float | None) -> tuple[float | None, str]:
    """ROIC = EBIT × (1 − tax rate) / average invested capital.

    Invested capital = total debt + shareholders' equity − cash & short-term investments.
    Returns (value, explanation). Not meaningful when invested capital ≤ 0.
    """
    if ebit is None:
        return None, "EBIT unavailable"
    rate, tax_note = effective_tax_rate(tax, pretax)
    ic_close = None if None in (debt_close, equity_close) else debt_close + equity_close - (cash_close or 0)
    ic_open = None if None in (debt_open, equity_open) else debt_open + equity_open - (cash_open or 0)
    ic = average(ic_open, ic_close)
    if ic is None:
        return None, "invested capital unavailable (needs total debt and equity)"
    if ic <= 0:
        return None, "invested capital is zero or negative (net cash exceeds debt + equity); ROIC not meaningful"
    return ebit * (1 - rate) / ic, tax_note


def piotroski(cur: dict[str, float | None], prev: dict[str, float | None]) -> tuple[int | None, int, list[dict]]:
    """Piotroski F-score (Piotroski 2000). Returns (score, tests_available, details).

    Inputs per year: net_income, operating_cash_flow, total_assets, long_term_debt,
    current_assets, current_liabilities, diluted_shares, gross_profit, revenue,
    and prior-year total_assets for ROA denominators (approximated by year-end assets).
    """
    def g(d, k):
        return d.get(k)

    tests = []

    def add(name, cond):
        tests.append({"test": name, "passed": cond})

    roa_c = safe_div(g(cur, "net_income"), g(cur, "total_assets"))
    roa_p = safe_div(g(prev, "net_income"), g(prev, "total_assets"))
    add("Positive net income", None if g(cur, "net_income") is None else g(cur, "net_income") > 0)
    add("Positive operating cash flow", None if g(cur, "operating_cash_flow") is None else g(cur, "operating_cash_flow") > 0)
    add("ROA improved", None if None in (roa_c, roa_p) else roa_c > roa_p)
    add("Operating cash flow exceeds net income", None if None in (g(cur, "operating_cash_flow"), g(cur, "net_income"))
        else g(cur, "operating_cash_flow") > g(cur, "net_income"))
    lev_c = safe_div(g(cur, "long_term_debt"), g(cur, "total_assets"))
    lev_p = safe_div(g(prev, "long_term_debt"), g(prev, "total_assets"))
    add("Long-term leverage decreased", None if None in (lev_c, lev_p) else lev_c <= lev_p)
    cr_c = safe_div(g(cur, "current_assets"), g(cur, "current_liabilities"))
    cr_p = safe_div(g(prev, "current_assets"), g(prev, "current_liabilities"))
    add("Current ratio improved", None if None in (cr_c, cr_p) else cr_c > cr_p)
    add("No share dilution", None if None in (g(cur, "diluted_shares"), g(prev, "diluted_shares"))
        else g(cur, "diluted_shares") <= g(prev, "diluted_shares") * 1.005)
    gm_c = safe_div(g(cur, "gross_profit"), g(cur, "revenue"))
    gm_p = safe_div(g(prev, "gross_profit"), g(prev, "revenue"))
    add("Gross margin improved", None if None in (gm_c, gm_p) else gm_c > gm_p)
    at_c = safe_div(g(cur, "revenue"), g(cur, "total_assets"))
    at_p = safe_div(g(prev, "revenue"), g(prev, "total_assets"))
    add("Asset turnover improved", None if None in (at_c, at_p) else at_c > at_p)
    avail = [t for t in tests if t["passed"] is not None]
    if len(avail) < 7:
        return None, len(avail), tests
    return sum(1 for t in avail if t["passed"]), len(avail), tests


def altman_z(working_capital: float | None, retained_earnings: float | None, ebit: float | None,
             market_cap: float | None, total_liabilities: float | None, revenue: float | None,
             total_assets: float | None) -> float | None:
    """Original Altman (1968) Z-score for public manufacturers. >2.99 safe, <1.81 distress."""
    vals = [working_capital, retained_earnings, ebit, market_cap, total_liabilities, revenue, total_assets]
    if any(v is None for v in vals) or not total_assets or not total_liabilities:
        return None
    ta = total_assets
    return (1.2 * working_capital / ta + 1.4 * retained_earnings / ta + 3.3 * ebit / ta
            + 0.6 * market_cap / total_liabilities + 1.0 * revenue / ta)


# ---------------------------------------------------------------- series helpers
def _by_end(points: list[DataPoint]) -> dict[date, DataPoint]:
    return {p.period_end: p for p in points if p.period_end is not None}


def _val(p: DataPoint | None) -> float | None:
    return p.value if p is not None else None


def _find_prior(series: dict[date, DataPoint], end: date, days: int, tol: int = 20) -> DataPoint | None:
    for d, p in series.items():
        if abs((end - d).days - days) <= tol:
            return p
    return None


class FundamentalCalculator:
    """Computes the full fundamental metric set from a FinancialData object."""

    def __init__(self, fd: FinancialData):
        self.fd = fd
        self.metrics: list[Metric] = []

    def _add(self, m: Metric) -> Metric:
        self.metrics.append(m)
        return m

    def _ann(self, f: str) -> dict[date, DataPoint]:
        return _by_end(self.fd.annual.get(f, []))

    def _q(self, f: str) -> dict[date, DataPoint]:
        return _by_end(self.fd.quarterly.get(f, []))

    # ---------------------------------------------------- growth
    def growth_series(self, fld: str, label: str, category: str = "growth") -> None:
        ann = self._ann(fld)
        ends = sorted(ann)
        for i, end in enumerate(ends):
            cur = ann[end]
            prev = _find_prior(ann, end, 365, 25)
            mid = f"met.{fld}_yoy.A{end.isoformat()}"
            if prev is None:
                if i > 0:
                    self._add(unavailable(mid, f"{fld}_yoy_annual", f"{label} growth YoY (annual)", category, "pct",
                                          f"({label} FY / {label} prior FY) − 1", "prior fiscal year unavailable",
                                          cur.fiscal_period, [cur.id], True))
                continue
            v, why = growth(cur.value, prev.value)
            m = Metric(id=mid, key=f"{fld}_yoy_annual", label=f"{label} growth YoY (annual)", category=category,
                       value=v, unit="pct", period=cur.fiscal_period, period_end=end,
                       formula=f"({label} {cur.fiscal_period} / {label} {prev.fiscal_period}) − 1",
                       inputs=[cur.id, prev.id], higher_is_better=True)
            if v is None:
                m.status, m.reason = ("not_meaningful" if "negative" in (why or "") else "unavailable"), why
            self._add(m)
        q = self._q(fld)
        for end in sorted(q):
            cur = q[end]
            prev = _find_prior(q, end, 364, 20)
            if prev is None:
                continue
            v, why = growth(cur.value, prev.value)
            m = Metric(id=f"met.{fld}_yoy.Q{end.isoformat()}", key=f"{fld}_yoy_quarterly",
                       label=f"{label} growth YoY (quarterly)", category=category, value=v, unit="pct",
                       period=cur.fiscal_period, period_end=end,
                       formula=f"({label} {cur.fiscal_period} / {label} {prev.fiscal_period}) − 1",
                       inputs=[cur.id, prev.id], higher_is_better=True)
            if v is None:
                m.status, m.reason = ("not_meaningful" if "negative" in (why or "") else "unavailable"), why
            self._add(m)
            # sequential
            seq = _find_prior(q, end, 91, 15)
            if seq is not None:
                sv, swhy = growth(cur.value, seq.value)
                sm = Metric(id=f"met.{fld}_qoq.Q{end.isoformat()}", key=f"{fld}_qoq", label=f"{label} growth QoQ (sequential)",
                            category=category, value=sv, unit="pct", period=cur.fiscal_period, period_end=end,
                            formula=f"({label} {cur.fiscal_period} / {label} {seq.fiscal_period}) − 1",
                            inputs=[cur.id, seq.id], higher_is_better=True,
                            notes=["Sequential growth can reflect seasonality."])
                if sv is None:
                    sm.status, sm.reason = "not_meaningful", swhy
                self._add(sm)
        for years in (3, 5):
            if not ends:
                break
            last = ann[ends[-1]]
            first = _find_prior(ann, ends[-1], 365 * years, 30)
            mid = f"met.{fld}_cagr_{years}y"
            if first is None:
                self._add(unavailable(mid, f"{fld}_cagr_{years}y", f"{label} {years}-year CAGR", category, "pct",
                                      f"({label} latest FY / {label} FY−{years})^(1/{years}) − 1",
                                      f"fewer than {years + 1} fiscal years available", last.fiscal_period, [last.id], True))
                continue
            v, why = cagr(first.value, last.value, years)
            m = Metric(id=mid, key=f"{fld}_cagr_{years}y", label=f"{label} {years}-year CAGR", category=category, value=v,
                       unit="pct", period=f"{first.fiscal_period}→{last.fiscal_period}", period_end=last.period_end,
                       formula=f"({label} {last.fiscal_period} / {label} {first.fiscal_period})^(1/{years}) − 1",
                       inputs=[first.id, last.id], higher_is_better=True)
            if v is None:
                m.status, m.reason = "not_meaningful", why
            self._add(m)

    # ---------------------------------------------------- ratio over periods
    def ratio_series(self, key: str, label: str, category: str, num: str, den: str, unit: str = "pct",
                     higher_is_better: bool | None = True, den_must_be_positive: bool = True,
                     include_ttm: bool = True, avg_den: bool = False, quarterly: bool = True) -> None:
        sets = [("A", self._ann(num), self._ann(den))]
        if quarterly:
            sets.append(("Q", self._q(num), self._q(den)))
        for tag, ns, ds in sets:
            for end in sorted(ns):
                n = ns[end]
                d = ds.get(end) or next((ds[x] for x in ds if abs((x - end).days) <= 10), None)
                mid = f"met.{key}.{tag}{end.isoformat()}"
                if d is None or n.value is None or d.value is None:
                    self._add(unavailable(mid, f"{key}_{'annual' if tag == 'A' else 'quarterly'}", label, category, unit,
                                          f"{num} / {den}", "numerator or denominator unavailable", n.fiscal_period,
                                          [n.id] + ([d.id] if d else []), higher_is_better))
                    continue
                dv = d.value
                inputs = [n.id, d.id]
                if avg_den:
                    prev = _find_prior(ds, end, 365 if tag == "A" else 91, 25)
                    if prev is not None and prev.value is not None:
                        dv = (d.value + prev.value) / 2
                        inputs.append(prev.id)
                if den_must_be_positive and dv <= 0:
                    self._add(not_meaningful(mid, f"{key}_{'annual' if tag == 'A' else 'quarterly'}", label, category, unit,
                                             f"{num} / {den}", f"{den} is zero or negative", n.fiscal_period, inputs,
                                             higher_is_better))
                    continue
                self._add(Metric(id=mid, key=f"{key}_{'annual' if tag == 'A' else 'quarterly'}", label=label,
                                 category=category, value=n.value / dv, unit=unit, period=n.fiscal_period, period_end=end,
                                 formula=f"{num} / {'average ' if avg_den else ''}{den}", inputs=inputs,
                                 higher_is_better=higher_is_better))
        if include_ttm:
            n, d = self.fd.ttm.get(num), self.fd.ttm.get(den) or self.fd.latest_instant.get(den)
            mid = f"met.{key}.TTM"
            if n is None or d is None or n.value is None or d.value is None:
                self._add(unavailable(mid, f"{key}_ttm", f"{label} (TTM)", category, unit, f"TTM {num} / {den}",
                                      "TTM inputs unavailable", "TTM", higher_is_better=higher_is_better))
            elif den_must_be_positive and d.value <= 0:
                self._add(not_meaningful(mid, f"{key}_ttm", f"{label} (TTM)", category, unit, f"TTM {num} / {den}",
                                         f"{den} is zero or negative", "TTM", [n.id, d.id], higher_is_better))
            else:
                self._add(Metric(id=mid, key=f"{key}_ttm", label=f"{label} (TTM)", category=category,
                                 value=n.value / d.value, unit=unit, period=n.fiscal_period, period_end=n.period_end,
                                 formula=f"TTM {num} / {'TTM ' if d.period_type.value == 'ttm' else 'latest '}{den}",
                                 inputs=[n.id, d.id], higher_is_better=higher_is_better))

    # ---------------------------------------------------- composite
    def compute(self) -> list[Metric]:
        for fld, label in [("revenue", "Revenue"), ("diluted_eps", "Diluted EPS"), ("net_income", "Net income"),
                           ("operating_income", "Operating income"), ("operating_cash_flow", "Operating cash flow")]:
            self.growth_series(fld, label)
        self.free_cash_flow()
        self.growth_series("free_cash_flow", "Free cash flow")
        # profitability
        self.ratio_series("gross_margin", "Gross margin", "profitability", "gross_profit", "revenue")
        self.ratio_series("operating_margin", "Operating margin", "profitability", "operating_income", "revenue")
        self.ratio_series("net_margin", "Net margin", "profitability", "net_income", "revenue")
        self.ratio_series("ebitda_margin", "EBITDA margin", "profitability", "ebitda", "revenue", quarterly=False)
        self.ratio_series("fcf_margin", "Free cash flow margin", "cash_flow", "free_cash_flow", "revenue")
        # efficiency / intensity
        self.ratio_series("rnd_intensity", "R&D as % of revenue", "efficiency", "rnd", "revenue", higher_is_better=None,
                          quarterly=False)
        self.ratio_series("sga_intensity", "SG&A as % of revenue", "efficiency", "sga", "revenue", higher_is_better=False,
                          quarterly=False)
        self.ratio_series("capex_intensity", "Capex as % of revenue", "cash_flow", "capex", "revenue",
                          higher_is_better=None, quarterly=False)
        self.ratio_series("sbc_intensity", "Stock-based compensation as % of revenue", "dilution", "sbc", "revenue",
                          higher_is_better=False, quarterly=False)
        self.ratio_series("asset_turnover", "Asset turnover", "efficiency", "revenue", "total_assets", unit="x",
                          avg_den=True, quarterly=False, include_ttm=False)
        # returns
        self.ratio_series("roe", "Return on equity", "returns", "net_income", "equity", avg_den=True, quarterly=False)
        self.ratio_series("roa", "Return on assets", "returns", "net_income", "total_assets", avg_den=True, quarterly=False)
        self.roic_series()
        # cash flow quality
        self.ratio_series("fcf_conversion", "FCF conversion (FCF / net income)", "cash_flow", "free_cash_flow",
                          "net_income", unit="x", quarterly=False)
        self.ratio_series("ocf_conversion", "Operating cash flow / net income", "cash_flow", "operating_cash_flow",
                          "net_income", unit="x", quarterly=False)
        self.ratio_series("payout_ratio", "Dividend payout ratio", "capital_allocation", "dividends_paid", "net_income",
                          higher_is_better=None, quarterly=False)
        self.accruals()
        # balance sheet
        self.balance_sheet()
        self.dilution()
        self.quality_scores()
        self.stability()
        return self.metrics

    def free_cash_flow(self) -> None:
        """FCF = operating cash flow − capital expenditure, computed per period and cross-checked with vendor FCF."""
        for period in ("annual", "quarterly"):
            ocf = _by_end(self.fd.series("operating_cash_flow", period))
            capex = _by_end(self.fd.series("capex", period))
            vend = _by_end(self.fd.series("vendor_free_cash_flow", period))
            pts = []
            for end, o in sorted(ocf.items()):
                c = capex.get(end)
                if o.value is None or c is None or c.value is None:
                    continue
                v = o.value - c.value
                dp = DataPoint(id=f"fin.free_cash_flow.{'A' if period == 'annual' else 'Q'}{end.isoformat()}",
                               field="free_cash_flow", label="Free cash flow", value=v, unit="USD",
                               currency=o.currency, period_type=o.period_type, period_end=end,
                               fiscal_period=o.fiscal_period, source_name="Calculated", source_type="calculated",
                               reported_or_calculated="calculated",
                               calculation_formula="Operating cash flow − capital expenditure", inputs=[o.id, c.id])
                vf = vend.get(end)
                if vf and vf.value is not None and v != 0:
                    diff = abs(vf.value - v) / abs(v)
                    if diff > 0.02:
                        dp.notes.append(f"Vendor-reported FCF {vf.value:,.4g} differs by {diff:.1%} from OCF − capex.")
                pts.append(dp)
            if pts:
                (self.fd.annual if period == "annual" else self.fd.quarterly)["free_cash_flow"] = pts
        ocf_t, cap_t = self.fd.ttm.get("operating_cash_flow"), self.fd.ttm.get("capex")
        if ocf_t and cap_t and ocf_t.value is not None and cap_t.value is not None:
            self.fd.ttm["free_cash_flow"] = DataPoint(
                id=f"fin.free_cash_flow.TTM{ocf_t.period_end}", field="free_cash_flow", label="Free cash flow (TTM)",
                value=ocf_t.value - cap_t.value, unit="USD", currency=ocf_t.currency, period_type="ttm",
                period_end=ocf_t.period_end, fiscal_period=ocf_t.fiscal_period, source_name="Calculated",
                source_type="calculated", reported_or_calculated="calculated",
                calculation_formula="TTM operating cash flow − TTM capex", inputs=[ocf_t.id, cap_t.id])

    def roic_series(self) -> None:
        ebit = self._ann("ebit") or self._ann("operating_income")
        src = "ebit" if self._ann("ebit") else "operating_income"
        tax, pre = self._ann("tax_provision"), self._ann("pretax_income")
        debt, eq = self._ann("total_debt"), self._ann("equity")
        cash = self._ann("cash_and_st_investments") or self._ann("cash")
        for end in sorted(ebit):
            e = ebit[end]

            def near(s, d=end):
                return s.get(d) or next((s[x] for x in s if abs((x - d).days) <= 10), None)

            def prior(s, d=end):
                return _find_prior(s, d, 365, 25)

            v, note = roic(e.value, _val(near(tax)), _val(near(pre)), _val(prior(debt)), _val(near(debt)),
                           _val(prior(eq)), _val(near(eq)), _val(prior(cash)), _val(near(cash)))
            inputs = [p.id for p in [e, near(tax), near(pre), near(debt), near(eq), near(cash), prior(debt), prior(eq)] if p]
            m = Metric(id=f"met.roic.A{end.isoformat()}", key="roic_annual", label="Return on invested capital",
                       category="returns", value=v, unit="pct", period=e.fiscal_period, period_end=end,
                       formula=f"{src.upper() if src == 'ebit' else 'Operating income'} × (1 − tax rate) / average "
                               "(total debt + equity − cash & short-term investments)",
                       inputs=inputs, higher_is_better=True, notes=[note] if v is not None else [])
            if v is None:
                m.status = "not_meaningful" if "negative" in note else "unavailable"
                m.reason = note
            self._add(m)

    def accruals(self) -> None:
        ni, ocf, ta = self._ann("net_income"), self._ann("operating_cash_flow"), self._ann("total_assets")
        for end in sorted(ni):
            o = ocf.get(end)
            a = ta.get(end)
            prev_a = _find_prior(ta, end, 365, 25)
            avg_ta = average(_val(a), _val(prev_a))
            if o is None or ni[end].value is None or o.value is None or not avg_ta:
                continue
            self._add(Metric(id=f"met.accruals_ratio.A{end.isoformat()}", key="accruals_ratio_annual",
                             label="Accruals ratio", category="earnings_quality",
                             value=(ni[end].value - o.value) / avg_ta, unit="pct", period=ni[end].fiscal_period,
                             period_end=end, formula="(net income − operating cash flow) / average total assets",
                             inputs=[p.id for p in (ni[end], o, a, prev_a) if p], higher_is_better=False,
                             notes=["High positive accruals indicate earnings not backed by cash (Sloan 1996)."]))

    def balance_sheet(self) -> None:
        li = self.fd.latest_instant
        ebitda_t = self.fd.ttm.get("ebitda")
        if ebitda_t is None and self.fd.ttm.get("operating_income") and self.fd.ttm.get("dna"):
            oi, dna = self.fd.ttm["operating_income"], self.fd.ttm["dna"]
            if oi.value is not None and dna.value is not None:
                ebitda_t = DataPoint(id=f"fin.ebitda.TTM{oi.period_end}", field="ebitda", label="EBITDA (TTM, derived)",
                                     value=oi.value + dna.value, unit="USD", period_type="ttm", period_end=oi.period_end,
                                     fiscal_period=oi.fiscal_period, source_name="Calculated", source_type="calculated",
                                     reported_or_calculated="calculated",
                                     calculation_formula="TTM operating income + TTM D&A", inputs=[oi.id, dna.id])
                self.fd.ttm["ebitda"] = ebitda_t
        debt, eq, cash = li.get("total_debt"), li.get("equity"), li.get("cash_and_st_investments") or li.get("cash")
        period = (debt or eq).fiscal_period if (debt or eq) else None

        def latest_metric(key, label, category, unit, formula, num, den, hib, den_pos=True, extra_reason=None):
            mid = f"met.{key}.latest"
            if num is None or den is None or num.value is None or den.value is None:
                return self._add(unavailable(mid, key, label, category, unit, formula,
                                             extra_reason or "input unavailable", period, higher_is_better=hib))
            if den_pos and den.value <= 0:
                return self._add(not_meaningful(mid, key, label, category, unit, formula,
                                                f"denominator ({den.label}) is zero or negative", period,
                                                [num.id, den.id], hib))
            if den.value == 0:
                return self._add(not_meaningful(mid, key, label, category, unit, formula, "denominator is zero",
                                                period, [num.id, den.id], hib))
            return self._add(Metric(id=mid, key=key, label=label, category=category, value=num.value / den.value,
                                    unit=unit, period=num.fiscal_period, period_end=num.period_end, formula=formula,
                                    inputs=[num.id, den.id], higher_is_better=hib))

        latest_metric("debt_to_equity", "Debt-to-equity", "financial_health", "x",
                      "total debt (incl. finance leases) / shareholders' equity", debt, eq, False)
        latest_metric("current_ratio", "Current ratio", "financial_health", "x", "current assets / current liabilities",
                      li.get("current_assets"), li.get("current_liabilities"), True)
        ca, inv, cl = li.get("current_assets"), li.get("inventory"), li.get("current_liabilities")
        if ca and cl and ca.value is not None and cl.value:
            q = (ca.value - (inv.value if inv and inv.value else 0)) / cl.value
            self._add(Metric(id="met.quick_ratio.latest", key="quick_ratio", label="Quick ratio",
                             category="financial_health", value=q, unit="x", period=ca.fiscal_period,
                             period_end=ca.period_end, formula="(current assets − inventory) / current liabilities",
                             inputs=[p.id for p in (ca, inv, cl) if p], higher_is_better=True,
                             notes=[] if inv else ["Inventory not reported; treated as zero."]))
        latest_metric("cash_ratio", "Cash ratio", "financial_health", "x",
                      "cash & short-term investments / current liabilities", cash, cl, True)
        latest_metric("debt_to_assets", "Debt-to-assets", "financial_health", "pct", "total debt / total assets",
                      debt, li.get("total_assets"), False)
        if debt and cash and debt.value is not None and cash.value is not None:
            nd = debt.value - cash.value
            self._add(Metric(id="met.net_debt.latest", key="net_debt", label="Net debt", category="financial_health",
                             value=nd, unit="USD", period=debt.fiscal_period, period_end=debt.period_end,
                             formula="total debt − cash & short-term investments", inputs=[debt.id, cash.id],
                             higher_is_better=False, notes=["Negative net debt means net cash."]))
            if ebitda_t and ebitda_t.value:
                if ebitda_t.value <= 0:
                    self._add(not_meaningful("met.net_debt_to_ebitda.latest", "net_debt_to_ebitda", "Net debt / EBITDA",
                                             "financial_health", "x", "net debt / TTM EBITDA", "EBITDA is zero or negative",
                                             period, [debt.id, cash.id, ebitda_t.id], False))
                else:
                    self._add(Metric(id="met.net_debt_to_ebitda.latest", key="net_debt_to_ebitda",
                                     label="Net debt / EBITDA", category="financial_health", value=nd / ebitda_t.value,
                                     unit="x", period=ebitda_t.fiscal_period, period_end=ebitda_t.period_end,
                                     formula="(total debt − cash & ST investments) / TTM EBITDA",
                                     inputs=[debt.id, cash.id, ebitda_t.id], higher_is_better=False))
        ebit_t = self.fd.ttm.get("ebit") or self.fd.ttm.get("operating_income")
        int_t = self.fd.ttm.get("interest_expense")
        if ebit_t and ebit_t.value is not None:
            if int_t is None or int_t.value is None:
                self._add(unavailable("met.interest_coverage.latest", "interest_coverage", "Interest coverage",
                                      "financial_health", "x", "TTM EBIT / TTM interest expense",
                                      "interest expense not reported (may be zero or netted)", ebit_t.fiscal_period,
                                      [ebit_t.id], True))
            elif int_t.value <= 0:
                self._add(not_meaningful("met.interest_coverage.latest", "interest_coverage", "Interest coverage",
                                         "financial_health", "x", "TTM EBIT / TTM interest expense",
                                         "no interest expense reported", ebit_t.fiscal_period, [ebit_t.id, int_t.id], True))
            else:
                self._add(Metric(id="met.interest_coverage.latest", key="interest_coverage", label="Interest coverage",
                                 category="financial_health", value=ebit_t.value / int_t.value, unit="x",
                                 period=ebit_t.fiscal_period, period_end=ebit_t.period_end,
                                 formula="TTM EBIT / TTM interest expense", inputs=[ebit_t.id, int_t.id],
                                 higher_is_better=True))
        # working-capital days
        rev_t, rec = self.fd.ttm.get("revenue"), li.get("receivables")
        if rev_t and rec and rev_t.value and rec.value is not None and rev_t.value > 0:
            self._add(Metric(id="met.dso.latest", key="dso", label="Days sales outstanding", category="efficiency",
                             value=rec.value / rev_t.value * 365, unit="days", period=rec.fiscal_period,
                             period_end=rec.period_end, formula="receivables / TTM revenue × 365",
                             inputs=[rec.id, rev_t.id], higher_is_better=False))
        cogs_t = self.fd.ttm.get("cost_of_revenue")
        if cogs_t and inv and cogs_t.value and inv.value is not None and cogs_t.value > 0:
            self._add(Metric(id="met.dio.latest", key="dio", label="Days inventory outstanding", category="efficiency",
                             value=inv.value / cogs_t.value * 365, unit="days", period=inv.fiscal_period,
                             period_end=inv.period_end, formula="inventory / TTM cost of revenue × 365",
                             inputs=[inv.id, cogs_t.id], higher_is_better=False))

    def dilution(self) -> None:
        sh = self._ann("diluted_shares")
        ends = sorted(sh)
        if len(ends) >= 2:
            cur, prev = sh[ends[-1]], _find_prior(sh, ends[-1], 365, 25)
            if prev:
                v, why = growth(cur.value, prev.value)
                self._add(Metric(id="met.share_change_1y.latest", key="share_change_1y",
                                 label="Diluted share count change (1y)", category="dilution", value=v, unit="pct",
                                 period=cur.fiscal_period, period_end=cur.period_end,
                                 formula="diluted weighted shares FY / prior FY − 1", inputs=[cur.id, prev.id],
                                 higher_is_better=False, status="ok" if v is not None else "unavailable", reason=why))
            first = _find_prior(sh, ends[-1], 365 * 3, 30)
            if first:
                v, why = cagr(first.value, cur.value, 3)
                self._add(Metric(id="met.share_change_3y_cagr.latest", key="share_change_3y_cagr",
                                 label="Diluted share count 3-year CAGR", category="dilution", value=v, unit="pct",
                                 period=f"{first.fiscal_period}→{cur.fiscal_period}", period_end=cur.period_end,
                                 formula="(shares FY / shares FY−3)^(1/3) − 1", inputs=[first.id, cur.id],
                                 higher_is_better=False, status="ok" if v is not None else "unavailable", reason=why))

    def quality_scores(self) -> None:
        a = self.fd.annual
        ends = sorted({p.period_end for p in a.get("revenue", []) if p.period_end})
        if len(ends) < 2:
            self._add(unavailable("met.piotroski.latest", "piotroski_f_score", "Piotroski F-score", "earnings_quality",
                                  "score", "9 binary tests (Piotroski 2000)", "needs two fiscal years"))
            return
        cur_end, prev_end = ends[-1], ends[-2]

        def year(end):
            out, ids = {}, []
            for f in ["net_income", "operating_cash_flow", "total_assets", "long_term_debt", "current_assets",
                      "current_liabilities", "diluted_shares", "gross_profit", "revenue"]:
                p = _by_end(a.get(f, [])).get(end) or next(
                    (x for x in a.get(f, []) if x.period_end and abs((x.period_end - end).days) <= 10), None)
                out[f] = _val(p)
                if p:
                    ids.append(p.id)
            return out, ids

        cur, cid = year(cur_end)
        prev, pid = year(prev_end)
        score, n, tests = piotroski(cur, prev)
        rev = _by_end(a["revenue"])[cur_end]
        m = Metric(id="met.piotroski.latest", key="piotroski_f_score", label="Piotroski F-score", category="earnings_quality",
                   value=score, unit="score", period=rev.fiscal_period, period_end=cur_end,
                   formula="Sum of 9 binary profitability, leverage/liquidity and efficiency tests (Piotroski 2000)",
                   inputs=cid + pid, higher_is_better=True,
                   notes=[f"{t['test']}: {'pass' if t['passed'] else 'fail' if t['passed'] is False else 'n/a'}" for t in tests])
        if score is None:
            m.status, m.reason = "unavailable", f"only {n} of 9 tests computable"
        elif n < 9:
            m.notes.append(f"Score out of {n} computable tests.")
        self._add(m)

    def stability(self) -> None:
        """Moat proxies: level and stability of margins and returns over up to 5 fiscal years."""
        for key, label in [("gross_margin_annual", "Gross margin"), ("operating_margin_annual", "Operating margin"),
                           ("roic_annual", "ROIC")]:
            vals = [m for m in self.metrics if m.key == key and m.ok][-5:]
            if len(vals) < 3:
                continue
            xs = [m.value for m in vals]
            self._add(Metric(id=f"met.{key}_avg5", key=f"{key.replace('_annual', '')}_avg", label=f"{label} average ({len(xs)}y)",
                             category="moat", value=statistics.mean(xs), unit="pct",
                             period=f"{vals[0].period}→{vals[-1].period}", formula=f"mean of annual {label.lower()}",
                             inputs=[m.id for m in vals], higher_is_better=True))
            self._add(Metric(id=f"met.{key}_stdev5", key=f"{key.replace('_annual', '')}_stdev", label=f"{label} volatility ({len(xs)}y)",
                             category="moat", value=statistics.pstdev(xs), unit="pct",
                             period=f"{vals[0].period}→{vals[-1].period}",
                             formula=f"population standard deviation of annual {label.lower()}",
                             inputs=[m.id for m in vals], higher_is_better=False))
        gr = [m for m in self.metrics if m.key == "revenue_yoy_annual" and m.ok][-5:]
        if len(gr) >= 3:
            self._add(Metric(id="met.revenue_growth_consistency", key="revenue_growth_consistency",
                             label="Years of positive revenue growth", category="moat",
                             value=sum(1 for m in gr if m.value > 0) / len(gr), unit="pct",
                             period=f"{gr[0].period}→{gr[-1].period}", formula="share of fiscal years with revenue growth > 0",
                             inputs=[m.id for m in gr], higher_is_better=True))


def latest_metric(metrics: list[Metric], key: str) -> Metric | None:
    found = [m for m in metrics if m.key == key]
    if not found:
        return None
    ok = [m for m in found if m.ok]
    pool = ok or found
    return sorted(pool, key=lambda m: (m.period_end or date.min))[-1]
