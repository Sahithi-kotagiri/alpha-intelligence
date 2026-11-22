"""Factor engine for the team rulebook (backend/config/rulebook.yaml).

One function computes the raw measures for a company from the same inputs for the subject of an
analysis and for every S&P 500 constituent in the universe screen, so sector percentiles compare
like with like. Pillars, gates, fair value, labels, sizing and confidence follow from the rulebook.
Every number keeps its inputs and formula; nothing missing is estimated.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import pandas as pd

from backend.analysis.config_loader import load

LABELS = ["Strong Buy", "Buy", "Wait", "Watchlist", "Reject"]
INSUFFICIENT = "Insufficient data"


def rulebook() -> dict[str, Any]:
    return load("rulebook")


def _f(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) or math.isinf(v) else v


# ------------------------------------------------------------------ SEC annual series
SEC = {
    "revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet",
                "RevenueFromContractWithCustomerIncludingAssessedTax"],
    "operating_income": ["OperatingIncomeLoss"],
    "pretax": ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
               "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"],
    "tax": ["IncomeTaxExpenseBenefit"],
    "net_income": ["NetIncomeLoss"],
    "ocf": ["NetCashProvidedByUsedInOperatingActivities"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"],
    "sbc": ["ShareBasedCompensation", "AllocatedShareBasedCompensationExpense"],
    "eps": ["EarningsPerShareDiluted"],
    "shares": ["WeightedAverageNumberOfDilutedSharesOutstanding"],
    "assets": ["Assets"],
    "current_liabilities": ["LiabilitiesCurrent"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue"],
    "equity": ["StockholdersEquity"],
}
UNIT = {"eps": "USD/shares", "shares": "shares"}
INSTANT = {"assets", "current_liabilities", "cash", "equity"}
FINANCIAL_SECTORS = {"Financials", "Financial Services"}
FIN_LABELS = {"roic_5y": "ROE, 5-year average (financials)", "roic_stability": "ROE stability (std. dev., 5y)",
              "fcf_yield": "Earnings yield (financials)"}


def sec_annual(facts: dict | None, key: str) -> dict[date, tuple[float, date]]:
    """{fiscal-year end: (value, filed date)} from 10-K values; latest filing per period wins.
    Revenue takes the largest concept per period (total revenue)."""
    out: dict[date, tuple[float, date]] = {}
    if not facts:
        return out
    gaap = facts.get("facts", {}).get("us-gaap", {})
    unit = UNIT.get(key, "USD")
    for concept in SEC[key]:
        for e in gaap.get(concept, {}).get("units", {}).get(unit, []):
            form = str(e.get("form", ""))
            ok_form = form.startswith("10-K") or (key in INSTANT and form.startswith("10-Q"))
            if not ok_form or e.get("val") is None:
                continue
            end = date.fromisoformat(e["end"])
            if key not in INSTANT:
                if not e.get("start"):
                    continue
                days = (end - date.fromisoformat(e["start"])).days
                if not 350 <= days <= 380:
                    continue
            filed = date.fromisoformat(e["filed"])
            val = float(e["val"])
            if key == "capex":
                val = abs(val)
            cur = out.get(end)
            if cur is None:
                out[end] = (val, filed)
            elif key == "revenue" and concept != SEC[key][0]:
                if abs(val) > abs(cur[0]) * 1.001 and filed >= cur[1] - timedelta(days=400):
                    out[end] = (val, filed)
            elif filed > cur[1] and concept == SEC[key][0]:
                out[end] = (val, filed)
        # later concepts only fill periods the earlier ones do not cover (revenue handled above)
    if key in INSTANT:  # keep fiscal-year-end instants only (those reported in 10-Ks, matched to FY ends)
        return out
    return out


def _years(series: dict[date, tuple[float, date]], n: int) -> list[tuple[date, float, date]]:
    ends = sorted(series)[-n:]
    return [(e, series[e][0], series[e][1]) for e in ends]


def _near(series: dict[date, tuple[float, date]], end: date) -> float | None:
    for e, (v, _) in series.items():
        if abs((e - end).days) <= 10:
            return v
    return None


# ------------------------------------------------------------------ valuation helpers
def dcf_value(base_fcf: float, growth: float, r: float, g_term: float, years: int, fade_after: int | None = None) -> float:
    """Present value of FCF growing at `growth` (fading linearly to `g_term` after `fade_after` years, if given)
    for `years`, then a Gordon terminal value."""
    pv, cf = 0.0, base_fcf
    for t in range(1, years + 1):
        g = growth
        if fade_after is not None and t > fade_after:
            g = growth + (g_term - growth) * (t - fade_after) / (years - fade_after)
        cf *= 1 + g
        pv += cf / (1 + r) ** t
    tv = cf * (1 + g_term) / (r - g_term)
    return pv + tv / (1 + r) ** years


def implied_growth(ev: float, base_fcf: float, r: float, g_term: float, years: int) -> float | None:
    """Reverse DCF: the constant growth rate that makes the DCF equal today's enterprise value."""
    if base_fcf is None or base_fcf <= 0 or ev is None or ev <= 0:
        return None
    lo, hi = -0.5, 1.0
    if dcf_value(base_fcf, hi, r, g_term, years) < ev:
        return hi
    if dcf_value(base_fcf, lo, r, g_term, years) > ev:
        return lo
    for _ in range(80):
        mid = (lo + hi) / 2
        if dcf_value(base_fcf, mid, r, g_term, years) > ev:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def cagr(first: float | None, last: float | None, years: float) -> float | None:
    if first is None or last is None or first <= 0 or last <= 0 or years <= 0:
        return None
    return (last / first) ** (1 / years) - 1


