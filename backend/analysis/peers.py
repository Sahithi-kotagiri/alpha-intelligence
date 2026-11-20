"""Peer groups and peer metric distributions from the S&P 500 universe.

Peer metrics are vendor-reported (Yahoo `info`) values so that every peer is measured
the same way. The subject company's percentile is computed on the *same vendor
definition* when available (apples-to-apples), and the company's own statement-based
calculation is shown alongside with a consistency note.
"""

from __future__ import annotations

import logging
import statistics
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from backend.analysis.config_loader import thresholds

log = logging.getLogger(__name__)


class PeerGroup(BaseModel):
    level: str  # sub_industry | sector | universe | none
    name: str
    tickers: list[str]
    with_data: list[str] = Field(default_factory=list)
    retrieved_at: datetime | None = None
    source: str = "Yahoo Finance via yfinance (vendor-reported metrics)"


class PeerStats(BaseModel):
    metric: str
    n: int
    median: float | None
    p25: float | None
    p75: float | None
    values: dict[str, float] = Field(default_factory=dict)


def vendor_value(info: dict[str, Any], spec: dict[str, Any]) -> float | None:
    key = spec["vendor"]
    try:
        if key == "_price_to_fcf":
            mc, f = info.get("marketCap"), info.get("freeCashflow")
            return mc / f if mc and f and f > 0 else None
        if key == "_fcf_margin":
            r, f = info.get("totalRevenue"), info.get("freeCashflow")
            return f / r if r and r > 0 and f is not None else None
        if key == "_fcf_yield":
            mc, f = info.get("marketCap"), info.get("freeCashflow")
            return f / mc if mc and f is not None else None
        v = info.get(key)
        if v is None:
            return None
        v = float(v) * float(spec.get("scale", 1.0))
        return None if v != v else v
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def peer_levels(ticker: str, sector: str | None, sub_industry: str | None,
                universe: list[dict[str, Any]]) -> dict[str, PeerGroup]:
    """All candidate peer levels; the assessor picks per metric the narrowest level with enough valid data."""
    t = ticker.upper()
    out: dict[str, PeerGroup] = {}
    if sub_industry:
        sub = [u["symbol"] for u in universe if u.get("sub_industry") == sub_industry and u["symbol"] != t]
        if sub:
            out["sub_industry"] = PeerGroup(level="sub_industry", name=sub_industry, tickers=sub)
    if sector:
        sec = [u["symbol"] for u in universe if u.get("sector") == sector and u["symbol"] != t]
        if sec:
            out["sector"] = PeerGroup(level="sector", name=sector, tickers=sec)
    allu = [u["symbol"] for u in universe if u["symbol"] != t]
    if allu:
        out["universe"] = PeerGroup(level="universe", name="S&P 500", tickers=allu)
    return out


def choose_peer_group(ticker: str, sector: str | None, sub_industry: str | None, universe: list[dict[str, Any]]) -> PeerGroup:
    cfg = thresholds()["peer_group"]
    t = ticker.upper()
    sub = [u["symbol"] for u in universe if u.get("sub_industry") == sub_industry and u["symbol"] != t]
    if sub_industry and len(sub) >= cfg["min_sub_industry_peers"]:
        return PeerGroup(level="sub_industry", name=sub_industry, tickers=sub)
    sec = [u["symbol"] for u in universe if u.get("sector") == sector and u["symbol"] != t]
    if sector and len(sec) >= cfg["min_sector_peers"]:
        return PeerGroup(level="sector", name=sector, tickers=sec)
    allu = [u["symbol"] for u in universe if u["symbol"] != t]
    if allu:
        return PeerGroup(level="universe", name="S&P 500", tickers=allu)
    return PeerGroup(level="none", name="no peer universe available", tickers=[])


def fetch_peer_infos(market, tickers: list[str], snapshot: dict[str, dict[str, Any]] | None = None,
                     workers: int = 6, limit: int | None = None) -> dict[str, dict[str, Any]]:
    """Return {ticker: info}; uses universe-screen snapshot first, then cached/live vendor calls."""
    out: dict[str, dict[str, Any]] = {}
    todo = []
    for t in tickers:
        if snapshot and t in snapshot and snapshot[t]:
            out[t] = snapshot[t]
        else:
            todo.append(t)
    if limit is not None:
        todo = todo[:limit]

    def get(t):
        try:
            return t, market.get_info(t)
        except Exception as exc:
            log.info("peer info unavailable for %s: %s", t, exc)
            return t, None

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for t, info in ex.map(get, todo):
            if info:
                out[t] = info
    return out


def peer_stats(peer_infos: dict[str, dict[str, Any]]) -> dict[str, PeerStats]:
    cfg = thresholds()
    ranges = cfg["peer_group"]["valid_ranges"]
    stats: dict[str, PeerStats] = {}
    for key, spec in cfg["peer_metrics"].items():
        vals: dict[str, float] = {}
        for t, info in peer_infos.items():
            v = vendor_value(info, spec)
            if v is None:
                continue
            lo, hi = ranges.get(key, [-1e18, 1e18])
            if lo <= v <= hi:
                vals[t] = v
        xs = sorted(vals.values())
        if len(xs) >= 3:
            q = statistics.quantiles(xs, n=4, method="inclusive")
            stats[key] = PeerStats(metric=key, n=len(xs), median=statistics.median(xs), p25=q[0], p75=q[2], values=vals)
        else:
            stats[key] = PeerStats(metric=key, n=len(xs), median=None, p25=None, p75=None, values=vals)
    return stats


def oriented_percentile(value: float, values: list[float], better: str) -> float:
    """Share of peers the company is better than (0..1), ties count half."""
    if not values:
        return 0.5
    if better == "lower":
        better_than = sum(1 for v in values if value < v) + 0.5 * sum(1 for v in values if v == value)
    else:
        better_than = sum(1 for v in values if value > v) + 0.5 * sum(1 for v in values if v == value)
    return better_than / len(values)


def now() -> datetime:
    return datetime.now(UTC)
