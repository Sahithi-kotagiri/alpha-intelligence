"""Applies the team rulebook to one company (analysis) or to the whole S&P 500 (screen).

The same `factor_raw` is used for both, so a subject is ranked against sector peers measured identically.
"""

from __future__ import annotations

import logging
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, date, datetime
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.analysis import factors as F
from backend.database.models import ScreenRun, UniverseSnapshot

log = logging.getLogger(__name__)
_LABEL_CACHE: dict[tuple, dict[str, Any]] = {}


def _jsonable(x: Any) -> Any:
    if isinstance(x, dict):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, list | tuple):
        return [_jsonable(v) for v in x]
    if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
        return None
    if isinstance(x, date):
        return x.isoformat()
    return x


def factor_raw(providers, sym: str, sector: str | None, cik: int | None, info: dict[str, Any] | None = None,
               daily: pd.DataFrame | None = None, monthly: pd.DataFrame | None = None,
               facts: dict | None = None, filings: list | None = None) -> dict[str, Any]:
    """Fetch what the rulebook needs (reusing anything already fetched) and compute raw measures."""
    m = providers.market
    errors: list[str] = []

    def safe(name, fn, default=None):
        try:
            return fn()
        except Exception as exc:  # each input is optional; its absence is recorded per measure
            errors.append(f"{name}: {exc}")
            return default

    info = info if info is not None else safe("info", lambda: m.get_info(sym), {})
    if daily is None:
        daily = safe("daily prices", lambda: m.get_price_history(sym, period="3y"))
    if monthly is None:
        monthly = safe("monthly prices", lambda: m.get_prices_batch([sym], period="10y", interval="1mo").get(sym))
    if cik and facts is None and getattr(providers.sec, "configured", True):
        facts = safe("SEC facts", lambda: providers.sec.company_facts(cik))
    if cik and filings is None and getattr(providers.sec, "configured", True):
        filings = safe("SEC filings", lambda: providers.sec.recent_filings(cik, ("8-K", "8-K/A"), 60), [])
    eps_trend = safe("EPS trend", lambda: m.get_eps_trend(sym))
    eh = safe("earnings history", lambda: m.get_earnings_history(sym))
    raw = F.compute_measures(F.FactorInputs(sym, sector, info or {}, facts, daily, monthly, eps_trend, eh, filings or []))
    raw["input_errors"] = errors
    raw["computed_at"] = datetime.now(UTC).isoformat()
    return _jsonable(raw)


def run_factor_phase(session_factory, providers, run_id: int, rows: list[dict[str, Any]], workers: int = 4) -> None:
    syms = [r["symbol"] for r in rows]
    with session_factory() as db:
        run = db.get(ScreenRun, run_id)
        run.phase, run.rulebook_version = "prices", F.rulebook()["version"]
        db.commit()
    daily = providers.market.get_prices_batch(syms, period="3y", interval="1d")
    monthly = providers.market.get_prices_batch(syms, period="10y", interval="1mo")
    with session_factory() as db:
        infos = {s.ticker: s.info for s in db.scalars(select(UniverseSnapshot)) if s.status == "ok"}
        run = db.get(ScreenRun, run_id)
        run.phase = "factors"
        db.commit()

    def work(r):
        sym = r["symbol"]
        return sym, factor_raw(providers, sym, r.get("sector"), r.get("cik"), infos.get(sym), daily.get(sym), monthly.get(sym))

    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(work, r) for r in rows]
        for f in as_completed(futs):
            try:
                sym, raw = f.result()
            except Exception as exc:
                log.warning("factor computation failed: %s", exc)
                continue
            done += 1
            with session_factory() as db:
                snap = db.scalar(select(UniverseSnapshot).where(UniverseSnapshot.ticker == sym))
                if snap is None:
                    snap = UniverseSnapshot(ticker=sym, info={}, metrics={}, status="failed")
                    db.add(snap)
                snap.factors = raw
                if done % 20 == 0:
                    db.get(ScreenRun, run_id).phase = f"factors {done}/{len(rows)}"
                db.commit()
    _LABEL_CACHE.clear()


def universe_raws(db: Session) -> list[dict[str, Any]]:
    return [s.factors for s in db.scalars(select(UniverseSnapshot)) if s.factors]


def universe_labels(db: Session) -> dict[str, Any]:
    """Pillars + label for every screened constituent (cached per screen run and rulebook version)."""
    last = db.scalars(select(ScreenRun).order_by(ScreenRun.id.desc()).limit(1)).first()
    raws = universe_raws(db)
    key = (last.id if last else None, last.phase if last else None, len(raws), F.rulebook()["_sha256"])
    if key not in _LABEL_CACHE:
        rows = F.screen_universe_rows(raws)
        _LABEL_CACHE.clear()
        _LABEL_CACHE[key] = {r["ticker"]: r for r in rows}
    return _LABEL_CACHE[key]