def eps_revision(et: Any, col: str) -> float | None:
    """Mean change in consensus EPS for this and next fiscal year versus `col` (e.g. '60daysAgo')."""
    if not isinstance(et, pd.DataFrame) or et.empty:
        return None
    vals = []
    for row in ("0y", "+1y"):
        if row in et.index:
            cur, old = _f(et.loc[row].get("current")), _f(et.loc[row].get(col))
            if cur is not None and old and old > 0:
                vals.append(cur / old - 1)
    return statistics.mean(vals) if vals else None


# ------------------------------------------------------------------ inputs and measures
@dataclass
class FactorInputs:
    ticker: str
    sector: str | None
    info: dict[str, Any]
    facts: dict | None = None
    daily: pd.DataFrame | None = None          # ≥ 1y daily bars (Close, Adj Close)
    monthly: pd.DataFrame | None = None        # ~10y monthly bars with Stock Splits
    eps_trend: pd.DataFrame | None = None
    earnings_history: pd.DataFrame | None = None
    filings: list[dict[str, Any]] = field(default_factory=list)
    as_of: date = field(default_factory=date.today)


def compute_measures(inp: FactorInputs) -> dict[str, Any]:
    """Raw measures + supporting details + gate flags + fair value inputs. None = unavailable (with reason)."""
    cfg = rulebook()
    v = cfg["valuation"]
    m: dict[str, Any] = {}
    why: dict[str, str] = {}
    detail: dict[str, Any] = {}
    info = inp.info or {}
    f = inp.facts
    ser = {k: sec_annual(f, k) for k in SEC}

    # ---- Quality
    roic_years = []
    for end, oi, _ in _years(ser["operating_income"], 5):
        assets, cl, cash = _near(ser["assets"], end), _near(ser["current_liabilities"], end), _near(ser["cash"], end)
        tax, pre = _near(ser["tax"], end), _near(ser["pretax"], end)
        rate = tax / pre if (tax is not None and pre and pre > 0) else 0.21
        rate = min(max(rate, 0.0), 0.35)
        capital = None if None in (assets, cl) else assets - cl - (cash or 0)
        if capital and capital > 0:
            roic_years.append((end.isoformat(), oi * (1 - rate) / capital))
    financial = (inp.sector or "") in FINANCIAL_SECTORS
    detail["financial_substitutes"] = financial
    if financial:  # banks/insurers: capital employed and FCF are not meaningful; use ROE and earnings yield
        roic_years = []
        for end, ni, _ in _years(ser["net_income"], 5):
            eq = _near(ser["equity"], end)
            if eq and eq > 0:
                roic_years.append((end.isoformat(), ni / eq))
    detail["roic_by_year"] = roic_years
    if len(roic_years) >= 3:
        vals = [x for _, x in roic_years]
        m["roic_5y"] = statistics.mean(vals)
        m["roic_stability"] = statistics.pstdev(vals)
    else:
        why["roic_5y"] = why["roic_stability"] = "fewer than 3 fiscal years of operating income and capital employed"
    ni3 = _years(ser["net_income"], 3)
    if financial:
        why["fcf_conversion"] = "not applicable to financials (cash flow reflects lending and deposits)"
    elif len(ni3) >= 2:
        num = den = 0.0
        ok = True
        for end, ni, _ in ni3:
            ocf, cap = _near(ser["ocf"], end), _near(ser["capex"], end)
            sbc = _near(ser["sbc"], end) or 0.0
            if ocf is None or cap is None:
                ok = False
                break
            num += ocf - cap - sbc
            den += ni
        if ok and den > 0:
            m["fcf_conversion"] = max(min(num / den, 3.0), -2.0)
        else:
            why["fcf_conversion"] = "net income not positive over 3 years or cash-flow data missing"
    else:
        why["fcf_conversion"] = "fewer than 2 fiscal years of net income"

    # ---- latest FY cash flow and growth
    price = _f(info.get("currentPrice")) or _f(info.get("regularMarketPrice"))
    if price is None and inp.daily is not None and not inp.daily.empty:
        price = float(inp.daily["Close"].iloc[-1])
    mcap = _f(info.get("marketCap"))
    debt, cashv = _f(info.get("totalDebt")) or 0.0, _f(info.get("totalCash")) or 0.0
    ebitda = _f(info.get("ebitda"))
    ocf_last = _years(ser["ocf"], 1)
    fcf_last = fcf_sbc = None
    if ocf_last:
        end = ocf_last[0][0]
        cap = _near(ser["capex"], end)
        if cap is not None:
            fcf_last = ocf_last[0][1] - cap
            fcf_sbc = fcf_last - (_near(ser["sbc"], end) or 0.0)
    # normalised DCF base: average FCF after SBC over the last 3 fiscal years (through-the-cycle), else the latest year
    fcf_hist = []
    for end, ocf_v, _ in _years(ser["ocf"], 3):
        cap = _near(ser["capex"], end)
        if cap is not None:
            fcf_hist.append(ocf_v - cap - (_near(ser["sbc"], end) or 0.0))
    fcf_base = statistics.mean(fcf_hist) if len(fcf_hist) == 3 else fcf_sbc
    detail.update(fcf_latest_fy=fcf_last, fcf_after_sbc=fcf_sbc, fcf_base=fcf_base,
                  fcf_base_method="3-year average FCF after SBC" if len(fcf_hist) == 3 else "latest FY FCF after SBC",
                  market_cap=mcap, total_debt=debt, total_cash=cashv, price=price)
    rev = _years(ser["revenue"], 6)
    growth5 = None
    if len(rev) >= 4:
        yrs = (rev[-1][0] - rev[0][0]).days / 365.25
        growth5 = cagr(rev[0][1], rev[-1][1], yrs)
    detail["revenue_cagr"] = growth5

    # ---- Value
    if financial and _f(info.get("trailingPE")) and info["trailingPE"] > 0:
        m["fcf_yield"] = 1 / float(info["trailingPE"])  # earnings yield substitutes for FCF yield
    elif fcf_last is not None and mcap and not financial:
        m["fcf_yield"] = fcf_last / mcap
    else:
        why["fcf_yield"] = "free cash flow or market cap unavailable"
    ev = (mcap + debt - cashv) if mcap else None
    ig = implied_growth(ev, fcf_base, v["discount_rate"], v["terminal_growth"], v["years"]) if ev else None
    detail["implied_growth"] = ig
    detail["achieved_growth_label"] = "revenue"
    if financial:
        # earnings-based reverse DCF: growth in net income that justifies today's market cap (equity, not EV)
        ni_hist = _years(ser["net_income"], 6)
        ig = None
        if ni_hist and mcap and ni_hist[-1][1] > 0:
            ig = implied_growth(mcap, ni_hist[-1][1], v["discount_rate"], v["terminal_growth"], v["years"])
        ni_cagr = None
        if len(ni_hist) >= 4:
            ni_cagr = cagr(ni_hist[0][1], ni_hist[-1][1], (ni_hist[-1][0] - ni_hist[0][0]).days / 365.25)
        detail.update(implied_growth=ig, revenue_cagr=ni_cagr, achieved_growth_label="net income",
                      implied_growth_basis="net income vs market cap (financials)")
        why["growth_gap"] = "not applicable to financials (no meaningful free cash flow; earnings-based view shown instead)"
    elif ig is not None and growth5 is not None:
        m["growth_gap"] = growth5 - ig
    elif "growth_gap" not in why:
        why["growth_gap"] = ("free cash flow after SBC is not positive (reverse DCF not meaningful)"
                             if (fcf_base is not None and fcf_base <= 0) else "implied or achieved growth unavailable")
    # own P/E history: split-adjusted SEC EPS at each FY end vs monthly close
    pes = []
    if inp.monthly is not None and not inp.monthly.empty:
        splits = [(d.date(), float(r)) for d, r in inp.monthly.get("Stock Splits", pd.Series(dtype=float)).items() if r and r > 0 and r != 1]
        for end, eps, filed in _years(ser["eps"], 10):
            for sd, r in splits:
                if filed < sd:
                    eps /= r
            sub = inp.monthly.loc[: pd.Timestamp(end)]
            if eps > 0 and not sub.empty:
                pes.append(float(sub["Close"].iloc[-1]) / eps)
    cur_pe = _f(info.get("trailingPE"))
    detail["pe_history"] = [round(x, 1) for x in pes]
    detail["median_pe"] = statistics.median(pes) if len(pes) >= 3 else None
    if cur_pe and cur_pe > 0 and len(pes) >= 3:
        m["pe_vs_history"] = sum(1 for x in pes if x <= cur_pe) / len(pes)
    else:
        why["pe_vs_history"] = "need a positive P/E and at least 3 fiscal years of EPS history"

    # ---- Momentum
    d = inp.daily
    if d is not None and len(d) >= 253:
        a = d["Adj Close"] if "Adj Close" in d else d["Close"]
        m["mom_12_1"] = float(a.iloc[-22] / a.iloc[-253] - 1)
        sma = d["Close"].rolling(200).mean()
        dist = float(d["Close"].iloc[-1] / sma.iloc[-1] - 1)
        slope = float(sma.iloc[-1] / sma.iloc[-22] - 1)
        detail.update(dist_200=dist, slope_200=slope)
        m["trend_200"] = dist  # ranked together with slope below
        m["_slope_200"] = slope
        r = d["Close"].pct_change().dropna().iloc[-252:]
        detail["vol_1y"] = float(r.std() * math.sqrt(252))
        peak = d["Close"].iloc[-756:].cummax()
        detail["max_drawdown_3y"] = float((d["Close"].iloc[-756:] / peak - 1).min())
    else:
        why["mom_12_1"] = why["trend_200"] = "fewer than 253 trading sessions of price history"

    # ---- Revisions
    et = inp.eps_trend
    revs = []
    if isinstance(et, pd.DataFrame) and not et.empty:
        for row in ("0y", "+1y"):
            if row in et.index:
                cur, old = _f(et.loc[row].get("current")), _f(et.loc[row].get("90daysAgo"))
                if cur is not None and old and old > 0:
                    revs.append(cur / old - 1)
    detail["eps_rev_60d"] = eps_revision(et, "60daysAgo")
    if revs:
        m["eps_rev_90d"] = statistics.mean(revs)
    else:
        why["eps_rev_90d"] = "no consensus EPS trend available"
    eh = inp.earnings_history
    if isinstance(eh, pd.DataFrame) and not eh.empty and "surprisePercent" in eh:
        s = [x for x in (_f(y) for y in eh["surprisePercent"].tail(4)) if x is not None]
        if s:
            m["surprise_4q"] = statistics.mean(max(min(x, 0.5), -0.5) for x in s)
            detail["beats_4q"] = sum(1 for x in s if x > 0)
    if "surprise_4q" not in m:
        why["surprise_4q"] = "no earnings surprise history available"

    # ---- gate inputs
    gates: list[dict[str, Any]] = []
    g = cfg["gates"]
    cutoff = inp.as_of - timedelta(days=g["red_flag_days"])
    for fl in inp.filings or []:
        items = [s.strip() for s in str(fl.get("items") or "").split(",") if s.strip()]
        hit = [i for i in items if i in g["red_flag_8k_items"]]
        caution = [i for i in items if i in g.get("caution_8k_items", [])]
        try:
            fd = date.fromisoformat(fl.get("filingDate", ""))
        except ValueError:
            continue
        if fd < cutoff or not str(fl.get("form", "")).startswith("8-K"):
            continue
        if hit:
            gates.append({"id": "red_flag_filing", "effect": "Reject", "reason": f"8-K item(s) {', '.join(hit)} filed {fd}",
                          "url": fl.get("url")})
        elif caution and not any(x["id"] == "listing_notice" for x in gates):
            gates.append({"id": "listing_notice", "effect": "Wait",
                          "reason": f"8-K item 3.01 (listing notice or transfer) filed {fd}: read the filing", "url": fl.get("url")})
    nd = debt - cashv
    lev_max = (g.get("leverage_nd_ebitda_by_sector") or {}).get(inp.sector or "", g["leverage_nd_ebitda"])
    if not financial and fcf_last is not None and fcf_last < 0 and nd > 0 and (ebitda is None or ebitda <= 0 or nd / ebitda > lev_max):
        gates.append({"id": "leverage", "effect": "Reject",
                      "reason": f"net debt/EBITDA {('%.1fx' % (nd / ebitda)) if ebitda and ebitda > 0 else 'n/a (EBITDA ≤ 0)'} "
                                f"(limit {lev_max:g}x for {inp.sector or 'this sector'}) with negative FCF"})
    sh = _years(ser["shares"], 3)
    if len(sh) >= 3 and sh[0][1] > 0:
        ratio = sh[-1][1] / sh[0][1]
        if 0.55 < ratio < 1.8:  # outside this band a split is the likely cause; ignore
            dil = ratio ** 0.5 - 1
            detail["dilution_annual"] = dil
            if dil > g["dilution_max_annual"]:
                gates.append({"id": "dilution", "effect": "Watchlist", "reason": f"share count rising {dil:.1%} a year"})
    if m.get("eps_rev_90d") is not None and m["eps_rev_90d"] < 0 and detail.get("dist_200", 0) < 0 and detail.get("slope_200", 0) < 0:
        gates.append({"id": "falling_estimates_and_trend", "effect": "Wait",
                      "reason": "EPS estimates falling and price below a falling 200-day average"})

    # vendor per-share data sanity: a recent split or an implausible P/E means per-share inputs may be stale
    split_days = g.get("recent_split_days", 30)
    if inp.daily is not None and "Stock Splits" in inp.daily:
        sp = inp.daily["Stock Splits"]
        recent = sp[(sp > 0) & (sp != 1) & (sp.index >= pd.Timestamp(inp.as_of - timedelta(days=split_days)))]
        if len(recent):
            gates.append({"id": "recent_split", "effect": "Watchlist",
                          "reason": f"{recent.iloc[-1]:g}-for-1 split on {recent.index[-1].date()}: vendor per-share data may "
                                    "not be adjusted yet"})
    vpe = _f(info.get("trailingPE"))
    if vpe is not None and 0 < vpe < g.get("min_plausible_pe", 2.0):
        gates.append({"id": "vendor_inconsistency", "effect": "Watchlist",
                      "reason": f"vendor P/E {vpe:.2f} is implausible (EPS and price likely on different share bases)"})

    # ---- fair value
    fv_parts = []
    shares_eq = (mcap / price) if (mcap and price) else None
    if not financial and fcf_base and fcf_base > 0 and growth5 is not None and shares_eq:
        gcap = min(max(growth5, v["growth_cap"][0]), v["growth_cap"][1])
        eq = dcf_value(fcf_base, gcap, v["discount_rate"], v["terminal_growth"], v["years"], v["high_growth_years"]) - nd
        if eq > 0:
            fv_parts.append((f"DCF: {gcap:.1%} growth for {v['high_growth_years']} years, fading to "
                             f"{v['terminal_growth']:.1%}", eq / shares_eq, gcap))
    eps_ttm = _f(info.get("trailingEps"))
    if eps_ttm and eps_ttm > 0 and detail["median_pe"]:
        fv_parts.append(("TTM EPS × own median P/E", eps_ttm * detail["median_pe"], detail["median_pe"]))
    fair = statistics.mean(p[1] for p in fv_parts) if fv_parts else None
    if len(fv_parts) == 2:
        spread = max(p[1] for p in fv_parts) / min(p[1] for p in fv_parts)
        detail["fair_value_spread"] = spread
        detail["fair_value_low_precision"] = spread > v["methods_disagree_ratio"]
    detail["fair_value_methods"] = [{"method": a, "value": round(b, 2), "param": round(c, 4)} for a, b, c in fv_parts]
    return {"ticker": inp.ticker, "sector": inp.sector, "measures": m, "missing": why, "detail": detail, "gates": gates,
            "fair_value": fair, "price": price}


