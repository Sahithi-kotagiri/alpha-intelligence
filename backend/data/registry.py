"""Wires configured provider implementations together (dependency container)."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from backend.app.config import Settings, get_settings
from backend.data.cache import DiskCache
from backend.data.providers.sec_edgar import SecEdgarProvider
from backend.data.providers.sp500 import WikipediaSP500Provider
from backend.data.providers.yfinance_provider import YFinanceProvider


@dataclass
class Providers:
    market: YFinanceProvider
    sec: SecEdgarProvider
    universe: WikipediaSP500Provider
    cache: DiskCache


def build_providers(settings: Settings) -> Providers:
    cache = DiskCache(settings.cache_dir)
    if settings.market_data_provider != "yfinance":
        raise ValueError(f"Unsupported market_data_provider {settings.market_data_provider!r}")
    return Providers(
        market=YFinanceProvider(cache, settings.cache_ttl_hours_info, settings.cache_ttl_hours_prices,
                                settings.cache_ttl_hours_statements),
        sec=SecEdgarProvider(settings.sec_user_agent, cache, settings.sec_max_requests_per_second),
        universe=WikipediaSP500Provider(settings.universe_source_url, cache, settings.cache_ttl_hours_universe),
        cache=cache,
    )


@lru_cache
def get_providers() -> Providers:
    return build_providers(get_settings())
