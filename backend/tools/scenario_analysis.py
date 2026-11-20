"""Bull / base / bear valuation scenarios and simple portfolio stress scenarios.

Scenarios are *illustrations under stated assumptions*, not forecasts:
- EPS path: base = consensus next-FY EPS when sourced, else TTM EPS × (1 + 3y EPS CAGR capped to ±30%).
  Bull/bear apply ± the configured EPS shock.
- Multiple: bear = peer 25th percentile P/E (or own-history min), base = peer median
  (or current), bull = peer 75th percentile (or own-history max).
- When EPS is ≤ 0, EV/Sales is used instead with the same percentile logic.
"""

from __future__ import annotations

from typing import Any


def eps_scenarios(price: float | None, eps_ttm: float | None, eps_next: float | None, eps_cagr: float | None,
                  pe_p25: float | None, pe_median: float | None, pe_p75: float | None, current_pe: float | None,
                  eps_shock: float = 0.15) -> dict[str, Any]:
    if price is None or eps_ttm is None:
        return {"available": False, "reason": "price or TTM EPS unavailable"}
    if eps_ttm <= 0 and (eps_next is None or eps_next <= 0):
        return {"available": False, "reason": "earnings are negative; P/E-based scenarios not meaningful"}
    if eps_next is not None and eps_next > 0:
        base_eps, eps_src = eps_next, "consensus next-FY EPS (estimate)"
    else:
        g = max(min(eps_cagr if eps_cagr is not None else 0.0, 0.30), -0.30)
        base_eps, eps_src = eps_ttm * (1 + g), f"TTM EPS grown at 3y CAGR {g:.1%} (capped ±30%)"
    mult = {"bear": pe_p25, "base": pe_median or current_pe, "bull": pe_p75}
    msrc = "peer P/E percentiles"
    if pe_p25 is None or pe_p75 is None:
        if current_pe is None:
            return {"available": False, "reason": "no peer or current P/E available"}
        mult = {"bear": current_pe * 0.75, "base": current_pe, "bull": current_pe * 1.15}
        msrc = "current P/E −25% / unchanged / +15% (peer data unavailable)"
    eps = {"bear": base_eps * (1 - eps_shock), "base": base_eps, "bull": base_eps * (1 + eps_shock)}
    out = {"available": True, "method": "EPS × P/E", "eps_basis": eps_src, "multiple_basis": msrc,
           "eps_shock": eps_shock, "horizon": "approximately 12 months", "scenarios": {}}
    for k in ("bear", "base", "bull"):
        p = eps[k] * mult[k] if mult[k] else None
        out["scenarios"][k] = {"eps": round(eps[k], 4), "pe": round(mult[k], 2) if mult[k] else None,
                               "price": round(p, 2) if p else None,
                               "return": round(p / price - 1, 4) if p else None}
    return out


def sales_scenarios(price: float | None, ev_sales_now: float | None, revenue_ttm: float | None, rev_growth: float | None,
                    net_debt: float | None, shares: float | None, p25: float | None, med: float | None,
                    p75: float | None, shock: float = 0.10) -> dict[str, Any]:
    if None in (price, revenue_ttm, shares) or not shares:
        return {"available": False, "reason": "price, revenue or share count unavailable"}
    g = max(min(rev_growth or 0.0, 0.5), -0.3)
    rev = {"bear": revenue_ttm * (1 + g - shock), "base": revenue_ttm * (1 + g), "bull": revenue_ttm * (1 + g + shock)}
    mult = {"bear": p25, "base": med or ev_sales_now, "bull": p75}
    if p25 is None or p75 is None:
        if ev_sales_now is None:
            return {"available": False, "reason": "no EV/Sales reference available"}
        mult = {"bear": ev_sales_now * 0.7, "base": ev_sales_now, "bull": ev_sales_now * 1.2}
    out = {"available": True, "method": "Revenue × EV/Sales", "scenarios": {}, "horizon": "approximately 12 months",
           "eps_basis": f"TTM revenue grown at {g:.1%} ± {shock:.0%}"}
    for k in rev:
        if mult[k] is None:
            continue
        equity = rev[k] * mult[k] - (net_debt or 0)
        p = equity / shares
        out["scenarios"][k] = {"revenue": rev[k], "ev_sales": round(mult[k], 2), "price": round(p, 2),
                               "return": round(p / price - 1, 4)}
    return out


def market_stress(beta: float | None, shocks: tuple[float, ...] = (-0.10, -0.20, -0.35)) -> list[dict[str, Any]]:
    if beta is None:
        return []
    return [{"market_move": s, "estimated_move": round(beta * s, 4),
             "method": "beta × market move (linear; ignores idiosyncratic risk)"} for s in shocks]