# ------------------------------------------------------------------ ranking, pillars, labels
def _pct(value: float, others: list[float], higher_better: bool) -> float:
    if not others:
        return 50.0
    if higher_better:
        b = sum(1 for o in others if value > o) + 0.5 * sum(1 for o in others if o == value)
    else:
        b = sum(1 for o in others if value < o) + 0.5 * sum(1 for o in others if o == value)
    return 100.0 * b / len(others)


def _anchor(value: float, lo_hi: list[float]) -> float:
    a, b = lo_hi
    if b == a:
        return 50.0
    return max(0.0, min(100.0, 100.0 * (value - a) / (b - a)))


def score_measure(key: str, value: float, sector: str | None, universe: list[dict[str, Any]] | None,
                  subject: str) -> tuple[float, str, int]:
    """Return (score 0–100, basis, n). Basis: sector percentile, universe percentile or absolute anchors."""
    cfg = rulebook()
    spec = cfg["measures"][key]
    hb = spec["better"] == "higher"
    rk = cfg["ranking"]
    raw_key = key

    def vals(rows):
        return [r["measures"][raw_key] for r in rows if r["ticker"] != subject and r["measures"].get(raw_key) is not None]

    if universe:
        sec = vals([r for r in universe if r.get("sector") == sector]) if sector else []
        if len(sec) >= rk["min_sector_values"]:
            return _pct(value, sec, hb), "sector percentile", len(sec)
        allv = vals(universe)
        if len(allv) >= rk["min_universe_values"]:
            return _pct(value, allv, hb), "S&P 500 percentile", len(allv)
    return _anchor(value, spec["anchors"]), "absolute anchors (no peer data)", 0


