"""Context-aware metric assessment: peer-relative bands + absolute guardrails + business-type rules.

For each metric the engine records *why* it passes or fails: the value and formula,
the peer group used (level, size, median, IQR), the company's oriented percentile,
any absolute guardrail with its source, business-type overrides, and the final verdict.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from backend.analysis.classification import BusinessProfile
from backend.analysis.config_loader import thresholds
from backend.analysis.peers import PeerGroup, PeerStats, oriented_percentile, vendor_value
from backend.data.normalisation import FinancialData
from backend.tools.financial_metrics import altman_z, latest_metric
from backend.tools.types import Metric, fmt_value
from backend.tools.valuation import MarketSnapshot

VERDICT_POINTS = {"favourable": 1.0, "neutral": 0.5, "unfavourable": 0.0}


class PeerComparison(BaseModel):
    level: str
    group: str
    n: int
    median: float | None
    p25: float | None
    p75: float | None
    company_value_used: float | None
    basis: str
    percentile: float | None
    verdict: str | None


class AbsoluteCheck(BaseModel):
    rule: str
    source: str
    verdict: str


class Assessment(BaseModel):
    metric_key: str
    metric_id: str | None
    label: str
    category: str
    value: float | None
    unit: str
    display: str
    period: str | None
    formula: str | None
    verdict: str  # favourable | neutral | unfavourable | informational | not_meaningful | insufficient_data | not_applicable
    explanation: str
    peer: PeerComparison | None = None
    absolute: AbsoluteCheck | None = None
    overrides: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    threshold_version: str = ""


def extra_metrics(fd: FinancialData | None, snap: MarketSnapshot) -> list[Metric]:
    """Metrics needing both statements and market data (Altman Z)."""
    if fd is None:
        return []
    li, ttm = fd.latest_instant, fd.ttm
    ca, cl, re_, tl, ta = (li.get(k) for k in ("current_assets", "current_liabilities", "retained_earnings",
                                                 "total_liabilities", "total_assets"))
    ebit = ttm.get("ebit") or ttm.get("operating_income")
    rev = ttm.get("revenue")
    vals = [ca, cl, re_, tl, ta, ebit, rev]
    if any(v is None or v.value is None for v in vals) or snap.market_cap is None:
        return [Metric(id="met.altman_z.latest", key="altman_z", label="Altman Z-score", category="financial_health",
                       value=None, unit="score", formula="1.2·WC/TA + 1.4·RE/TA + 3.3·EBIT/TA + 0.6·MVE/TL + 1.0·Sales/TA",
                       status="unavailable", reason="one or more inputs unavailable", higher_is_better=True)]
    z = altman_z(ca.value - cl.value, re_.value, ebit.value, snap.market_cap, tl.value, rev.value, ta.value)
    return [Metric(id="met.altman_z.latest", key="altman_z", label="Altman Z-score", category="financial_health", value=z,
                   unit="score", period=ta.fiscal_period, period_end=ta.period_end,
                   formula="1.2·WC/TA + 1.4·RE/TA + 3.3·EBIT(TTM)/TA + 0.6·market cap/TL + 1.0·revenue(TTM)/TA",
                   inputs=[p.id for p in vals] + ["val.market_cap"], higher_is_better=True,
                   notes=["Original Altman (1968) model was calibrated on manufacturers; interpret with care."])]


def _abs_verdict(key: str, value: float, profile: BusinessProfile) -> AbsoluteCheck | None:
    cfg = thresholds()["absolute"].get(key)
    if not cfg:
        return None
    src = cfg.get("source", "")
    if key in ("debt_to_equity", "net_debt_to_ebitda"):
        band = cfg.get(profile.primary) if profile.primary in ("utility", "reit") else cfg["default"]
        band = band or cfg["default"]
        lo, hi = band["favourable_max"], band["unfavourable_min"]
        v = "favourable" if value <= lo else "unfavourable" if value >= hi else "neutral"
        return AbsoluteCheck(rule=f"≤{lo:g}x favourable, ≥{hi:g}x unfavourable ({profile.primary if profile.primary in ('utility', 'reit') else 'default'} band)",
                             source=src, verdict=v)
    if "favourable_max" in cfg or "unfavourable_min" in cfg:  # lower is better
        fmax, umin = cfg.get("favourable_max"), cfg.get("unfavourable_min")
        if fmax is not None and value <= fmax:
            v = "favourable"
        elif umin is not None and value >= umin:
            v = "unfavourable"
        else:
            v = "neutral"
        parts = ([f"≤{fmax:g} favourable"] if fmax is not None else []) + ([f"≥{umin:g} unfavourable"] if umin is not None else [])
        return AbsoluteCheck(rule=", ".join(parts), source=src, verdict=v)
    fmin, umax = cfg.get("favourable_min"), cfg.get("unfavourable_max")
    if fmin is not None and value >= fmin:
        v = "favourable"
    elif umax is not None and value <= umax:
        v = "unfavourable"
    else:
        v = "neutral"
    parts = ([f"≥{fmin:g} favourable"] if fmin is not None else []) + ([f"≤{umax:g} unfavourable"] if umax is not None else [])
    return AbsoluteCheck(rule=", ".join(parts), source=src, verdict=v)


def _combine(a: str | None, b: str | None) -> str:
    pts = {"favourable": 1, "neutral": 0, "unfavourable": -1}
    xs = [pts[x] for x in (a, b) if x in pts]
    if not xs:
        return "insufficient_data"
    s = sum(xs)
    return "favourable" if s > 0 else "unfavourable" if s < 0 else "neutral"


def _fmt(v: float | None, unit: str) -> str:
    return fmt_value(v, unit)


class Assessor:
    def __init__(self, metrics: list[Metric], profile: BusinessProfile, levels: dict[str, PeerGroup],
                 stats_by_level: dict[str, dict[str, PeerStats]], company_info: dict[str, Any]):
        self.metrics = metrics
        self.profile = profile
        self.levels = levels
        self.stats_by_level = stats_by_level
        self.info = company_info
        self.cfg = thresholds()

    def peer_stats_for(self, key: str) -> tuple[PeerGroup | None, PeerStats | None, list[str]]:
        """Narrowest peer level with enough valid values for this metric (documented fallback)."""
        mins = self.cfg["peer_group"]["min_valid_by_level"]
        tried = []
        for lvl in ("sub_industry", "sector", "universe"):
            pg = self.levels.get(lvl)
            st = self.stats_by_level.get(lvl, {}).get(key)
            if pg is None or st is None:
                continue
            if st.n >= mins[lvl] and st.median is not None:
                return pg, st, tried
            tried.append(f"{pg.name} ({st.n} valid, minimum {mins[lvl]})")
        return None, None, tried

    def not_applicable_reason(self, key: str) -> str | None:
        na = self.cfg["not_applicable"]
        if "balance_sheet_financial" in self.profile.flags and key in na["financial"]:
            return "Not applicable to balance-sheet-funded financial companies (debt and deposits are operating funding)."
        if self.profile.primary == "reit" and key in na["reit"]:
            return "Not applicable to REITs (structurally high payout; Altman Z not designed for REITs)."
        if self.profile.primary == "utility" and key in na["utility"]:
            return "Altman Z is not designed for regulated utilities."
        if self.profile.primary == "financial" and key == "altman_z":
            return "Altman Z is not designed for financial companies."
        if "negative_equity" in self.profile.flags and key in ("debt_to_equity", "roe_ttm", "price_to_book"):
            return "Shareholders' equity is negative, so this ratio is not meaningful; leverage is judged by net debt/EBITDA and coverage."
        return None

    def assess(self, key: str) -> Assessment | None:
        m = latest_metric(self.metrics, key)
        if m is None:
            return None
        base = dict(metric_key=key, metric_id=m.id, label=m.label, category=m.category, value=m.value, unit=m.unit,
                    display=_fmt(m.value, m.unit), period=m.period, formula=m.formula,
                    threshold_version=self.cfg["version"], evidence_ids=[m.id])
        reason = self.not_applicable_reason(key)
        if reason:
            return Assessment(**base, verdict="not_applicable", explanation=reason)
        if not m.ok:
            verdict = "not_meaningful" if m.status == "not_meaningful" else "insufficient_data"
            return Assessment(**base, verdict=verdict, explanation=f"{m.label}: {m.reason}.")
        expl: list[str] = [f"{m.label} {base['display']} ({m.formula}{', ' + m.period if m.period else ''})."]
        # ---- peer-relative
        peer = None
        spec = self.cfg["peer_metrics"].get(key)
        pg, st, tried = self.peer_stats_for(key) if spec else (None, None, [])
        if spec and st is not None and pg is not None:
            vend = vendor_value(self.info, spec)
            lo, hi = self.cfg["peer_group"]["valid_ranges"].get(key, [-1e18, 1e18])
            if vend is not None and lo <= vend <= hi:
                used, basis = vend, "vendor-reported value (same definition as peers)"
                if m.value and abs(vend - m.value) / max(abs(m.value), 1e-9) > 0.15:
                    expl.append(f"Note: vendor-reported value {_fmt(vend, m.unit)} differs from our calculation; the vendor "
                                "value is used for the peer comparison so that all companies share one definition.")
            else:
                used, basis = m.value, "calculated value (vendor value unavailable)"
            pct = oriented_percentile(used, list(st.values.values()), spec["better"])
            bands = self.cfg["percentile_bands"]
            pv = ("favourable" if pct >= bands["favourable_at_or_above"] else
                  "unfavourable" if pct < bands["unfavourable_below"] else "neutral")
            peer = PeerComparison(level=pg.level, group=pg.name, n=st.n, median=st.median, p25=st.p25,
                                  p75=st.p75, company_value_used=used, basis=basis, percentile=pct, verdict=pv)
            word = "cheaper/better" if spec["better"] == "lower" else "better"
            if tried:
                expl.append(f"Narrower peer groups had too few valid values ({'; '.join(tried)}).")
            expl.append(f"Versus {st.n} {pg.name} peers ({pg.level.replace('_', '-')} level; median "
                        f"{_fmt(st.median, m.unit)}, interquartile range {_fmt(st.p25, m.unit)}–{_fmt(st.p75, m.unit)}), "
                        f"it is {word} than {pct:.0%} of peers → {pv} (bands: ≥{bands['favourable_at_or_above']:.0%} "
                        f"favourable, <{bands['unfavourable_below']:.0%} unfavourable).")
        elif spec:
            expl.append("Peer comparison unavailable: " + ("; ".join(tried) if tried else "no peer data") + ".")
        # ---- absolute guardrail
        absc = _abs_verdict(key, m.value, self.profile)
        if absc:
            expl.append(f"Absolute guardrail: {absc.rule} → {absc.verdict} (source: {absc.source}).")
        verdict = _combine(peer.verdict if peer else None, absc.verdict if absc else None)
        if verdict == "insufficient_data":
            verdict = "informational"
            expl.append("No peer or absolute reference is available for this metric, so it is shown for information and not scored.")
        overrides: list[str] = []
        if key == "fcf_conversion_annual" and verdict == "unfavourable":
            ocf = latest_metric(self.metrics, "ocf_conversion_annual")
            capex = latest_metric(self.metrics, "capex_intensity_ttm")
            if ocf and ocf.ok and ocf.value >= 1.0 and capex and capex.ok and capex.value >= 0.15:
                verdict = "neutral"
                overrides.append(f"Operating cash flow covers net income ({ocf.value:.2f}x) and capex is {capex.value:.0%} of "
                                 "revenue: low FCF conversion reflects investment spending rather than weak earnings quality.")
                base["evidence_ids"] += [ocf.id, capex.id]
        # ---- business-type overrides
        if key == "debt_to_equity" and verdict == "unfavourable":
            ov = self.cfg["absolute"]["leverage_serviceable_override"]
            cov = latest_metric(self.metrics, "interest_coverage")
            nde = latest_metric(self.metrics, "net_debt_to_ebitda")
            if cov and cov.ok and nde and nde.ok and cov.value >= ov["min_interest_coverage"] and \
                    nde.value <= ov["max_net_debt_to_ebitda"]:
                verdict = "neutral"
                overrides.append(f"Leverage serviceable: interest coverage {cov.value:.1f}x ≥ {ov['min_interest_coverage']}x "
                                 f"and net debt/EBITDA {nde.value:.1f}x ≤ {ov['max_net_debt_to_ebitda']}x, so high D/E is "
                                 "not treated as a weakness.")
                base["evidence_ids"] += [cov.id, nde.id]
        if key in ("trailing_pe", "forward_pe", "ev_to_ebitda") and verdict == "unfavourable" and \
                ("growth" in self.profile.flags):
            peg = latest_metric(self.metrics, "peg_forward")
            peg_a = self.assess_peg_only(peg) if peg else None
            if peg_a in ("favourable", "neutral"):
                verdict = "neutral"
                overrides.append(f"Growth company: the premium multiple is offset by a {peg_a} growth-adjusted valuation "
                                 f"(forward PEG {peg.value:.2f}).")
                base["evidence_ids"].append(peg.id)
        if key in ("trailing_pe", "ev_to_ebitda") and verdict == "favourable" and "cyclical" in self.profile.flags:
            om = latest_metric(self.metrics, "operating_margin_ttm")
            avg = latest_metric(self.metrics, "operating_margin_avg")
            sd = latest_metric(self.metrics, "operating_margin_stdev")
            if om and om.ok and avg and avg.ok and sd and sd.ok and om.value >= avg.value + sd.value:
                verdict = "neutral"
                overrides.append(f"Cyclical business with TTM operating margin {om.value:.1%} above its multi-year average "
                                 f"{avg.value:.1%} + 1σ: a low multiple may reflect peak earnings.")
                base["evidence_ids"] += [om.id, avg.id]
        if "unprofitable" in self.profile.flags and key in ("net_margin_ttm", "roe_ttm") and verdict != "unfavourable":
            verdict = "unfavourable"
            overrides.append("Company is unprofitable on a TTM basis.")
        expl += overrides
        expl.append(f"Verdict: {verdict}.")
        return Assessment(**base, verdict=verdict, explanation=" ".join(expl), peer=peer, absolute=absc, overrides=overrides)

    def assess_peg_only(self, peg: Metric) -> str | None:
        if not peg.ok:
            return None
        absc = _abs_verdict("peg_forward", peg.value, self.profile)
        _, st, _ = self.peer_stats_for("peg_forward")
        pv = None
        if st is not None:
            pct = oriented_percentile(peg.value, list(st.values.values()), "lower")
            bands = self.cfg["percentile_bands"]
            pv = "favourable" if pct >= bands["favourable_at_or_above"] else "unfavourable" if pct < bands["unfavourable_below"] else "neutral"
        return _combine(pv, absc.verdict if absc else None)

    def assess_all(self, keys: list[str]) -> list[Assessment]:
        out = []
        for k in keys:
            a = self.assess(k)
            if a is not None:
                out.append(a)
        return out


ASSESSED_KEYS = [
    "revenue_yoy_annual", "revenue_cagr_3y", "revenue_cagr_5y", "revenue_yoy_quarterly", "diluted_eps_yoy_annual",
    "diluted_eps_cagr_3y", "diluted_eps_yoy_quarterly", "free_cash_flow_yoy_annual",
    "gross_margin_ttm", "operating_margin_ttm", "net_margin_ttm", "roe_ttm", "roic_annual", "roa_ttm",
    "debt_to_equity", "net_debt_to_ebitda", "interest_coverage", "current_ratio", "quick_ratio", "altman_z",
    "fcf_margin_ttm", "fcf_conversion_annual", "piotroski_f_score", "accruals_ratio_annual", "share_change_1y",
    "trailing_pe", "forward_pe", "peg_forward", "peg_trailing", "ev_to_ebitda", "ev_to_sales", "price_to_sales",
    "price_to_book", "price_to_fcf", "fcf_yield", "pe_vs_own_history",
]
