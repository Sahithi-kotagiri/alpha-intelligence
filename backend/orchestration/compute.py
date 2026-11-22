"""Deterministic compute stage: metrics, valuation, peers, assessments, scores, technicals,
risk, scenarios, portfolio context, evidence pack and chart series."""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from backend.analysis.assessment import ASSESSED_KEYS, Assessor, extra_metrics
from backend.analysis.classification import classify
from backend.analysis.config_loader import policy, thresholds
from backend.analysis.evidence import build_pack
from backend.analysis.peers import choose_peer_group, fetch_peer_infos, peer_levels, peer_stats
from backend.analysis.scoring import risk_score, score_from_assessments, technical_score
from backend.data.ingestion import IngestionResult
from backend.data.validation import DataQualityReport
from backend.tools import portfolio_metrics as PM
from backend.tools.financial_metrics import FundamentalCalculator, latest_metric
from backend.tools.risk_metrics import compute_risk
from backend.tools.scenario_analysis import eps_scenarios, sales_scenarios
from backend.tools.technical_indicators import chart_series, compute_technicals
from backend.tools.valuation import (
    compute_valuation,
    consensus_growth,
    historical_valuation,
    market_snapshot,
)

log = logging.getLogger(__name__)


def portfolio_context(providers, portfolio: dict[str, Any] | None, candidate: str, cand_prices: pd.DataFrame | None,
                      sector_of: dict[str, str | None], emit) -> dict[str, Any]:
    if not portfolio or not portfolio.get("holdings"):
        return {"available": False, "reason": "No portfolio holdings supplied."}
    holdings = portfolio["holdings"]
    tickers = sorted({h["ticker"].upper() for h in holdings})
    emit("portfolio", "started", f"Retrieving prices for {len(tickers)} portfolio holdings")
    price_map: dict[str, pd.DataFrame] = {}
    failed = []
    for t in tickers:
        try:
            price_map[t] = providers.market.get_price_history(t)
        except Exception as exc:  # recorded, not hidden
            failed.append({"ticker": t, "error": str(exc)})
    last = {t: float(df["Close"].iloc[-1]) for t, df in price_map.items()}
    w = PM.weights_from_holdings(holdings, last, float(portfolio.get("cash") or 0))
    if cand_prices is not None:
        price_map[candidate] = cand_prices
    rets = PM.returns_frame(price_map, 252)
    tol = portfolio.get("risk_tolerance")
    cand_w = policy()["allocation"]["max_position_by_tolerance"].get(tol, 0.05)
    if portfolio.get("max_position"):
        cand_w = min(cand_w, portfolio["max_position"])
    sectors = dict(sector_of)
    impact = PM.candidate_impact(w["weights"], w["cash_weight"], candidate, cand_w, rets, sectors)
    emit("portfolio", "completed", f"Portfolio analytics computed ({len(rets)} sessions, {len(failed)} holdings without prices)")
    return {"available": bool(w["weights"]), "weights": w["weights"], "cash_weight": w["cash_weight"],
            "total_value": w["total_value"], "weight_method": w["method"], "missing_prices": w["missing_prices"] + [f["ticker"] for f in failed],
            "risk_tolerance": tol, "horizon": portfolio.get("horizon"), "max_position": portfolio.get("max_position"),
            "excluded_sectors": portfolio.get("excluded_sectors") or [], "impact": impact,
            "sector_exposure_before": impact.get("sector_before"), "correlation": PM.correlation_matrix(rets),
            "illustrative_weight_note": f"Candidate impact computed at an illustrative {cand_w:.0%} weight "
                                        f"({'risk-tolerance cap' if tol else 'default 5%; no risk tolerance supplied'})."}


def financial_chart_series(fd) -> dict[str, Any]:
    if fd is None:
        return {}
    out: dict[str, Any] = {}
    for period, src in (("annual", fd.annual), ("quarterly", fd.quarterly)):
        block: dict[str, Any] = {}
        for fld in ("revenue", "diluted_eps", "net_income", "operating_income", "gross_profit", "operating_cash_flow",
                    "capex", "free_cash_flow", "total_debt", "cash_and_st_investments", "cash", "equity", "diluted_shares"):
            pts = src.get(fld, [])
            block[fld] = [{"period": p.fiscal_period, "end": p.period_end.isoformat() if p.period_end else None, "value": p.value,
                           "id": p.id, "source": p.source_name, "quality": str(p.quality.value if hasattr(p.quality, "value") else p.quality)}
                          for p in pts]
        out[period] = block
    return out