def pillars(raw: dict[str, Any], universe: list[dict[str, Any]] | None) -> dict[str, Any]:
    cfg = rulebook()
    out: dict[str, Any] = {}
    m = raw["measures"]
    for name, p in cfg["pillars"].items():
        rows, scores, applicable = [], [], 0
        fin = raw.get("detail", {}).get("financial_substitutes")
        for key in p["measures"]:
            spec = cfg["measures"][key]
            label = spec["label"]
            if fin and key in FIN_LABELS:
                label = FIN_LABELS[key]
            reason = raw["missing"].get(key, "unavailable")
            na = m.get(key) is None and reason.startswith("not applicable")
            if not na:
                applicable += 1
            if m.get(key) is None:
                rows.append({"key": key, "label": label, "value": None, "score": None, "reason": reason,
                             "unit": spec["unit"], "not_applicable": na})
                continue
            s, basis, n = score_measure(key, m[key], raw.get("sector"), universe, raw["ticker"])
            if key == "trend_200" and m.get("_slope_200") is not None:
                s2, _, _ = score_measure("_slope_200", m["_slope_200"], raw.get("sector"), universe, raw["ticker"])
                s = (s + s2) / 2
            scores.append(s)
            rows.append({"key": key, "label": label, "value": m[key], "score": round(s, 1), "basis": basis, "n": n,
                         "unit": spec["unit"], "better": spec["better"]})
        missing_ratio = 1 - len(scores) / applicable if applicable else 1.0
        too_sparse = missing_ratio > cfg["ranking"]["pillar_missing_ratio"]
        score = 50.0 if (too_sparse or not scores) else statistics.mean(scores)
        out[name] = {"score": round(score, 1), "weight": p["weight"], "measures": rows, "sparse": too_sparse,
                     "missing_ratio": round(missing_ratio, 2)}
    return out


