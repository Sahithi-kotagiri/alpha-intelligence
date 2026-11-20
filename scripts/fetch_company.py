"""Retrieve, normalise and validate one company's data and save a JSON snapshot (Milestone 1 utility).

Usage: uv run python scripts/fetch_company.py MSFT [--out data/snapshots]
"""

import argparse
import json
from pathlib import Path

from backend.data.ingestion import CompanyIdentity, ingest_company
from backend.data.registry import get_providers
from backend.data.validation import validate
from backend.services.analysis_service import jsonable

ap = argparse.ArgumentParser()
ap.add_argument("ticker")
ap.add_argument("--out", default="data/snapshots")
a = ap.parse_args()
p = get_providers()
t = a.ticker.upper()
rec = p.sec.ticker_map().get(t) or {}
res = ingest_company(p, CompanyIdentity(ticker=t, name=rec.get("name", t), cik=rec.get("cik")),
                     emit=lambda s, st, m: print(f"[{st}] {m}"))
dq = validate(res)
out = Path(a.out)
out.mkdir(parents=True, exist_ok=True)
f = out / f"{t}.json"
f.write_text(json.dumps(jsonable({"identity": res.identity.__dict__, "data_quality": dq.model_dump(mode="json"),
                                  "datapoints": [d.model_dump(mode="json") for d in res.financials.all_points()] if res.financials else [],
                                  "fetches": [x.__dict__ for x in res.fetches]}), indent=1))
print(f"data quality: {dq.status} ({dq.coverage_pct}% coverage); cross-check {dq.cross_check_summary}; saved {f}")
