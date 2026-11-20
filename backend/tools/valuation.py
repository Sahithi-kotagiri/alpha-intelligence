"""Valuation metrics, computed from our statements + latest price, with vendor cross-references.

Forward metrics use analyst consensus from the market-data vendor and are always
labelled as estimates. PEG has two documented variants:
- Forward PEG  = trailing P/E ÷ (consensus next-fiscal-year EPS growth × 100)
- Trailing PEG = trailing P/E ÷ (3-year diluted EPS CAGR × 100)
Both are not meaningful when P/E is not meaningful or growth ≤ 0.
"""

from __future__ import annotations

import statistics
from datetime import date
from typing import Any

import pandas as pd
from pydantic import BaseModel

from backend.data.normalisation import FinancialData
from backend.tools.financial_metrics import latest_metric
from backend.tools.types import Metric, not_meaningful, unavailable


class MarketSnapshot(BaseModel):
    price: float | None
    price_date: date | None
    price_id: str
    shares_outstanding: float | None
    shares_source: str
    shares_id: str
    market_cap: float | None
    market_cap_id: str = "val.market_cap"
    currency: str | None
    vendor: dict[str, Any] = {}
    notes: list[str] = []


def _f(x) -> float | None:
    try:
        v = float(x)
        return None if v != v else v
    except (TypeError, ValueError):
        return None


def market_snapshot(prices: pd.DataFrame | None, info: dict[str, Any], fd: FinancialData | None) -> MarketSnapshot:
    price = price_date = None
    if prices is not None and not prices.empty:
        price = float(prices["Close"].iloc[-1])
        price_date = prices.index[-1].date()
    shares = _f(info.get("sharesOutstanding"))
    src = "vendor sharesOutstanding (Yahoo)"
    sid = "val.shares_outstanding.vendor"
    if shares is None and fd is not None and fd.latest_instant.get("shares_outstanding"):
        p = fd.latest_instant["shares_outstanding"]
        shares, src, sid = p.value, f"balance sheet {p.fiscal_period}", p.id
    mcap = price * shares if (price is not None and shares) else None
    notes: list[str] = []
    vmc = _f(info.get("marketCap"))
    if mcap and vmc and abs(mcap / vmc - 1) > 0.15:
        # multi-class share structures (e.g. BRK.A/BRK.B, GOOG/GOOGL): price × one class's shares understates value
        notes.append(f"Price × reported shares ({mcap / 1e9:,.0f}B) differs from vendor company-wide market cap "
                     f"({vmc / 1e9:,.0f}B) by {abs(mcap / vmc - 1):.0%}: multi-class share structure suspected; vendor market "
                     "cap used and shares expressed as equivalents of the quoted class.")
        mcap, shares = vmc, vmc / price
        src = "vendor company-wide market cap ÷ price (class-equivalent shares)"
    vendor_keys = ["trailingPE", "forwardPE", "pegRatio", "trailingPegRatio", "debtToEquity", "enterpriseValue",
                   "enterpriseToEbitda", "enterpriseToRevenue", "priceToBook", "priceToSalesTrailing12Months",
                   "marketCap", "forwardEps", "trailingEps", "dividendYield", "beta", "earningsGrowth", "revenueGrowth",
                   "returnOnEquity", "grossMargins", "operatingMargins", "profitMargins", "freeCashflow",
                   "heldPercentInsiders", "heldPercentInstitutions", "auditRisk", "boardRisk", "compensationRisk",
                   "shareHolderRightsRisk", "overallRisk", "recommendationMean", "numberOfAnalystOpinions",
                   "targetMeanPrice", "targetLowPrice", "targetHighPrice", "shortPercentOfFloat", "averageVolume"]
    return MarketSnapshot(price=price, price_date=price_date, price_id=f"px.close.{price_date}", notes=notes,
                          shares_outstanding=shares, shares_source=src, shares_id=sid, market_cap=mcap,
                          currency=info.get("currency"), vendor={k: info.get(k) for k in vendor_keys if info.get(k) is not None})


def consensus_growth(estimates: dict[str, Any]) -> tuple[float | None, float | None, str | None]:
    """Return (next-FY EPS growth, next-FY EPS, source note) from vendor consensus tables."""
    ee = estimates.get("earnings_estimate") if estimates else None
    if ee is None or not hasattr(ee, "loc"):
        return None, None, None
    try:
        row = ee.loc["+1y"]
        g = _f(row.get("growth"))
        eps = _f(row.get("avg"))
        n = row.get("numberOfAnalysts")
        return g, eps, f"Yahoo consensus next fiscal year ({int(n) if _f(n) else '?'} analysts)"
    except KeyError:
        return None, None, None


