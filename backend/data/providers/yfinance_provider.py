"""yfinance market-data adapter.

yfinance is an unofficial client for Yahoo Finance. It is convenient and free, but:
- it is not a licensed data feed; Yahoo's terms restrict commercial redistribution,
- endpoints can change or rate-limit without notice,
- statement values are vendor-normalised and carry no filing date.
For those reasons every value from here is labelled `market_data_vendor`, cached,
and (for statements) cross-checked against SEC filings in `normalisation.py`.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, date, datetime
from typing import Any

import pandas as pd

from backend.data.cache import DiskCache
from backend.data.providers.base import NewsItem, NotFoundError, ProviderError, Statements

log = logging.getLogger(__name__)

YAHOO_QUOTE_URL = "https://finance.yahoo.com/quote/{ticker}"


def to_yahoo_symbol(ticker: str) -> str:
    """Wikipedia/SEC use BRK.B; Yahoo uses BRK-B."""
    return ticker.strip().upper().replace(".", "-")


class YFinanceProvider:
    name = "yfinance (Yahoo Finance)"

    def __init__(
        self,
        cache: DiskCache,
        ttl_info: float = 24,
        ttl_prices: float = 6,
        ttl_statements: float = 12,
        max_retries: int = 3,
    ):
        self.cache = cache
        self.ttl_info = ttl_info
        self.ttl_prices = ttl_prices
        self.ttl_statements = ttl_statements
        self.max_retries = max_retries
        self.last_fetch_times: dict[str, datetime] = {}

    # ------------------------------------------------------------------ helpers
    def _ticker(self, ticker: str):
        import yfinance as yf

        return yf.Ticker(to_yahoo_symbol(ticker))

    def _call(self, what: str, fn):
        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                return fn()
            except NotFoundError:
                raise
            except Exception as exc:  # yfinance raises many exception types
                last_exc = exc
                name = type(exc).__name__
                retryable = "RateLimit" in name or "Timeout" in name or "Connection" in name
                log.warning("yfinance %s failed (attempt %d): %s", what, attempt + 1, exc)
                if not retryable and attempt >= 1:
                    break
                time.sleep(min(2**attempt, 8))
        raise ProviderError(self.name, f"{what} failed: {last_exc}")

    def _cached(self, ns: str, key: str, ttl: float, fn):
        hit = self.cache.get(ns, key, ttl)
        if hit is not None:
            value, written = hit
            self.last_fetch_times[f"{ns}:{key}"] = datetime.fromtimestamp(written, UTC)
            return value
        value = fn()
        self.cache.set(ns, key, value)
        self.last_fetch_times[f"{ns}:{key}"] = datetime.now(UTC)
        return value

    def fetched_at(self, ns: str, key: str) -> datetime | None:
        return self.last_fetch_times.get(f"{ns}:{key}")

    # ------------------------------------------------------------------ API
    def get_info(self, ticker: str) -> dict[str, Any]:
        def fetch():
            info = self._call(f"info({ticker})", lambda: self._ticker(ticker).info)
            if not info or not (info.get("quoteType") or info.get("longName") or info.get("shortName")):
                raise NotFoundError(self.name, f"no quote information for {ticker}")
            return dict(info)

        return self._cached("info", to_yahoo_symbol(ticker), self.ttl_info, fetch)

    def get_price_history(self, ticker: str, start: date | None = None, period: str = "5y") -> pd.DataFrame:
        """Daily OHLCV with both raw Close and Adj Close (dividends/splits adjusted)."""

        def fetch():
            t = self._ticker(ticker)
            kwargs: dict[str, Any] = {"auto_adjust": False, "actions": True, "timeout": 30}
            if start:
                kwargs["start"] = start.isoformat()
            else:
                kwargs["period"] = period
            df = self._call(f"history({ticker})", lambda: t.history(**kwargs))
            if df is None or df.empty:
                raise NotFoundError(self.name, f"no price history for {ticker}")
            df = df.copy()
            df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
            df = df[~df.index.duplicated(keep="last")].sort_index()
            return df

        key = f"{to_yahoo_symbol(ticker)}:{start or period}"
        return self._cached("prices", key, self.ttl_prices, fetch)

    def get_statements(self, ticker: str) -> Statements:
        def fetch():
            t = self._ticker(ticker)
            frames = {}
            for attr, out in [
                ("income_stmt", "income_annual"),
                ("quarterly_income_stmt", "income_quarterly"),
                ("balance_sheet", "balance_annual"),
                ("quarterly_balance_sheet", "balance_quarterly"),
                ("cashflow", "cashflow_annual"),
                ("quarterly_cashflow", "cashflow_quarterly"),
            ]:
                df = self._call(f"{attr}({ticker})", lambda a=attr: getattr(t, a))
                frames[out] = df if df is not None else pd.DataFrame()
            if all(f.empty for f in frames.values()):
                raise NotFoundError(self.name, f"no financial statements for {ticker}")
            return Statements(
                **frames,
                source_name=self.name,
                source_url=YAHOO_QUOTE_URL.format(ticker=to_yahoo_symbol(ticker)) + "/financials",
                retrieved_at=datetime.now(UTC).isoformat(),
            )

        return self._cached("statements", to_yahoo_symbol(ticker), self.ttl_statements, fetch)

    def get_splits(self, ticker: str) -> pd.Series:
        """Complete stock-split history (date -> ratio), independent of the price window."""
        def fetch():
            sp = self._call(f"splits({ticker})", lambda: self._ticker(ticker).splits)
            if sp is None or len(sp) == 0:
                return pd.Series(dtype=float)
            sp = sp.copy()
            sp.index = pd.to_datetime(sp.index).tz_localize(None).normalize()
            return sp

        return self._cached("splits", to_yahoo_symbol(ticker), self.ttl_info, fetch)

    def get_news(self, ticker: str) -> list[NewsItem]:
        def fetch():
            raw = self._call(f"news({ticker})", lambda: self._ticker(ticker).news) or []
            items = []
            for r in raw:
                c = r.get("content", r)
                url = (c.get("canonicalUrl") or {}).get("url") or (c.get("clickThroughUrl") or {}).get("url")
                items.append(
                    NewsItem(
                        id=str(r.get("id") or c.get("id")),
                        title=c.get("title") or "",
                        publisher=(c.get("provider") or {}).get("displayName"),
                        published_at=c.get("pubDate") or c.get("displayTime"),
                        url=url,
                        summary=c.get("summary") or c.get("description"),
                        related_tickers=[s.get("symbol") for s in ((c.get("finance") or {}).get("stockTickers") or [])],
                        source_name="Yahoo Finance news feed (via yfinance)",
                    )
                )
            return items

        return self._cached("news", to_yahoo_symbol(ticker), 3, fetch)

    def get_calendar(self, ticker: str) -> dict[str, Any]:
        return self._cached(
            "calendar", to_yahoo_symbol(ticker), self.ttl_info,
            lambda: dict(self._call(f"calendar({ticker})", lambda: self._ticker(ticker).calendar) or {}),
        )

    def get_estimates(self, ticker: str) -> dict[str, Any]:
        def fetch():
            t = self._ticker(ticker)
            out: dict[str, Any] = {}
            for attr in ["earnings_estimate", "revenue_estimate", "growth_estimates", "eps_trend"]:
                try:
                    out[attr] = getattr(t, attr)
                except Exception as exc:  # optional data; record absence, don't fail
                    log.info("estimates %s unavailable for %s: %s", attr, ticker, exc)
                    out[attr] = None
            return out

        return self._cached("estimates", to_yahoo_symbol(ticker), self.ttl_info, fetch)

    def get_analyst_actions(self, ticker: str) -> pd.DataFrame:
        def fetch():
            try:
                df = self._ticker(ticker).upgrades_downgrades
            except Exception as exc:
                log.info("analyst actions unavailable for %s: %s", ticker, exc)
                return pd.DataFrame()
            return df if df is not None else pd.DataFrame()

        return self._cached("analyst_actions", to_yahoo_symbol(ticker), self.ttl_info, fetch)

    def get_earnings_history(self, ticker: str) -> pd.DataFrame:
        def fetch():
            try:
                df = self._ticker(ticker).earnings_history
            except Exception as exc:
                log.info("earnings history unavailable for %s: %s", ticker, exc)
                return pd.DataFrame()
            return df if df is not None else pd.DataFrame()

        return self._cached("earnings_history", to_yahoo_symbol(ticker), self.ttl_info, fetch)

    def get_eps_trend(self, ticker: str) -> pd.DataFrame:
        """Consensus EPS now vs 7/30/60/90 days ago (rows 0q, +1q, 0y, +1y). Vendor estimates."""
        def fetch():
            try:
                df = self._ticker(ticker).eps_trend
            except Exception as exc:
                log.info("eps trend unavailable for %s: %s", ticker, exc)
                return pd.DataFrame()
            return df if df is not None else pd.DataFrame()

        return self._cached("eps_trend", to_yahoo_symbol(ticker), self.ttl_info, fetch)

    def get_prices_batch(self, tickers: list[str], period: str = "2y", interval: str = "1d",
                         chunk: int = 60) -> dict[str, pd.DataFrame]:
        """Bars for many tickers in a few batched requests (raw Close, Adj Close, Stock Splits).
        Returns {ticker: frame}; tickers with no data are omitted (the caller records them as unavailable)."""
        import yfinance as yf

        out: dict[str, pd.DataFrame] = {}
        todo = []
        for t in tickers:
            hit = self.cache.get("prices_batch", f"{to_yahoo_symbol(t)}:{period}:{interval}", self.ttl_prices)
            if hit is not None:
                out[t] = hit[0]
            else:
                todo.append(t)
        for i in range(0, len(todo), chunk):
            part = todo[i:i + chunk]
            syms = [to_yahoo_symbol(t) for t in part]
            try:
                df = self._call(f"download({len(part)} tickers)", lambda syms=syms: yf.download(
                    syms, period=period, interval=interval, auto_adjust=False, actions=True, group_by="ticker",
                    progress=False, threads=True, timeout=30))
            except ProviderError as exc:
                log.warning("batch download failed: %s", exc)
                continue
            if df is None or df.empty:
                continue
            for t, sym in zip(part, syms, strict=True):
                try:
                    sub = df[sym] if isinstance(df.columns, pd.MultiIndex) else df
                except KeyError:
                    continue
                sub = sub.dropna(subset=["Close"]).copy()
                if sub.empty:
                    continue
                sub.index = pd.to_datetime(sub.index)
                if sub.index.tz is not None:
                    sub.index = sub.index.tz_localize(None)
                sub = sub[~sub.index.duplicated(keep="last")].sort_index()
                out[t] = sub
                self.cache.set("prices_batch", f"{sym}:{period}:{interval}", sub)
        return out
