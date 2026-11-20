"""Thesis monitoring: re-test stored assumptions against fresh evidence, record events, raise alerts,
and trigger a versioned reassessment only when a change is material.

Material triggers (METHODOLOGY §9): an assumption moving to At risk / Invalidated, a new
financial reporting period, a high-materiality 8-K item, or a price move > 15% from the
reference price. Small moves inside the review band never trigger anything. A
reassessment for the same ticker is not started more than once every 3 days.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select

from backend.data.ingestion import EIGHT_K_ITEMS
from backend.database import models as M
from backend.intelligence.thesis import evaluate, overall
from backend.tools.financial_metrics import FundamentalCalculator, latest_metric
from backend.tools.technical_indicators import compute_technicals
from backend.tools.valuation import compute_valuation, market_snapshot

log = logging.getLogger(__name__)
PRICE_MOVE_TRIGGER = 0.15
REASSESS_COOLDOWN_DAYS = 3
SEVERITY = {"Review": "medium", "At risk": "high", "Invalidated": "high", "Improving": "low"}


def current_values(providers, ticker: str, cik: int | None) -> dict[str, Any]:
    """Fresh metric values for monitored keys (same deterministic code as the analysis)."""
    from backend.data.normalisation import build_financials

    md = providers.market
    out: dict[str, Any] = {"metrics": {}, "errors": []}
    try:
        prices = md.get_price_history(ticker)
        out["price"], out["price_date"] = float(prices["Close"].iloc[-1]), prices.index[-1].date()
        out["technicals"] = compute_technicals(prices, None)
    except Exception as exc:
        prices = None
        out["errors"].append(f"prices: {exc}")
    try:
        statements = md.get_statements(ticker)
        facts = providers.sec.company_facts(cik) if cik else None
        info = md.get_info(ticker)
        fd = build_financials(ticker, statements, facts, cik, info.get("financialCurrency"))
        metrics = FundamentalCalculator(fd).compute()
        snap = market_snapshot(prices, info, fd)
        metrics += compute_valuation(fd, snap, metrics, md.get_estimates(ticker))
        out["metrics"] = {m.key: m for m in metrics}
        out["all_metrics"] = metrics
        rev = fd.latest("revenue", "quarterly") or fd.latest("revenue", "annual")
        out["latest_period_end"] = rev.period_end if rev else None
        out["latest_period"] = rev.fiscal_period if rev else None
    except Exception as exc:
        out["errors"].append(f"financials: {exc}")
    if cik:
        try:
            out["filings"] = providers.sec.recent_filings(cik, forms=("8-K", "8-K/A", "10-Q", "10-K"), limit=30)
        except Exception as exc:
            out["errors"].append(f"filings: {exc}")
    return out


def _event(db, thesis, ticker, etype, title, materiality, dedupe, detail=None, assumption=None, before=None, after=None,
           url=None) -> M.ThesisEvent | None:
    if db.scalar(select(M.ThesisEvent).where(M.ThesisEvent.thesis_id == thesis.id, M.ThesisEvent.dedupe_key == dedupe)):
        return None
    ev = M.ThesisEvent(thesis_id=thesis.id, assumption_id=assumption.id if assumption else None, ticker=ticker, event_type=etype,
                       title=title, detail=detail or {}, materiality=materiality, status_before=before, status_after=after,
                       source_url=url, dedupe_key=dedupe)
    db.add(ev)
    db.flush()
    return ev


PREF_FOR_KIND = {"thesis_change": "thesis", "filing": "filings", "price": "price", "earnings": "earnings", "price_target": "price"}


def _alert(db, user_id, ticker, kind, severity, title, message, detail, ev=None):
    """Create an alert unless the user's watchlist preferences for this ticker switch that alert type off."""
    pref = PREF_FOR_KIND.get(kind)
    if pref and ticker:
        w = db.scalar(select(M.WatchlistItem).where(M.WatchlistItem.user_id == user_id, M.WatchlistItem.ticker == ticker))
        if w is not None and (w.alert_prefs or {}).get(pref) is False:
            return
    db.add(M.Alert(user_id=user_id, ticker=ticker, kind=kind, severity=severity, title=title, message=message,
                   detail=detail, thesis_event_id=ev.id if ev else None))


