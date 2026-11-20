"""Provider interfaces.

Providers retrieve source information and nothing else: no investment logic lives
here. Each interface is a Protocol so a new vendor can be added by implementing the
same methods, and tests can supply fixture-backed fakes.

Errors distinguish *provider failure* (outage, rate limit, blocked) from *data that
genuinely does not exist* so the UI never shows a failed fetch as a zero.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Protocol

import pandas as pd
from pydantic import BaseModel


class ProviderError(Exception):
    """The provider could not be reached or returned an unusable response."""

    def __init__(self, provider: str, message: str, retryable: bool = True):
        super().__init__(f"{provider}: {message}")
        self.provider = provider
        self.retryable = retryable


class NotFoundError(Exception):
    """The provider responded, but the requested entity/data does not exist."""

    def __init__(self, provider: str, message: str):
        super().__init__(f"{provider}: {message}")
        self.provider = provider


class Statements(BaseModel):
    """Raw vendor statements, columns = period-end dates, index = line items."""

    model_config = {"arbitrary_types_allowed": True}
    income_annual: pd.DataFrame
    income_quarterly: pd.DataFrame
    balance_annual: pd.DataFrame
    balance_quarterly: pd.DataFrame
    cashflow_annual: pd.DataFrame
    cashflow_quarterly: pd.DataFrame
    source_name: str
    source_url: str
    retrieved_at: str


class NewsItem(BaseModel):
    id: str
    title: str
    publisher: str | None
    published_at: str | None
    url: str | None
    summary: str | None
    related_tickers: list[str] = []
    source_name: str


class MarketDataProvider(Protocol):
    name: str

    def get_info(self, ticker: str) -> dict[str, Any]: ...

    def get_price_history(self, ticker: str, start: date | None = None, period: str = "5y") -> pd.DataFrame: ...

    def get_statements(self, ticker: str) -> Statements: ...

    def get_news(self, ticker: str) -> list[NewsItem]: ...

    def get_calendar(self, ticker: str) -> dict[str, Any]: ...

    def get_estimates(self, ticker: str) -> dict[str, Any]: ...

    def get_analyst_actions(self, ticker: str) -> pd.DataFrame: ...

    def get_earnings_history(self, ticker: str) -> pd.DataFrame: ...


class FilingsProvider(Protocol):
    name: str

    def ticker_map(self) -> dict[str, dict[str, Any]]: ...

    def submissions(self, cik: int) -> dict[str, Any]: ...

    def company_facts(self, cik: int) -> dict[str, Any]: ...


class UniverseProvider(Protocol):
    name: str

    def constituents(self) -> pd.DataFrame: ...
