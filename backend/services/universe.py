"""S&P 500 universe: refresh, change detection, search, identity resolution and screening."""

from __future__ import annotations

import logging
import statistics
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.analysis.config_loader import thresholds
from backend.analysis.peers import oriented_percentile, vendor_value
from backend.data.ingestion import CompanyIdentity
from backend.data.providers.base import NotFoundError, ProviderError
from backend.database.models import ScreenRun, UniverseChange, UniverseMember, UniverseSnapshot

log = logging.getLogger(__name__)

SCREEN_FIELDS = ["trailingPE", "forwardPE", "trailingPegRatio", "debtToEquity", "enterpriseToEbitda",
                 "priceToSalesTrailing12Months", "priceToBook", "grossMargins", "operatingMargins", "profitMargins",
                 "returnOnEquity", "revenueGrowth", "earningsGrowth", "currentRatio", "freeCashflow", "marketCap", "beta",
                 "currentPrice", "fiftyTwoWeekHigh", "fiftyTwoWeekLow", "fiftyDayAverage", "twoHundredDayAverage",
                 "dividendYield", "longName", "currency", "financialCurrency", "quoteType", "exchange", "sector", "industry",
                 "recommendationMean", "numberOfAnalystOpinions", "targetMeanPrice", "averageVolume", "heldPercentInsiders",
                 "overallRisk", "auditRisk", "boardRisk", "compensationRisk", "shareHolderRightsRisk", "fullTimeEmployees",
                 "country", "website", "longBusinessSummary", "enterpriseValue", "forwardEps", "trailingEps", "pegRatio",
                 "sharesOutstanding", "regularMarketTime"]


def refresh_universe(db: Session, providers, force: bool = False) -> dict[str, Any]:
    df, retrieved = providers.universe.constituents(force=force)
    existing = {m.ticker: m for m in db.scalars(select(UniverseMember).where(UniverseMember.universe == "SP500"))}
    new_syms = set(df["symbol"])
    added, removed = [], []
    first_load = not existing
    for r in df.to_dict("records"):
        m = existing.get(r["symbol"])
        cik = int(r["cik"]) if r.get("cik") == r.get("cik") and r.get("cik") is not None else None
        if m is None:
            m = UniverseMember(universe="SP500", ticker=r["symbol"])
            db.add(m)
            if not first_load:
                added.append(r["symbol"])
        elif not m.active:
            m.active, m.removed_at = True, None
            added.append(r["symbol"])
        m.name, m.sector, m.sub_industry, m.cik = r["name"], r["sector"], r["sub_industry"], cik
        m.headquarters = str(r.get("headquarters") or "")[:160] or None
        m.date_added = str(r.get("date_added") or "")[:20] or None
        m.retrieved_at, m.source_url = retrieved, providers.universe.url
    for t, m in existing.items():
        if t not in new_syms and m.active:
            m.active, m.removed_at = False, datetime.now(UTC)
            removed.append(t)
    for t in added:
        db.add(UniverseChange(universe="SP500", ticker=t, change="added"))
    for t in removed:
        db.add(UniverseChange(universe="SP500", ticker=t, change="removed"))
    db.commit()
    return {"count": len(new_syms), "retrieved_at": retrieved.isoformat(), "added": added, "removed": removed,
            "source": providers.universe.name, "source_url": providers.universe.url}


def universe_rows(db: Session) -> list[dict[str, Any]]:
    rows = db.scalars(select(UniverseMember).where(UniverseMember.universe == "SP500", UniverseMember.active.is_(True))
                      .order_by(UniverseMember.ticker)).all()
    return [{"symbol": r.ticker, "name": r.name, "sector": r.sector, "sub_industry": r.sub_industry, "cik": r.cik,
             "date_added": r.date_added, "headquarters": r.headquarters,
             "retrieved_at": r.retrieved_at.isoformat() if r.retrieved_at else None} for r in rows]


def ensure_universe(db: Session, providers) -> list[dict[str, Any]]:
    rows = universe_rows(db)
    if not rows:
        try:
            refresh_universe(db, providers)
        except ProviderError as exc:
            log.warning("universe refresh failed: %s", exc)
            return []
        rows = universe_rows(db)
    return rows


