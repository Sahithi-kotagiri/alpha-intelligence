"""SEC EDGAR adapter: ticker map, submissions (filings list) and XBRL company facts.

Primary-source data. SEC fair-access policy requires a descriptive User-Agent with
contact details and no more than 10 requests/second; both are enforced here.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

import httpx

from backend.data.cache import DiskCache
from backend.data.providers.base import NotFoundError, ProviderError

log = logging.getLogger(__name__)

TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
FILING_INDEX_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accn_nodash}/"
FILING_DOC_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accn_nodash}/{doc}"


class SecEdgarProvider:
    name = "SEC EDGAR"

    def __init__(self, user_agent: str | None, cache: DiskCache, max_rps: float = 8.0, timeout: float = 30.0):
        self.user_agent = user_agent
        self.cache = cache
        self.min_interval = 1.0 / max_rps
        self.timeout = timeout
        self._lock = threading.Lock()
        self._last = 0.0

    @property
    def configured(self) -> bool:
        return bool(self.user_agent and "@" in self.user_agent)

    def _get_json(self, url: str) -> Any:
        if not self.configured:
            raise ProviderError(self.name, "SEC_USER_AGENT is not configured (name + contact email required)", False)
        with self._lock:
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                r = httpx.get(url, headers={"User-Agent": self.user_agent, "Accept-Encoding": "gzip"}, timeout=self.timeout)
                if r.status_code == 404:
                    raise NotFoundError(self.name, f"not found: {url}")
                if r.status_code in (429, 503):
                    last_exc = ProviderError(self.name, f"HTTP {r.status_code} (rate limited)")
                    time.sleep(2 ** (attempt + 1))
                    continue
                r.raise_for_status()
                return r.json()
            except NotFoundError:
                raise
            except (httpx.HTTPError, ValueError) as exc:
                last_exc = exc
                time.sleep(2**attempt)
        raise ProviderError(self.name, f"request failed: {url}: {last_exc}")

    def _cached(self, ns: str, key: str, ttl: float, url: str) -> Any:
        hit = self.cache.get(ns, key, ttl)
        if hit is not None:
            return hit[0]
        data = self._get_json(url)
        self.cache.set(ns, key, data)
        return data

    def ticker_map(self) -> dict[str, dict[str, Any]]:
        """ticker -> {cik, name, exchange}."""
        data = self._cached("sec", "ticker_map", 24, TICKER_MAP_URL)
        fields = data["fields"]
        out = {}
        for row in data["data"]:
            rec = dict(zip(fields, row, strict=False))
            out[str(rec["ticker"]).upper()] = {"cik": int(rec["cik"]), "name": rec["name"], "exchange": rec.get("exchange")}
        return out

    def submissions(self, cik: int) -> dict[str, Any]:
        return self._cached("sec_submissions", str(cik), 6, SUBMISSIONS_URL.format(cik=cik))

    def company_facts(self, cik: int) -> dict[str, Any]:
        return self._cached("sec_facts", str(cik), 12, FACTS_URL.format(cik=cik))

    @staticmethod
    def filing_url(cik: int, accession: str, primary_doc: str | None = None) -> str:
        nodash = accession.replace("-", "")
        if primary_doc:
            return FILING_DOC_URL.format(cik=cik, accn_nodash=nodash, doc=primary_doc)
        return FILING_INDEX_URL.format(cik=cik, accn_nodash=nodash)

    def recent_filings(self, cik: int, forms: tuple[str, ...] | None = None, limit: int = 40) -> list[dict[str, Any]]:
        sub = self.submissions(cik)
        recent = sub.get("filings", {}).get("recent", {})
        keys = list(recent.keys())
        n = len(recent.get("accessionNumber", []))
        rows = []
        for i in range(n):
            rec = {k: recent[k][i] for k in keys}
            if forms and rec.get("form") not in forms:
                continue
            rec["url"] = self.filing_url(cik, rec["accessionNumber"], rec.get("primaryDocument"))
            rows.append(rec)
            if len(rows) >= limit:
                break
        return rows
