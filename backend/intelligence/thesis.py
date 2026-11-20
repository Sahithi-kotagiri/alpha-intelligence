"""Investment thesis extraction and assumption evaluation.

Each quantitative assumption = metric + comparator + thresholds derived from the value at
decision time using documented materiality bands (METHODOLOGY §9):

| metric                     | at-risk threshold                  | review threshold        |
|----------------------------|------------------------------------|-------------------------|
| revenue / EPS growth (>0)  | 50% of original growth             | 75% of original         |
| growth (≤0)                | original − 5pp                     | original − 2.5pp        |
| operating / FCF margin     | original − 5pp                     | original − 2.5pp        |
| ROIC                       | 70% of original                    | 85% of original         |
| net debt / EBITDA          | original + 1.0x                    | original + 0.5x         |
| trailing P/E (valuation)   | original × 1.25                    | original × 1.10         |
| price vs 200-day average   | −10%                               | −5%                     |

Status: Improving (better than original by ≥ one band), Valid, Review, At risk,
Invalidated (beyond the at-risk threshold by ≥ two bands), Unknown (no data).
Small fluctuations inside the review band never change status.
"""

from __future__ import annotations

from typing import Any

from backend.analysis.assessment import Assessment
from backend.tools.types import fmt_value

STATUSES = ["Improving", "Valid", "Review", "At risk", "Invalidated", "Unknown"]


def _a(assessments: list[Assessment], key: str) -> Assessment | None:
    return next((a for a in assessments if a.metric_key == key and a.value is not None
                 and a.verdict not in ("not_applicable", "not_meaningful")), None)


def extract(assessments: list[Assessment], technicals: dict[str, Any] | None, qualitative: list[str] | None = None,
            moat: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    def add(key, desc, metric, comp, thr, rev, orig, unit, mat, ev, inval):
        out.append({"key": key, "description": desc, "kind": "quantitative", "metric_key": metric, "comparator": comp,
                    "threshold": thr, "review_threshold": rev, "original_value": orig, "current_value": orig, "unit": unit,
                    "materiality": mat, "evidence_ids": ev, "invalidation": inval, "status": "Valid"})

    for key, label in (("revenue_yoy_quarterly", "Revenue growth"), ("diluted_eps_yoy_quarterly", "EPS growth")):
        a = _a(assessments, key) or _a(assessments, key.replace("_quarterly", "_annual"))
        if not a:
            continue
        v = a.value
        if v > 0:
            thr, rev = v * 0.5, v * 0.75
            desc = f"{label} remains robust (currently {fmt_value(v, 'pct')} YoY; review below {fmt_value(rev, 'pct')})"
        else:
            thr, rev = v - 0.05, v - 0.025
            desc = f"{label} does not deteriorate further (currently {fmt_value(v, 'pct')} YoY)"
        add(f"{key.split('_')[0]}_growth", desc, a.metric_key, ">=", thr, rev, v, "pct", "high",
            [f"asm.{a.metric_key}", a.metric_id], f"{label} below {fmt_value(thr, 'pct')} YoY")
    for key, label in (("operating_margin_ttm", "Operating margin"), ("fcf_margin_ttm", "Free-cash-flow margin")):
        a = _a(assessments, key)
        if a:
            add(key, f"{label} remains resilient (currently {a.display})", key, ">=", a.value - 0.05, a.value - 0.025, a.value,
                "pct", "high" if key == "operating_margin_ttm" else "medium", [f"asm.{key}", a.metric_id],
                f"{label} more than 5pp below {a.display}")
    a = _a(assessments, "roic_annual")
    if a and a.value > 0:
        add("roic", f"Returns on capital stay high (ROIC {a.display})", "roic_annual", ">=", a.value * 0.7, a.value * 0.85,
            a.value, "pct", "medium", ["asm.roic_annual", a.metric_id], f"ROIC below {fmt_value(a.value * 0.7, 'pct')}")
    a = _a(assessments, "net_debt_to_ebitda")
    if a:
        add("leverage", f"Leverage stays contained (net debt/EBITDA {a.display})", "net_debt_to_ebitda", "<=", a.value + 1.0,
            a.value + 0.5, a.value, "x", "medium", ["asm.net_debt_to_ebitda", a.metric_id],
            f"Net debt/EBITDA above {a.value + 1.0:.1f}x")
    a = _a(assessments, "trailing_pe")
    if a:
        add("valuation", f"Valuation does not expand materially (trailing P/E {a.display})", "trailing_pe", "<=", a.value * 1.25,
            a.value * 1.10, a.value, "x", "medium", ["asm.trailing_pe", a.metric_id], f"Trailing P/E above {a.value * 1.25:.1f}x")
    if technicals and technicals.get("dist_sma_200") is not None:
        d = technicals["dist_sma_200"]
        add("trend", f"Long-term price trend holds (price {fmt_value(d, 'pct')} vs 200-day average)", "dist_sma_200", ">=",
            min(-0.10, d - 0.15), min(-0.05, d - 0.08), d, "pct", "low", ["tech.dist_sma_200"],
            "Price more than 10% below the 200-day average")
    if moat and moat.get("rating") in ("narrow", "wide"):
        out.append({"key": "moat", "description": f"Competitive position remains durable (moat rated {moat['rating']} by analyst)",
                    "kind": "qualitative", "metric_key": None, "comparator": None, "threshold": None, "review_threshold": None,
                    "original_value": None, "current_value": None, "unit": None, "materiality": "high",
                    "evidence_ids": moat.get("evidence_ids", []), "invalidation": "Evidence of share loss, margin compression or substitution",
                    "status": "Valid"})
    for i, q in enumerate(qualitative or []):
        out.append({"key": f"qualitative_{i}", "description": q, "kind": "qualitative", "metric_key": None, "comparator": None,
                    "threshold": None, "review_threshold": None, "original_value": None, "current_value": None, "unit": None,
                    "materiality": "medium", "evidence_ids": [], "invalidation": None, "status": "Valid"})
    return out


def evaluate(comparator: str | None, original: float | None, threshold: float | None, review: float | None,
             current: float | None) -> str:
    """Map a new value onto Improving / Valid / Review / At risk / Invalidated / Unknown."""
    if current is None or comparator is None or threshold is None or review is None or original is None:
        return "Unknown"
    band = abs(review - threshold) or abs(original) * 0.05 or 0.01
    if comparator == ">=":
        if current >= original + band:
            return "Improving"
        if current >= review:
            return "Valid"
        if current >= threshold:
            return "Review"
        if current >= threshold - 2 * band:
            return "At risk"
        return "Invalidated"
    if current <= original - band:
        return "Improving"
    if current <= review:
        return "Valid"
    if current <= threshold:
        return "Review"
    if current <= threshold + 2 * band:
        return "At risk"
    return "Invalidated"


def overall(statuses: list[str]) -> str:
    for s in ("Invalidated", "At risk", "Review"):
        if s in statuses:
            return s
    if statuses and all(s == "Unknown" for s in statuses):
        return "Unknown"
    return "Valid"