def assess_subject(raw: dict[str, Any], peers: list[dict[str, Any]], dq_status: str, llm_used: bool,
                   extra_gates: list[dict[str, Any]] | None = None, bench_vol: float | None = None) -> dict[str, Any]:
    """Full rulebook verdict for one company: pillars, label, price ladder, size and confidence."""
    cfg = F.rulebook()
    universe = [p for p in peers if p.get("ticker") != raw["ticker"]] + [raw]
    pil = F.pillars(raw, universe if len(universe) > 1 else None)
    gates = list(extra_gates or [])
    if dq_status in ("poor", "critical"):
        gates.append({"id": "data_quality", "effect": "Watchlist",
                      "reason": f"data quality {dq_status}: no buy recommendation on unreliable data"})
    cls = F.classify(raw, pil, gates)
    d = raw.get("detail", {})
    vol = d.get("vol_1y")
    worst = d.get("max_drawdown_3y")
    size = F.position_size(cls["label"], vol, bench_vol, worst)
    conf, conf_why = F.confidence(dq_status, pil, llm_used)
    fair = raw.get("fair_value")
    bb = F.buy_below(fair, pil["quality"]["score"])
    reasons_for, reasons_against = [], []
    measures = [mm for p in pil.values() for mm in p["measures"] if mm.get("score") is not None]
    for mm in sorted(measures, key=lambda x: -x["score"])[:2]:
        if mm["score"] >= 60:
            reasons_for.append(mm)
    for mm in sorted(measures, key=lambda x: x["score"])[:2]:
        if mm["score"] <= 40:
            reasons_against.append(mm)
    change = []
    price = raw.get("price")
    if cls["label"] in ("Wait", "Watchlist") and bb:
        change.append(f"Price at or below the buy-below level of {bb:.2f}")
    for name, p in pil.items():
        if p["score"] < 50 and cls["label"] not in ("Strong Buy",):
            change.append(f"{name.title()} pillar recovering above 50 (now {p['score']:.0f})")
    for g in cls["gates"]:
        if g.get("binding"):
            change.append(f"Gate cleared: {g['reason'].rstrip('.')}")
    if cls["label"] in ("Strong Buy", "Buy"):
        change += ["Close below the 200-day average", "EPS estimates falling for 2 consecutive months",
                   "A thesis assumption moving to At risk"]
    return {
        "rulebook_version": cfg["version"], "label": cls["label"], "composite": cls["composite"],
        "pillars": pil, "trace": cls["trace"], "gates": cls["gates"],
        "price": price, "fair_value": round(fair, 2) if fair else None, "buy_below": bb,
        "price_vs_fair": round(cls["price_vs_fair"], 3) if cls["price_vs_fair"] else None,
        "fair_value_methods": d.get("fair_value_methods", []),
        "implied_growth": d.get("implied_growth"), "revenue_cagr": d.get("revenue_cagr"),
        "position_size": size, "confidence": conf, "confidence_reasons": conf_why,
        "reasons_for": reasons_for, "reasons_against": reasons_against, "what_would_change": change[:6],
        "peer_count": len(universe) - 1, "missing": raw.get("missing", {}), "detail": d,
        "review_triggers": ["Close below the 200-day average", "EPS estimates falling for 2 months",
                            "Any thesis assumption At risk"],
    }


SRC = "Rulebook factor engine (SEC XBRL filings, Yahoo prices and consensus estimates)"


def _fmt(v: Any, unit: str) -> str:
    if v is None:
        return "n/a"
    if unit == "pct":
        return f"{v * 100:.1f}%"
    if unit == "x":
        return f"{v:.2f}x"
    return f"{v:.2f}"


def add_evidence(pack, rb: dict[str, Any]) -> None:
    """Expose the rulebook result as citable evidence (category 'rulebook')."""
    from backend.analysis.evidence import EvidenceItem

    def add(eid, title, display, value=None, unit=None, detail=None):
        pack.add(EvidenceItem(id=eid, category="rulebook", title=title, display=display, value=value, unit=unit,
                              source_name=SRC, source_type="calculated", detail=detail or {}))

    add("rb.composite", "Rulebook composite (0–100, mean of 4 pillars)", f"{rb['composite']:.1f}", rb["composite"],
        detail={"label_before_committee": rb["label"], "trace": rb["trace"]})
    for name, p in rb["pillars"].items():
        add(f"rb.pillar.{name}", f"{name.title()} pillar (sector percentile, 0–100)",
            f"{p['score']:.1f}" + (" (over half of measures missing: set to 50)" if p["sparse"] else ""), p["score"],
            detail={"measures": p["measures"]})
        for mm in p["measures"]:
            if mm.get("value") is None:
                add(f"rb.m.{mm['key']}", mm["label"], f"unavailable — {mm.get('reason')}")
            else:
                add(f"rb.m.{mm['key']}", mm["label"],
                    f"{_fmt(mm['value'], mm['unit'])} → score {mm['score']:.0f}/100 ({mm['basis']}, n={mm['n']})",
                    mm["value"], mm["unit"])
    if rb.get("fair_value"):
        add("rb.fair_value", "Fair value per share (mean of DCF and own-history P/E)", f"${rb['fair_value']:,.2f}",
            rb["fair_value"], "USD/share", {"methods": rb["fair_value_methods"]})
        add("rb.buy_below", "Buy-below price (fair value less margin of safety)", f"${rb['buy_below']:,.2f}",
            rb["buy_below"], "USD/share")
    else:
        add("rb.fair_value", "Fair value per share", "not computable (no positive FCF/EPS history)")
    if rb.get("price_vs_fair"):
        add("rb.price_vs_fair", "Price ÷ fair value", f"{rb['price_vs_fair']:.2f}", rb["price_vs_fair"], "x")
    if rb.get("implied_growth") is not None:
        add("rb.implied_growth", "Growth implied by today's price (reverse DCF, 10y)", _fmt(rb["implied_growth"], "pct"),
            rb["implied_growth"], "pct")
    if rb.get("revenue_cagr") is not None:
        add("rb.revenue_cagr", "Achieved revenue growth (CAGR, up to 5 years)", _fmt(rb["revenue_cagr"], "pct"),
            rb["revenue_cagr"], "pct")
    for g in rb["gates"]:
        add(f"rb.gate.{g['id']}", f"Gate: {g['id'].replace('_', ' ')}", f"caps label at {g['effect']}: {g['reason']}")