def check_thesis(db, providers, thesis: M.InvestmentThesis, fresh: dict[str, Any] | None = None) -> dict[str, Any]:
    run = db.get(M.AnalysisRun, thesis.analysis_id)
    ident = (run.result or {}).get("identity", {}) if run else {}
    fresh = fresh or current_values(providers, thesis.ticker, ident.get("cik"))
    events: list[M.ThesisEvent] = []
    material = False
    reasons: list[str] = []
    statuses = []
    for a in thesis.assumptions:
        before = a.status
        if a.kind != "quantitative":
            statuses.append(a.status)
            continue
        cur = None
        evidence = None
        if a.metric_key == "dist_sma_200":
            cur = (fresh.get("technicals") or {}).get("dist_sma_200")
            evidence = f"price {fresh.get('price')} on {fresh.get('price_date')}"
        else:
            m = latest_metric(fresh.get("all_metrics", []), a.metric_key) if fresh.get("all_metrics") else None
            if m is not None and m.ok:
                cur, evidence = m.value, f"{m.label} {m.period}: {m.formula}"
        new = evaluate(a.comparator, a.original_value, a.threshold, a.review_threshold, cur)
        a.current_value = cur
        a.last_checked_at = datetime.now(UTC)
        if new != before:
            a.status = new
            ev = _event(db, thesis, thesis.ticker, "assumption_status_change",
                        f"{a.description}: {before} → {new}", SEVERITY.get(new, "low"),
                        f"assumption:{a.id}:{new}:{fresh.get('latest_period_end') or fresh.get('price_date')}",
                        {"original": a.original_value, "current": cur, "threshold": a.threshold, "review": a.review_threshold,
                         "evidence": evidence, "comparator": a.comparator}, a, before, new)
            if ev:
                events.append(ev)
                _alert(db, thesis.user_id, thesis.ticker, "thesis_change", SEVERITY.get(new, "low"),
                       f"{thesis.ticker}: thesis assumption now {new}",
                       f"{a.description}. Original {a.original_value}, now {cur} (review {a.review_threshold}, at-risk {a.threshold}).",
                       {"assumption_id": a.id}, ev)
            if new in ("At risk", "Invalidated"):
                material = True
                reasons.append(f"assumption '{a.key}' {new}")
        statuses.append(a.status)
    # new financial period
    orig_period = ((run.result or {}).get("data_through") or {}).get("financials") if run else None
    lp, lpe = fresh.get("latest_period"), fresh.get("latest_period_end")
    if lp and orig_period and lp not in str(orig_period):
        ev = _event(db, thesis, thesis.ticker, "new_financial_period", f"New financial period reported: {lp}", "medium",
                    f"period:{lpe}", {"previous": orig_period, "new": lp})
        if ev:
            events.append(ev)
            material = True
            reasons.append(f"new period {lp}")
            _alert(db, thesis.user_id, thesis.ticker, "earnings", "medium", f"{thesis.ticker}: new results ({lp})",
                   "New quarterly/annual figures are available; thesis assumptions were re-tested.", {}, ev)
    # new filings since thesis creation
    created = thesis.created_at.date() if thesis.created_at else date.today()
    for f in fresh.get("filings", []):
        try:
            fdate = date.fromisoformat(f.get("filingDate"))
        except (TypeError, ValueError):
            continue
        if fdate < created:
            continue
        items = [s.strip() for s in str(f.get("items") or "").split(",") if s.strip()]
        mats = [EIGHT_K_ITEMS.get(i, ("Other", "low")) for i in items]
        mat = "high" if any(m[1] == "high" for m in mats) else "medium" if any(m[1] == "medium" for m in mats) else "low"
        if f.get("form", "").startswith("8-K") and mat in ("high", "medium"):
            ev = _event(db, thesis, thesis.ticker, "sec_filing", f"SEC {f['form']} {fdate}: {', '.join(m[0] for m in mats)}",
                        mat, f"filing:{f['accessionNumber']}", {"items": items, "form": f["form"]}, url=f.get("url"))
            if ev:
                events.append(ev)
                _alert(db, thesis.user_id, thesis.ticker, "filing", mat, f"{thesis.ticker}: {f['form']} filed {fdate}",
                       ", ".join(m[0] for m in mats), {"url": f.get("url")}, ev)
                if mat == "high":
                    material = True
                    reasons.append(f"high-materiality 8-K {', '.join(items)}")
    # price move
    ref = run.reference_price if run else None
    if ref and fresh.get("price"):
        move = fresh["price"] / ref - 1
        if abs(move) >= PRICE_MOVE_TRIGGER:
            wk = fresh["price_date"].isocalendar()
            ev = _event(db, thesis, thesis.ticker, "price_move", f"Price moved {move:+.1%} since the decision", "medium",
                        f"price:{wk[0]}-{wk[1]}:{'up' if move > 0 else 'down'}", {"reference": ref, "current": fresh["price"]})
            if ev:
                events.append(ev)
                material = True
                reasons.append(f"price move {move:+.1%}")
                _alert(db, thesis.user_id, thesis.ticker, "price", "medium", f"{thesis.ticker}: {move:+.1%} since decision",
                       f"Reference ${ref:,.2f} → ${fresh['price']:,.2f}.", {}, ev)
    thesis.overall_status = overall(statuses)
    thesis.last_checked_at = datetime.now(UTC)
    return {"events": events, "material": material, "reasons": reasons, "errors": fresh.get("errors", [])}