def compute_valuation(fd: FinancialData | None, snap: MarketSnapshot, metrics: list[Metric],
                      estimates: dict[str, Any], currency_mismatch: bool = False) -> list[Metric]:
    out: list[Metric] = []
    price, mcap = snap.price, snap.market_cap
    period = f"price {snap.price_date}"
    base_inputs = [snap.price_id]

    def add(m: Metric) -> Metric:
        if snap.price_date:
            m.notes.append(f"Price as of {snap.price_date}.")
        out.append(m)
        return m

    out.append(Metric(id="val.market_cap", key="market_cap", label="Market capitalisation", category="valuation",
                      value=mcap, unit="USD", period=period, period_end=snap.price_date,
                      formula="latest close × shares outstanding", inputs=[snap.price_id, snap.shares_id],
                      status="ok" if mcap else "unavailable", reason=None if mcap else "price or shares unavailable",
                      notes=[f"Shares from {snap.shares_source}."] + snap.notes))
    if currency_mismatch:
        for key, label in [("trailing_pe", "Trailing P/E"), ("forward_pe", "Forward P/E"), ("peg_forward", "PEG (forward)"),
                           ("ev_to_ebitda", "EV/EBITDA"), ("price_to_fcf", "Price / free cash flow")]:
            out.append(not_meaningful(f"val.{key}", key, label, "valuation", "x", "n/a",
                                      "financial-statement currency differs from price currency; no FX conversion applied"))
        return out
    ttm = fd.ttm if fd else {}
    li = fd.latest_instant if fd else {}

    def ratio(key, label, num, num_id, den_pt, formula, hib=False, unit="x", allow_neg_den=False, extra_inputs=()):
        mid = f"val.{key}"
        if num is None:
            return add(unavailable(mid, key, label, "valuation", unit, formula, "price/market cap unavailable",
                                   period, higher_is_better=hib))
        if den_pt is None or den_pt.value is None:
            return add(unavailable(mid, key, label, "valuation", unit, formula, "denominator unavailable", period,
                                   higher_is_better=hib))
        if den_pt.value <= 0 and not allow_neg_den:
            return add(not_meaningful(mid, key, label, "valuation", unit, formula,
                                      f"{den_pt.label} is zero or negative ({den_pt.value:,.4g})", period,
                                      [num_id, den_pt.id], hib))
        return add(Metric(id=mid, key=key, label=label, category="valuation", value=num / den_pt.value, unit=unit,
                          period=period, period_end=snap.price_date, formula=formula,
                          inputs=[num_id, den_pt.id, *extra_inputs], higher_is_better=hib))

    eps_t = ttm.get("diluted_eps")
    v_eps = _f(snap.vendor.get("trailingEps"))
    class_note = None
    if v_eps and v_eps > 0 and (eps_t is None or eps_t.value is None or eps_t.value <= 0
                                or not (0.2 < eps_t.value / v_eps < 5)):
        if eps_t is not None and eps_t.value and not (0.2 < eps_t.value / v_eps < 5):
            class_note = (f"Statement EPS {eps_t.value:,.2f} is inconsistent with the quoted share class (vendor EPS "
                          f"{v_eps:,.2f}); multi-class shares suspected, vendor EPS used.")
        elif eps_t is None or eps_t.value is None:
            class_note = "TTM diluted EPS unavailable from statements; vendor trailing EPS used."
        if class_note:
            from backend.data.provenance import DataPoint as _DP

            eps_t = _DP(id="vendor.trailing_eps", field="diluted_eps", label="Trailing EPS (vendor, quoted share class)",
                        value=v_eps, unit="USD/share", period_type="ttm", source_name="Yahoo Finance via yfinance",
                        source_type="market_data_vendor", quality="unverified", notes=[class_note])
    pe = ratio("trailing_pe", "Trailing P/E", price, snap.price_id, eps_t, "latest close / TTM diluted EPS")
    if class_note:
        pe.notes.append(class_note)
    vend_pe = _f(snap.vendor.get("trailingPE"))
    if pe.ok and vend_pe:
        diff = abs(pe.value - vend_pe) / vend_pe
        pe.notes.append(f"Vendor trailing P/E {vend_pe:.2f} ({'consistent' if diff < 0.05 else f'differs by {diff:.0%}'}).")
    # forward P/E
    g_next, eps_next, est_note = consensus_growth(estimates)
    fwd_eps = eps_next or _f(snap.vendor.get("forwardEps"))
    if fwd_eps is None:
        add(unavailable("val.forward_pe", "forward_pe", "Forward P/E", "valuation", "x",
                        "latest close / consensus next-FY EPS", "no sourced consensus EPS estimate", period))
    elif fwd_eps <= 0:
        add(not_meaningful("val.forward_pe", "forward_pe", "Forward P/E", "valuation", "x",
                           "latest close / consensus next-FY EPS", "consensus EPS is zero or negative", period))
    elif price:
        add(Metric(id="val.forward_pe", key="forward_pe", label="Forward P/E", category="valuation", value=price / fwd_eps,
                   unit="x", period=period, period_end=snap.price_date,
                   formula="latest close / consensus next-fiscal-year EPS (estimate)",
                   inputs=[snap.price_id, "est.eps_next_fy"], higher_is_better=False,
                   source="Calculated from vendor consensus estimate",
                   notes=[f"EPS estimate {fwd_eps:.2f} from {est_note or 'vendor forwardEps'}; estimates are not facts."]))
    # PEG
    if not pe.ok:
        for key, label in [("peg_forward", "PEG (forward growth)"), ("peg_trailing", "PEG (3-year EPS CAGR)")]:
            add(not_meaningful(f"val.{key}", key, label, "valuation", "x", "trailing P/E / (growth × 100)",
                               f"trailing P/E not meaningful ({pe.reason})", period))
    else:
        if g_next is None:
            add(unavailable("val.peg_forward", "peg_forward", "PEG (forward growth)", "valuation", "x",
                            "trailing P/E / (consensus next-FY EPS growth × 100)", "no consensus growth estimate", period))
        elif g_next <= 0:
            add(not_meaningful("val.peg_forward", "peg_forward", "PEG (forward growth)", "valuation", "x",
                               "trailing P/E / (consensus next-FY EPS growth × 100)",
                               f"expected EPS growth is {g_next:.1%} (≤ 0)", period))
        else:
            add(Metric(id="val.peg_forward", key="peg_forward", label="PEG (forward growth)", category="valuation",
                       value=pe.value / (g_next * 100), unit="x", period=period, period_end=snap.price_date,
                       formula="trailing P/E / (consensus next-FY EPS growth % )", inputs=[pe.id, "est.eps_growth_next_fy"],
                       higher_is_better=False, source="Calculated from vendor consensus estimate",
                       notes=[f"Growth {g_next:.1%} from {est_note}."]))
        c3 = latest_metric(metrics, "diluted_eps_cagr_3y")
        if c3 is None or not c3.ok:
            add(not_meaningful("val.peg_trailing", "peg_trailing", "PEG (3-year EPS CAGR)", "valuation", "x",
                               "trailing P/E / (3y diluted EPS CAGR × 100)",
                               (c3.reason if c3 else "3-year EPS CAGR unavailable"), period))
        elif c3.value <= 0:
            add(not_meaningful("val.peg_trailing", "peg_trailing", "PEG (3-year EPS CAGR)", "valuation", "x",
                               "trailing P/E / (3y diluted EPS CAGR × 100)", f"3-year EPS CAGR is {c3.value:.1%}", period,
                               [pe.id, c3.id]))
        else:
            add(Metric(id="val.peg_trailing", key="peg_trailing", label="PEG (3-year EPS CAGR)", category="valuation",
                       value=pe.value / (c3.value * 100), unit="x", period=period, period_end=snap.price_date,
                       formula="trailing P/E / (3-year diluted EPS CAGR %)", inputs=[pe.id, c3.id], higher_is_better=False))
    vpeg = _f(snap.vendor.get("trailingPegRatio")) or _f(snap.vendor.get("pegRatio"))
    if vpeg:
        out.append(Metric(id="val.peg_vendor", key="peg_vendor", label="PEG (vendor-reported)", category="valuation",
                          value=vpeg, unit="x", period=period, formula="Yahoo Finance reported PEG (methodology not disclosed)",
                          inputs=["vendor.pegRatio"], higher_is_better=False, source="Yahoo Finance via yfinance",
                          notes=["Vendor PEG shown for reference only; not used in scoring."]))
    # EV
    debt = li.get("total_debt")
    cash = li.get("cash_and_st_investments") or li.get("cash")
    ev = None
    if mcap and debt and debt.value is not None and cash and cash.value is not None:
        ev = mcap + debt.value - cash.value
        m = Metric(id="val.enterprise_value", key="enterprise_value", label="Enterprise value", category="valuation",
                   value=ev, unit="USD", period=period, period_end=snap.price_date,
                   formula="market cap + total debt − cash & short-term investments", inputs=["val.market_cap", debt.id, cash.id],
                   notes=["Excludes minority interest and preferred equity (not separately available)."])
        vev = _f(snap.vendor.get("enterpriseValue"))
        if vev:
            m.notes.append(f"Vendor EV {vev / 1e9:,.1f}B ({abs(ev - vev) / vev:.0%} difference).")
        add(m)
    else:
        add(unavailable("val.enterprise_value", "enterprise_value", "Enterprise value", "valuation", "USD",
                        "market cap + total debt − cash", "market cap, debt or cash unavailable", period))
    ev_id = "val.enterprise_value"
    ratio("ev_to_ebitda", "EV / EBITDA", ev, ev_id, ttm.get("ebitda"), "enterprise value / TTM EBITDA")
    ratio("ev_to_sales", "EV / Sales", ev, ev_id, ttm.get("revenue"), "enterprise value / TTM revenue")
    ratio("price_to_sales", "Price / Sales", mcap, "val.market_cap", ttm.get("revenue"), "market cap / TTM revenue")
    ratio("price_to_book", "Price / Book", mcap, "val.market_cap", li.get("equity"), "market cap / shareholders' equity")
    ratio("price_to_fcf", "Price / free cash flow", mcap, "val.market_cap", ttm.get("free_cash_flow"),
          "market cap / TTM free cash flow")
    fcf = ttm.get("free_cash_flow")
    if mcap and fcf and fcf.value is not None:
        add(Metric(id="val.fcf_yield", key="fcf_yield", label="Free cash flow yield", category="valuation",
                   value=fcf.value / mcap, unit="pct", period=period, period_end=snap.price_date,
                   formula="TTM free cash flow / market cap", inputs=[fcf.id, "val.market_cap"], higher_is_better=True))
    if price and eps_t and eps_t.value is not None:
        add(Metric(id="val.earnings_yield", key="earnings_yield", label="Earnings yield", category="valuation",
                   value=eps_t.value / price, unit="pct", period=period, period_end=snap.price_date,
                   formula="TTM diluted EPS / latest close", inputs=[eps_t.id, snap.price_id], higher_is_better=True))
    div = ttm.get("dividends_paid")
    if mcap and div and div.value is not None:
        add(Metric(id="val.dividend_yield", key="dividend_yield", label="Dividend yield (cash paid)", category="valuation",
                   value=div.value / mcap, unit="pct", period=period, period_end=snap.price_date,
                   formula="TTM dividends paid / market cap", inputs=[div.id, "val.market_cap"], higher_is_better=None))
    bb, iss = ttm.get("buybacks"), ttm.get("stock_issuance")
    if mcap and (bb or div):
        net = (bb.value if bb and bb.value else 0) - (iss.value if iss and iss.value else 0) + (div.value if div and div.value else 0)
        add(Metric(id="val.shareholder_yield", key="shareholder_yield", label="Shareholder yield", category="capital_allocation",
                   value=net / mcap, unit="pct", period=period, period_end=snap.price_date,
                   formula="(TTM dividends + buybacks − share issuance) / market cap",
                   inputs=[p.id for p in (bb, iss, div) if p] + ["val.market_cap"], higher_is_better=True))
    return out