def rulebook_stage(providers, res: IngestionResult, dq: DataQualityReport, factor_peers: list[dict[str, Any]] | None,
                   emit) -> tuple[dict[str, Any], dict[str, Any], float | None]:
    """Team rulebook: raw factors for the subject, ranked against the screened S&P 500 (same formulas)."""
    from backend.services import rulebook_service as RB

    ident = res.identity
    emit("rulebook", "started", "Applying the team rulebook: quality, value, momentum and revisions pillars, gates, "
         "fair value and buy-below price")
    raw = RB.factor_raw(providers, ident.ticker, ident.sector, ident.cik, info=res.info, daily=res.prices,
                        filings=res.filings)
    bench_vol = None
    if res.benchmark_prices is not None and len(res.benchmark_prices) > 60:
        r = res.benchmark_prices["Close"].pct_change().dropna().iloc[-252:]
        bench_vol = float(r.std() * (252 ** 0.5))
    peers = factor_peers or []
    prelim = RB.assess_subject(raw, peers, dq.status, llm_used=False, bench_vol=bench_vol)
    basis = "S&P 500 sector peers from the latest universe screen" if peers else \
        "absolute anchors (run a universe screen for sector percentiles)"
    emit("rulebook", "completed", f"Rulebook (before committee): {prelim['label']}, composite {prelim['composite']:.0f}; "
         + ", ".join(f"{k} {v['score']:.0f}" for k, v in prelim["pillars"].items()) + f"; ranked against {basis}",
         detail={"label": prelim["label"], "composite": prelim["composite"], "peers": len(peers)})
    return raw, prelim, bench_vol