def run_monitoring(session_factory, providers, runner=None, user_id: int | None = None) -> dict[str, Any]:
    from backend.services.analysis_service import create_analysis

    with session_factory() as db:
        mr = M.MonitoringRun(kind="thesis")
        db.add(mr)
        db.commit()
        q = select(M.InvestmentThesis).where(M.InvestmentThesis.status == "active")
        if user_id is not None:
            q = q.where(M.InvestmentThesis.user_id == user_id)
        theses = db.scalars(q).all()
        cache: dict[str, Any] = {}
        n_events = 0
        triggered = []
        errors = []
        for th in theses:
            try:
                run = db.get(M.AnalysisRun, th.analysis_id)
                cik = ((run.result or {}).get("identity") or {}).get("cik") if run else None
                if th.ticker not in cache:
                    cache[th.ticker] = current_values(providers, th.ticker, cik)
                out = check_thesis(db, providers, th, cache[th.ticker])
                n_events += len(out["events"])
                errors += out["errors"]
                if out["material"] and runner is not None:
                    recent = db.scalar(select(M.AnalysisRun).where(
                        M.AnalysisRun.user_id == th.user_id, M.AnalysisRun.ticker == th.ticker, M.AnalysisRun.trigger == "monitoring",
                        M.AnalysisRun.created_at >= datetime.now(UTC) - timedelta(days=REASSESS_COOLDOWN_DAYS)))
                    if recent is None:
                        params = dict((run.params or {}) if run else {})
                        params.pop("prior", None)
                        params["reassessment_reasons"] = out["reasons"]
                        new = create_analysis(db, th.user_id, th.ticker, params, trigger="monitoring", parent_id=th.analysis_id)
                        for ev in out["events"]:
                            ev.reassessment_analysis_id = new.id
                        triggered.append(new.id)
                db.commit()
            except Exception as exc:
                log.exception("monitoring failed for %s", th.ticker)
                errors.append(f"{th.ticker}: {exc}")
                db.rollback()
        # watchlist price targets
        wq = select(M.WatchlistItem).where(M.WatchlistItem.target_price.is_not(None))
        if user_id is not None:
            wq = wq.where(M.WatchlistItem.user_id == user_id)
        for w in db.scalars(wq):
            try:
                px = providers.market.get_price_history(w.ticker)
                last, d = float(px["Close"].iloc[-1]), px.index[-1].date()
                if last <= w.target_price:
                    key = f"target:{w.ticker}:{w.target_price}:{d.isocalendar()[1]}"
                    if not db.scalar(select(M.Alert).where(M.Alert.user_id == w.user_id, M.Alert.kind == "price_target",
                                                           M.Alert.ticker == w.ticker, M.Alert.message.contains(str(w.target_price)))):
                        _alert(db, w.user_id, w.ticker, "price_target", "medium", f"{w.ticker} at or below target",
                               f"Close ${last:,.2f} on {d} ≤ target {w.target_price}.", {"dedupe": key})
            except Exception as exc:
                errors.append(f"watchlist {w.ticker}: {exc}")
        mr = db.get(M.MonitoringRun, mr.id)
        mr.checked, mr.events_created, mr.completed_at = len(theses), n_events, datetime.now(UTC)
        mr.status = "completed" if not errors else "completed_with_errors"
        mr.error = "\n".join(errors)[:4000] or None
        db.commit()
    if runner is not None:
        for t in triggered:
            runner.submit(t)
    return {"checked": len(theses), "events": n_events, "reassessments": triggered, "errors": errors}