def search(db: Session, providers, q: str, limit: int = 20) -> list[dict[str, Any]]:
    q = q.strip()
    if not q:
        return []
    qu, ql = q.upper(), q.lower()
    rows = ensure_universe(db, providers)
    exact = [r for r in rows if r["symbol"] == qu]
    prefix = [r for r in rows if r["symbol"].startswith(qu) and r not in exact]
    name = [r for r in rows if ql in r["name"].lower() and r not in exact and r not in prefix]
    out = [r | {"in_sp500": True} for r in (exact + prefix + name)][:limit]
    if not exact:
        try:
            tm = providers.sec.ticker_map()
            if qu in tm:
                out.insert(0, {"symbol": qu, "name": tm[qu]["name"], "sector": None, "sub_industry": None,
                               "cik": tm[qu]["cik"], "in_sp500": False, "exchange": tm[qu].get("exchange")})
        except Exception as exc:
            log.info("SEC ticker map unavailable for search: %s", exc)
    return out


def resolve(db: Session, providers, ticker: str) -> dict[str, Any]:
    """Resolve a ticker to a confirmed identity, or return candidates when ambiguous."""
    t = ticker.strip().upper()
    rows = ensure_universe(db, providers)
    member = next((r for r in rows if r["symbol"] == t), None)
    sec_rec = None
    try:
        sec_rec = providers.sec.ticker_map().get(t)
    except Exception as exc:
        log.info("SEC ticker map unavailable: %s", exc)
    if member is None and sec_rec is None:
        cands = search(db, providers, ticker, 8)
        return {"resolved": False, "needs_confirmation": True, "candidates": cands,
                "message": f"'{ticker}' is not an S&P 500 constituent or SEC-registered ticker. Select a company."}
    try:
        info = providers.market.get_info(t)
    except (NotFoundError, ProviderError) as exc:
        info = {}
        log.info("vendor info unavailable for %s: %s", t, exc)
    ident = CompanyIdentity(
        ticker=t, name=(member or {}).get("name") or (sec_rec or {}).get("name") or info.get("longName") or t,
        exchange=info.get("exchange") or (sec_rec or {}).get("exchange"), currency=info.get("currency"),
        financial_currency=info.get("financialCurrency"), security_type=info.get("quoteType"),
        cik=(member or {}).get("cik") or (sec_rec or {}).get("cik"), sector=(member or {}).get("sector"),
        sub_industry=(member or {}).get("sub_industry"), vendor_sector=info.get("sector"),
        vendor_industry=info.get("industry"), in_sp500=member is not None, website=info.get("website"),
        description=info.get("longBusinessSummary"), country=info.get("country"), employees=info.get("fullTimeEmployees"))
    warnings = []
    if not member:
        warnings.append("Not an S&P 500 constituent: GICS classification unavailable; peer comparison uses the whole universe.")
    if ident.security_type and ident.security_type != "EQUITY":
        warnings.append(f"Security type is {ident.security_type}; analysis is designed for common equity.")
    if not info:
        warnings.append("Vendor profile unavailable; identity based on index/SEC records only.")
    return {"resolved": True, "needs_confirmation": False, "identity": ident.__dict__, "warnings": warnings}


def snapshot_infos(db: Session, max_age_hours: float = 72) -> dict[str, dict[str, Any]]:
    cutoff = datetime.now(UTC).timestamp() - max_age_hours * 3600
    out = {}
    for s in db.scalars(select(UniverseSnapshot).where(UniverseSnapshot.status == "ok")):
        ts = s.retrieved_at
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        if ts.timestamp() >= cutoff:
            out[s.ticker] = s.info
    return out