def historical_valuation(fd: FinancialData | None, prices: pd.DataFrame | None, current_pe: Metric | None) -> list[Metric]:
    """P/E at each fiscal-year end (close on/before period end ÷ that year's diluted EPS).

    Approximation: ignores filing lag (the EPS was not public on the period-end date).
    """
    out: list[Metric] = []
    if fd is None or prices is None or prices.empty:
        return out
    pes = []
    for p in fd.annual.get("diluted_eps", []):
        if p.value is None or p.period_end is None or p.value <= 0:
            continue
        sub = prices.loc[: pd.Timestamp(p.period_end)]
        if sub.empty:
            continue
        px = float(sub["Close"].iloc[-1])
        pe = px / p.value
        pes.append(pe)
        out.append(Metric(id=f"val.pe_hist.A{p.period_end}", key="pe_historical", label="P/E at fiscal year end",
                          category="valuation_history", value=pe, unit="x", period=p.fiscal_period, period_end=p.period_end,
                          formula="close on/before fiscal year end / diluted EPS for that year",
                          inputs=[p.id, f"px.close.{sub.index[-1].date()}"], higher_is_better=False,
                          notes=["Ignores filing lag; price history is split-adjusted, EPS is latest-filed (split-adjusted)."]))
    if len(pes) >= 3 and current_pe is not None and current_pe.ok:
        pct = sum(1 for x in pes if x <= current_pe.value) / len(pes)
        out.append(Metric(id="val.pe_vs_own_history", key="pe_vs_own_history", label="Current P/E percentile vs own history",
                          category="valuation_history", value=pct, unit="pct", period=f"{len(pes)} fiscal years",
                          formula="share of fiscal-year-end P/Es ≤ current trailing P/E",
                          inputs=[current_pe.id] + [m.id for m in out], higher_is_better=False,
                          notes=[f"Historical P/E range {min(pes):.1f}–{max(pes):.1f}, median {statistics.median(pes):.1f}."]))
    return out
