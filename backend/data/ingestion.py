"""Controlled data retrieval for one company.

Agents never fetch data themselves. This module is the single place where retrieval
happens; every attempt is recorded as a `SourceFetch` (ok / not_found / failed /
skipped) so the data-quality report can distinguish provider failure from absent data.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pandas as pd

from backend.data.normalisation import FinancialData, build_financials
from backend.data.providers.base import NewsItem, NotFoundError, ProviderError
from backend.data.registry import Providers

log = logging.getLogger(__name__)

SECTOR_ETFS = {
    "Information Technology": "XLK", "Financials": "XLF", "Health Care": "XLV",
    "Consumer Discretionary": "XLY", "Consumer Staples": "XLP", "Energy": "XLE", "Industrials": "XLI",
    "Materials": "XLB", "Utilities": "XLU", "Real Estate": "XLRE", "Communication Services": "XLC",
}

# SEC 8-K item codes -> description and default materiality (documented in METHODOLOGY §6)
EIGHT_K_ITEMS = {
    "1.01": ("Entry into a material definitive agreement", "medium"),
    "1.02": ("Termination of a material definitive agreement", "medium"),
    "1.03": ("Bankruptcy or receivership", "high"),
    "1.05": ("Material cybersecurity incident", "high"),
    "2.01": ("Completion of acquisition or disposition of assets", "medium"),
    "2.02": ("Results of operations and financial condition", "medium"),
    "2.03": ("Creation of a direct financial obligation", "medium"),
    "2.04": ("Triggering events accelerating a financial obligation", "high"),
    "2.05": ("Costs associated with exit or disposal activities", "medium"),
    "2.06": ("Material impairments", "high"),
    "3.01": ("Notice of delisting or failure to satisfy listing rule", "high"),
    "3.02": ("Unregistered sales of equity securities", "medium"),
    "3.03": ("Material modification to rights of security holders", "medium"),
    "4.01": ("Change in registrant's certifying accountant", "high"),
    "4.02": ("Non-reliance on previously issued financial statements", "high"),
    "5.01": ("Change in control of registrant", "high"),
    "5.02": ("Departure/appointment of directors or officers", "medium"),
    "5.03": ("Amendments to articles of incorporation or bylaws", "low"),
    "5.07": ("Submission of matters to a vote of security holders", "low"),
    "7.01": ("Regulation FD disclosure", "low"),
    "8.01": ("Other events", "low"),
    "9.01": ("Financial statements and exhibits", "low"),
}


@dataclass
class SourceFetch:
    source: str
    item: str
    status: str  # ok | not_found | failed | skipped
    retrieved_at: datetime | None = None
    url: str | None = None
    error: str | None = None
    detail: str | None = None


@dataclass
class CompanyIdentity:
    ticker: str
    name: str
    exchange: str | None = None
    currency: str | None = None
    financial_currency: str | None = None
    security_type: str | None = None
    cik: int | None = None
    sector: str | None = None
    sub_industry: str | None = None
    vendor_sector: str | None = None
    vendor_industry: str | None = None
    in_sp500: bool = False
    fiscal_year_end_month: int | None = None
    website: str | None = None
    description: str | None = None
    country: str | None = None
    employees: int | None = None


@dataclass
class IngestionResult:
    identity: CompanyIdentity
    info: dict[str, Any] = field(default_factory=dict)
    prices: pd.DataFrame | None = None
    benchmark_prices: pd.DataFrame | None = None
    sector_prices: pd.DataFrame | None = None
    benchmark: str = "SPY"
    sector_etf: str | None = None
    financials: FinancialData | None = None
    news: list[NewsItem] = field(default_factory=list)
    filings: list[dict[str, Any]] = field(default_factory=list)
    calendar: dict[str, Any] = field(default_factory=dict)
    estimates: dict[str, Any] = field(default_factory=dict)
    analyst_actions: pd.DataFrame | None = None
    earnings_history: pd.DataFrame | None = None
    fetches: list[SourceFetch] = field(default_factory=list)
    retrieved_at: datetime = field(default_factory=lambda: datetime.now(UTC))


Emit = Callable[[str, str, str], None]  # (stage, status, message)


def _noop(stage: str, status: str, message: str) -> None:
    return None


def _attempt(res: IngestionResult, source: str, item: str, fn, url: str | None = None):
    try:
        val = fn()
        res.fetches.append(SourceFetch(source, item, "ok", datetime.now(UTC), url))
        return val
    except NotFoundError as exc:
        res.fetches.append(SourceFetch(source, item, "not_found", datetime.now(UTC), url, str(exc)))
    except ProviderError as exc:
        res.fetches.append(SourceFetch(source, item, "failed", datetime.now(UTC), url, str(exc)))
    except Exception as exc:  # unexpected parsing error: record, never hide
        log.exception("unexpected error fetching %s %s", source, item)
        res.fetches.append(SourceFetch(source, item, "failed", datetime.now(UTC), url, f"{type(exc).__name__}: {exc}"))
    return None


def ingest_company(
    providers: Providers,
    identity: CompanyIdentity,
    benchmark: str = "SPY",
    emit: Emit = _noop,
) -> IngestionResult:
    md, sec = providers.market, providers.sec
    res = IngestionResult(identity=identity, benchmark=benchmark)
    t = identity.ticker
    quote_url = f"https://finance.yahoo.com/quote/{t.replace('.', '-')}"

    emit("retrieve_prices", "started", f"Retrieving daily price history for {t}, {benchmark} and sector index")
    res.prices = _attempt(res, md.name, "price_history", lambda: md.get_price_history(t), quote_url + "/history")
    res.benchmark_prices = _attempt(res, md.name, f"benchmark_prices:{benchmark}", lambda: md.get_price_history(benchmark))
    res.sector_etf = SECTOR_ETFS.get(identity.sector or "")
    if res.sector_etf:
        res.sector_prices = _attempt(res, md.name, f"sector_prices:{res.sector_etf}",
                                     lambda: md.get_price_history(res.sector_etf))
    last = res.prices.index[-1].date().isoformat() if res.prices is not None else "unavailable"
    emit("retrieve_prices", "completed", f"Price history retrieved (latest close {last})")

    emit("retrieve_financials", "started", "Retrieving financial statements (vendor) and SEC XBRL facts")
    res.info = _attempt(res, md.name, "company_info", lambda: md.get_info(t), quote_url) or {}
    statements = _attempt(res, md.name, "financial_statements", lambda: md.get_statements(t), quote_url + "/financials")
    facts = None
    if identity.cik:
        facts = _attempt(res, sec.name, "xbrl_company_facts", lambda: sec.company_facts(identity.cik),
                         f"https://data.sec.gov/api/xbrl/companyfacts/CIK{identity.cik:010d}.json")
    else:
        res.fetches.append(SourceFetch(sec.name, "xbrl_company_facts", "skipped", detail="No SEC CIK for this security"))
    currency = res.info.get("financialCurrency") or identity.currency
    if statements is not None:
        res.financials = build_financials(t, statements, facts, identity.cik, currency)
    elif facts:
        from backend.data.providers.base import Statements

        empty = pd.DataFrame()
        res.financials = build_financials(
            t, Statements(income_annual=empty, income_quarterly=empty, balance_annual=empty, balance_quarterly=empty,
                          cashflow_annual=empty, cashflow_quarterly=empty, source_name=md.name, source_url=quote_url,
                          retrieved_at=datetime.now(UTC).isoformat()),
            facts, identity.cik, currency)
    if res.financials is not None:
        splits = None
        if hasattr(md, "get_splits"):
            splits = _attempt(res, md.name, "split_history", lambda: md.get_splits(t))
        if splits is None and res.prices is not None and "Stock Splits" in res.prices.columns:
            splits = res.prices["Stock Splits"]
        if splits is not None:
            adjust_for_splits(res.financials, splits)
    emit("retrieve_financials", "completed",
         f"Financial statements retrieved ({'with' if facts else 'without'} SEC cross-check)")

    emit("retrieve_news", "started", "Retrieving news, SEC filings, calendar, estimates and analyst actions")
    res.news = _attempt(res, md.name, "news", lambda: md.get_news(t), quote_url + "/news") or []
    if identity.cik:
        res.filings = _attempt(res, sec.name, "recent_filings", lambda: sec.recent_filings(identity.cik, limit=60),
                               f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={identity.cik}") or []
    res.calendar = _attempt(res, md.name, "calendar", lambda: md.get_calendar(t)) or {}
    res.estimates = _attempt(res, md.name, "consensus_estimates", lambda: md.get_estimates(t)) or {}
    res.analyst_actions = _attempt(res, md.name, "analyst_actions", lambda: md.get_analyst_actions(t))
    res.earnings_history = _attempt(res, md.name, "earnings_history", lambda: md.get_earnings_history(t))
    emit("retrieve_news", "completed", f"{len(res.news)} news items and {len(res.filings)} SEC filings retrieved")
    return res


def adjust_for_splits(fd: FinancialData, splits_or_prices) -> int:
    """Split-adjust per-share and share-count values that come from SEC filings filed BEFORE a split.

    Vendor statements are already split-adjusted; SEC values keep the basis of the filing they came
    from. For each split (date, ratio) in the price history, a filing-sourced value whose filing date
    precedes the split is divided (per-share) or multiplied (shares) by the ratio, and annotated.
    """
    ser = splits_or_prices["Stock Splits"] if isinstance(splits_or_prices, pd.DataFrame) else splits_or_prices
    if ser is None or len(ser) == 0:
        return 0
    splits = [(d.date(), float(r)) for d, r in ser.items() if r and r > 0 and r != 1]
    n = 0
    for p in fd.all_points():
        if p.value is None or p.unit not in ("USD/share", "shares") or p.filing_date is None:
            continue
        if str(getattr(p.source_type, "value", p.source_type)) != "regulatory_filing":
            continue
        factor = 1.0
        for d, r in splits:
            if p.filing_date < d:
                factor *= r
        if factor != 1.0:
            p.original_value = p.original_value if p.original_value is not None else p.value
            p.value = p.value / factor if p.unit == "USD/share" else p.value * factor
            p.notes.append(f"Split-adjusted by factor {factor:g} (filed {p.filing_date}, before split(s) "
                           f"{', '.join(str(d) for d, _ in splits if p.filing_date < d)}).")
            p.reported_or_calculated = "derived"
            n += 1
    return n