def run_compute(providers, res: IngestionResult, dq: DataQualityReport, params: dict[str, Any],
                universe: list[dict[str, Any]], snapshot_infos: dict[str, dict[str, Any]], emit,
                factor_peers: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    fd = res.financials
    emit("calculate_metrics", "started", "Calculating financial ratios, growth, cash flow and valuation metrics")
    metrics = FundamentalCalculator(fd).compute() if fd else []
    snap = market_snapshot(res.prices, res.info, fd)
    mismatch = any(i.code == "currency_mismatch" for i in dq.issues)
    val = compute_valuation(fd, snap, metrics, res.estimates, mismatch)
    metrics += val
    metrics += historical_valuation(fd, res.prices, next((m for m in val if m.key == "trailing_pe"), None))
    metrics += extra_metrics(fd, snap)
    emit("calculate_metrics", "completed", f"{sum(1 for m in metrics if m.ok)} metrics calculated, "
         f"{sum(1 for m in metrics if not m.ok)} unavailable or not meaningful")
    # peers
    pg = choose_peer_group(res.identity.ticker, res.identity.sector, res.identity.sub_industry, universe)
    levels = peer_levels(res.identity.ticker, res.identity.sector, res.identity.sub_industry, universe)
    lim = thresholds()["peer_group"]["max_on_demand_fetch"]
    live = sorted(set((levels["sub_industry"].tickers if "sub_industry" in levels else []) +
                      (levels["sector"].tickers if "sector" in levels else [])))
    emit("peers", "started", f"Retrieving peer metrics: sub-industry '{res.identity.sub_industry}' and sector "
         f"'{res.identity.sector}' ({len(live)} companies; universe level from screen snapshot when available)")
    infos = fetch_peer_infos(providers.market, live, snapshot_infos, limit=lim)
    all_infos = {**snapshot_infos, **infos}
    stats_by_level = {}
    for lvl, grp in levels.items():
        grp.with_data = sorted(t for t in grp.tickers if t in all_infos)
        stats_by_level[lvl] = peer_stats({t: all_infos[t] for t in grp.with_data})
    stats = stats_by_level.get(pg.level, {})
    pg.with_data = levels[pg.level].with_data if pg.level in levels else []
    emit("peers", "completed", "Peer data: " + ", ".join(f"{lvl.replace('_', '-')} {len(g.with_data)}/{len(g.tickers)}"
                                                         for lvl, g in levels.items()))
    ni = fd.ttm.get("net_income").value if fd and fd.ttm.get("net_income") else None
    eq = fd.latest_instant.get("equity").value if fd and fd.latest_instant.get("equity") else None
    profile = classify(res.identity.sector, res.identity.sub_industry, metrics, ni, eq)
    emit("thresholds", "started", f"Evaluating metrics against {pg.level.replace('_', '-')} peers and "
         f"{profile.primary} business-type rules")
    assessments = Assessor(metrics, profile, levels, stats_by_level, res.info).assess_all(ASSESSED_KEYS)
    fq = score_from_assessments("fundamental_quality", assessments)
    vs = score_from_assessments("valuation", assessments)
    tech = risk = None
    tscore = rscore = None
    chart = {}
    if res.prices is not None and len(res.prices) >= 30:
        tech = compute_technicals(res.prices, res.benchmark_prices, res.sector_prices)
        tscore = technical_score(tech)
        risk = compute_risk(res.prices, res.benchmark_prices)
        rscore = risk_score(risk, assessments)
        chart = chart_series(res.prices, res.benchmark_prices)
    emit("thresholds", "completed", f"Assessments complete: quality {fq.score}, valuation {vs.score}, "
         f"technical {tscore.score if tscore else 'n/a'}, risk {rscore.score if rscore else 'n/a'} (0–10)")
    # scenarios
    eps_t = fd.ttm.get("diluted_eps").value if fd and fd.ttm.get("diluted_eps") else None
    _, eps_next, _ = consensus_growth(res.estimates)
    cagr = latest_metric(metrics, "diluted_eps_cagr_3y")
    pe = latest_metric(metrics, "trailing_pe")
    pe_st = stats.get("trailing_pe")
    scen = eps_scenarios(snap.price, eps_t, eps_next, cagr.value if cagr and cagr.ok else None,
                         pe_st.p25 if pe_st else None, pe_st.median if pe_st else None, pe_st.p75 if pe_st else None,
                         pe.value if pe and pe.ok else None)
    if not scen.get("available") and fd:
        evs = latest_metric(metrics, "ev_to_sales")
        nd = latest_metric(metrics, "net_debt")
        rg = latest_metric(metrics, "revenue_yoy_annual")
        scen = sales_scenarios(snap.price, evs.value if evs and evs.ok else None,
                               fd.ttm.get("revenue").value if fd.ttm.get("revenue") else None, rg.value if rg and rg.ok else None,
                               nd.value if nd and nd.ok else None, snap.shares_outstanding, None, None, None)
    # portfolio
    pf = {"available": False, "reason": "Portfolio context not requested."}
    if params.get("include_portfolio"):
        sector_of = {u["symbol"]: u.get("sector") for u in universe}
        sector_of[res.identity.ticker] = res.identity.sector
        pf = portfolio_context(providers, params.get("portfolio"), res.identity.ticker, res.prices, sector_of, emit)
    def ser(st):
        return {k: {"n": s.n, "median": s.median, "p25": s.p25, "p75": s.p75} for k, s in st.items()}

    peer_info = {"level": pg.level, "name": pg.name, "n": len(pg.tickers), "n_with_data": len(pg.with_data),
                 "tickers": pg.tickers, "with_data": pg.with_data, "source": pg.source, "stats": ser(stats),
                 "levels": {lvl: {"name": g.name, "n": len(g.tickers), "n_with_data": len(g.with_data),
                                  "tickers": g.tickers if lvl != "universe" else [], "stats": ser(stats_by_level[lvl])}
                            for lvl, g in levels.items()},
                 "fallback_rule": "Per metric, the narrowest level with enough valid values is used "
                                  f"(minimums {thresholds()['peer_group']['min_valid_by_level']})."}
    pack = build_pack(res, dq, metrics, assessments, tech, risk, peer_info, snap, scen, pf)
    rb_raw, rb, bench_vol = rulebook_stage(providers, res, dq, factor_peers, emit)
    from backend.services.rulebook_service import add_evidence

    add_evidence(pack, rb)
    emit("evidence", "completed", f"Evidence pack built: {len(pack.items)} items with provenance")
    return {"metrics": metrics, "snapshot": snap, "assessments": assessments, "profile": profile, "peer": peer_info,
            "scores": {"fundamental_quality": fq, "valuation": vs, "technical": tscore, "risk": rscore},
            "technicals": tech, "risk": risk, "scenarios": scen, "portfolio": pf, "pack": pack,
            "rulebook": rb, "rulebook_raw": rb_raw, "bench_vol": bench_vol,
            "charts": {"price": chart, "financials": financial_chart_series(fd),
                       "valuation_history": [m.model_dump(mode="json") for m in metrics if m.key == "pe_historical"]}}