def run_screen(session_factory, providers, run_id: int, workers: int = 4) -> None:
    """Fetch vendor metrics for every constituent (throttled) and store snapshots."""
    with session_factory() as db:
        run = db.get(ScreenRun, run_id)
        rows = universe_rows(db)
        run.status, run.started_at, run.total = "running", datetime.now(UTC), len(rows)
        db.commit()

    def fetch(sym):
        try:
            info = providers.market.get_info(sym)
            return sym, {k: info.get(k) for k in SCREEN_FIELDS}, None
        except Exception as exc:
            return sym, None, str(exc)

    ok = fail = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(fetch, r["symbol"]) for r in rows]
        for i, f in enumerate(as_completed(futs)):
            sym, info, err = f.result()
            with session_factory() as db:
                snap = db.scalar(select(UniverseSnapshot).where(UniverseSnapshot.ticker == sym))
                if snap is None:
                    snap = UniverseSnapshot(ticker=sym, info={}, metrics={})
                    db.add(snap)
                snap.screen_run_id = run_id
                snap.retrieved_at = datetime.now(UTC)
                if info:
                    snap.info, snap.status, snap.error = info, "ok", None
                    snap.metrics = {k: vendor_value(info, spec) for k, spec in thresholds()["peer_metrics"].items()}
                    ok += 1
                else:
                    snap.status, snap.error = "failed", err
                    fail += 1
                if i % 10 == 0 or i == len(futs) - 1:
                    run = db.get(ScreenRun, run_id)
                    run.succeeded, run.failed = ok, fail
                db.commit()
    with session_factory() as db:
        run = db.get(ScreenRun, run_id)
        run.status, run.completed_at, run.succeeded, run.failed = "completed", datetime.now(UTC), ok, fail
        db.commit()


def screener_table(db: Session) -> dict[str, Any]:
    """Universe table with sector-relative percentiles from the latest snapshot."""
    rows = universe_rows(db)
    snaps = {s.ticker: s for s in db.scalars(select(UniverseSnapshot))}
    cfg = thresholds()
    by_sector: dict[str, dict[str, list[float]]] = {}
    for r in rows:
        s = snaps.get(r["symbol"])
        if not s or s.status != "ok":
            continue
        for k, v in (s.metrics or {}).items():
            lo, hi = cfg["peer_group"]["valid_ranges"].get(k, [-1e18, 1e18])
            if v is not None and lo <= v <= hi:
                by_sector.setdefault(r["sector"], {}).setdefault(k, []).append(v)
    out = []
    for r in rows:
        s = snaps.get(r["symbol"])
        rec = dict(r)
        rec["snapshot_status"] = s.status if s else "not_screened"
        rec["retrieved_at"] = s.retrieved_at.isoformat() if s else None
        if s and s.status == "ok":
            m = s.metrics or {}
            rec["metrics"] = m
            pct = {}
            for k, v in m.items():
                dist = by_sector.get(r["sector"], {}).get(k, [])
                if v is not None and len(dist) >= 5:
                    pct[k] = round(oriented_percentile(v, dist, cfg["peer_metrics"][k]["better"]), 3)
            rec["sector_percentiles"] = pct
            info = s.info or {}
            rec["price"] = info.get("currentPrice")
            rec["market_cap"] = info.get("marketCap")
            ma200 = info.get("twoHundredDayAverage")
            rec["above_200d"] = (info.get("currentPrice") > ma200) if (info.get("currentPrice") and ma200) else None
            val_keys = [k for k in ("trailing_pe", "forward_pe", "ev_to_ebitda", "price_to_fcf", "peg_forward") if k in pct]
            qual_keys = [k for k in ("operating_margin_ttm", "roe_ttm", "revenue_yoy_quarterly", "gross_margin_ttm") if k in pct]
            rec["value_rank"] = round(statistics.mean(pct[k] for k in val_keys), 3) if len(val_keys) >= 2 else None
            rec["quality_rank"] = round(statistics.mean(pct[k] for k in qual_keys), 3) if len(qual_keys) >= 2 else None
        out.append(rec)
    last = db.scalars(select(ScreenRun).order_by(ScreenRun.id.desc()).limit(1)).first()
    return {"rows": out, "count": len(out), "screen_run": None if last is None else
            {"id": last.id, "status": last.status, "total": last.total, "succeeded": last.succeeded, "failed": last.failed,
             "started_at": last.started_at.isoformat() if last.started_at else None,
             "completed_at": last.completed_at.isoformat() if last.completed_at else None},
            "method": "Vendor-reported metrics (Yahoo); percentile = share of same-sector constituents the company is "
                      "better than (lower is better for valuation multiples). value_rank/quality_rank = mean of available "
                      "percentiles; screening ranks are not recommendations."}