def classify(raw: dict[str, Any], pil: dict[str, Any], extra_gates: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Apply the rulebook: composite, label rules (first match), then gates that can only lower the label."""
    cfg = rulebook()
    L = cfg["labels"]
    comp = round(sum(p["score"] * p["weight"] for p in pil.values()) / sum(p["weight"] for p in pil.values()), 1)
    q, val, mom = pil["quality"]["score"], pil["value"]["score"], pil["momentum"]["score"]
    scores = [p["score"] for p in pil.values()]
    sparse = sum(1 for p in pil.values() if p["sparse"])
    price, fair = raw.get("price"), raw.get("fair_value")
    pvf = (price / fair) if (price and fair) else None
    trace: list[str] = []
    if sparse >= 2:
        return {"composite": comp, "label": INSUFFICIENT, "trace": [f"{sparse} pillars lack data"], "price_vs_fair": pvf,
                "gates": list(raw.get("gates", [])) + list(extra_gates or [])}
    sb, b, w, wl, rj = L["strong_buy"], L["buy"], L["wait"], L["watchlist"], L["reject"]
    if comp < rj["composite_below"] or q < rj["quality_below"]:
        label = "Reject"
        trace.append(f"Reject: composite {comp} < {rj['composite_below']} or quality {q} < {rj['quality_below']}.")
    elif comp >= sb["composite_min"] and min(scores) >= sb["every_pillar_min"] and pvf is not None and pvf <= sb["price_vs_fair_max"]:
        label = "Strong Buy"
        trace.append(f"Strong Buy: composite {comp} ≥ {sb['composite_min']}, every pillar ≥ {sb['every_pillar_min']}, price ≤ fair value.")
    elif comp >= b["composite_min"] and min(scores) >= b["no_pillar_below"] and pvf is not None and pvf <= b["price_vs_fair_max"]:
        label = "Buy"
        trace.append(f"Buy: composite {comp} ≥ {b['composite_min']}, no pillar < {b['no_pillar_below']}, price ≤ fair value × {b['price_vs_fair_max']}.")
    elif q >= w["quality_min"] and (val < w["value_below"] or mom < w["momentum_below"] or pvf is None or pvf > 1.0):
        label = "Wait"
        why = [x for x, c in (("value pillar low", val < w["value_below"]), ("momentum weak", mom < w["momentum_below"]),
                              ("price above fair value", pvf is not None and pvf > 1.0), ("no fair value", pvf is None)) if c]
        trace.append(f"Wait: quality {q} ≥ {w['quality_min']} but " + ", ".join(why) + ".")
    elif comp >= wl["composite_min"]:
        label = "Watchlist"
        trace.append(f"Watchlist: composite {comp} between {wl['composite_min']} and the Buy rules.")
    else:
        label = "Watchlist" if comp >= rj["composite_below"] else "Reject"
        trace.append(f"{label}: composite {comp}.")
    gates = list(raw.get("gates", [])) + list(extra_gates or [])
    order = {lbl: i for i, lbl in enumerate(LABELS)}
    for gte in gates:
        cap = gte["effect"]
        gte["binding"] = order[label] < order[cap]
        if gte["binding"]:
            trace.append(f"Gate '{gte['id']}' caps the label at {cap}: {gte['reason'].rstrip('.')}.")
            label = cap
    return {"composite": comp, "label": label, "trace": trace, "price_vs_fair": pvf, "gates": gates}


def buy_below(fair: float | None, quality_score: float) -> float | None:
    if not fair:
        return None
    v = rulebook()["valuation"]
    mos = v["margin_of_safety_top_quality"] if quality_score >= v["top_quality_pillar"] else v["margin_of_safety"]
    return round(fair * (1 - mos), 2)


def position_size(label: str, vol: float | None, bench_vol: float | None, worst_loss: float | None) -> dict[str, Any]:
    s = rulebook()["sizing"]
    base = {"Strong Buy": s["strong_buy"], "Buy": s["buy"]}.get(label, 0.0)
    notes = []
    if base and vol and bench_vol and vol / bench_vol > s["halve_if_vol_ratio_above"]:
        base /= 2
        notes.append(f"halved: volatility {vol / bench_vol:.1f}× the market")
    if base and worst_loss is not None and abs(worst_loss) > s["halve_if_worst_loss_above"]:
        base /= 2
        notes.append(f"halved: worst plausible loss {abs(worst_loss):.0%}")
    return {"size": base, "notes": notes}


def confidence(dq_status: str, pil: dict[str, Any], llm_used: bool) -> tuple[float, list[str]]:
    c = rulebook()["confidence"]
    v, why = c["base"], [f"base {c['base']:.0%}"]
    if dq_status == "acceptable":
        v -= c["data_acceptable"]
        why.append(f"data acceptable −{c['data_acceptable']:.0%}")
    elif dq_status in ("poor", "critical"):
        v -= c["data_poor"]
        why.append(f"data {dq_status} −{c['data_poor']:.0%}")
    sp = sum(1 for p in pil.values() if p["sparse"])
    if sp:
        v -= c["per_missing_pillar"] * sp
        why.append(f"{sp} pillar(s) mostly missing −{c['per_missing_pillar'] * sp:.0%}")
    sc = [p["score"] for p in pil.values()]
    if max(sc) >= 80 and min(sc) <= 20:
        v -= c["pillar_disagreement"]
        why.append(f"pillars disagree sharply (max {max(sc):.0f}, min {min(sc):.0f}) −{c['pillar_disagreement']:.0%}")
    if not llm_used:
        v -= c["no_llm"]
        why.append(f"no LLM review of filings and news −{c['no_llm']:.0%}")
    return round(max(c["floor"], min(c["cap"], v)), 3), why


def screen_universe_rows(raws: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pillars + label for every constituent using the universe itself as the peer distribution."""
    out = []
    for r in raws:
        pil = pillars(r, raws)
        cls = classify(r, pil)
        out.append({"ticker": r["ticker"], "sector": r.get("sector"), "pillars": {k: v["score"] for k, v in pil.items()},
                    "pillar_detail": pil, "composite": cls["composite"], "label": cls["label"], "trace": cls["trace"],
                    "gates": cls["gates"], "fair_value": r.get("fair_value"), "price": r.get("price"),
                    "buy_below": buy_below(r.get("fair_value"), pil["quality"]["score"]), "price_vs_fair": cls["price_vs_fair"],
                    "fair_value_methods": r.get("detail", {}).get("fair_value_methods", []),
                    "fair_value_low_precision": bool(r.get("detail", {}).get("fair_value_low_precision"))})
    return out
