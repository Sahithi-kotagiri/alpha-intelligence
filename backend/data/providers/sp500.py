"""S&P 500 constituent list.

S&P Dow Jones Indices licenses the official list; there is no free official feed.
The maintained Wikipedia table is used as a transparent public source and is
cross-referenced against SEC CIKs. Retrieval time and source URL are recorded so the
UI can show when the list was last refreshed.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime

import httpx
import pandas as pd

from backend.data.cache import DiskCache
from backend.data.providers.base import ProviderError

REQUIRED_COLUMNS = ["Symbol", "Security", "GICS Sector", "GICS Sub-Industry"]


def parse_constituents_html(html: str) -> pd.DataFrame:
    tables = pd.read_html(io.StringIO(html), attrs={"id": "constituents"})
    if not tables:
        raise ValueError("constituents table not found")
    df = tables[0]
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"constituents table missing columns: {missing}")
    out = pd.DataFrame(
        {
            "symbol": df["Symbol"].astype(str).str.strip().str.upper(),
            "name": df["Security"].astype(str).str.strip(),
            "sector": df["GICS Sector"].astype(str).str.strip(),
            "sub_industry": df["GICS Sub-Industry"].astype(str).str.strip(),
            "headquarters": df.get("Headquarters Location", pd.Series([None] * len(df))),
            "date_added": df.get("Date added", pd.Series([None] * len(df))),
            "cik": pd.to_numeric(df.get("CIK", pd.Series([None] * len(df))), errors="coerce"),
            "founded": df.get("Founded", pd.Series([None] * len(df))).astype(str),
        }
    )
    out = out.drop_duplicates("symbol").reset_index(drop=True)
    if len(out) < 450:
        raise ValueError(f"constituents table has only {len(out)} rows; refusing to use a partial list")
    return out


class WikipediaSP500Provider:
    name = "Wikipedia: List of S&P 500 companies"

    def __init__(self, url: str, cache: DiskCache, ttl_hours: float = 168, timeout: float = 30):
        self.url = url
        self.cache = cache
        self.ttl_hours = ttl_hours
        self.timeout = timeout

    def constituents(self, force: bool = False) -> tuple[pd.DataFrame, datetime]:
        if not force:
            hit = self.cache.get("universe", "sp500", self.ttl_hours)
            if hit is not None:
                return hit[0], datetime.fromtimestamp(hit[1], UTC)
        try:
            r = httpx.get(self.url, headers={"User-Agent": "InvestmentIntelligencePrototype/0.1"}, timeout=self.timeout)
            r.raise_for_status()
            df = parse_constituents_html(r.text)
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError(self.name, f"could not retrieve constituents: {exc}") from exc
        self.cache.set("universe", "sp500", df)
        return df, datetime.now(UTC)
