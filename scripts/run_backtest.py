"""Point-in-time backtest of a simple deterministic composite (growth, margin, earnings yield).

Uses only SEC filings filed on/before each date and prices on/before each date; reports forward
1-year excess returns vs SPY by tercile. Survivorship bias: current constituents only.

Usage: uv run python scripts/run_backtest.py --n 60 --seed 7 --out docs/evaluation/backtest.json
"""

import argparse
import json
import random
from datetime import date
from pathlib import Path

from backend.data.registry import get_providers
from backend.intelligence.evaluation import backtest
from backend.services.analysis_service import jsonable

ap = argparse.ArgumentParser()
ap.add_argument("--n", type=int, default=60)
ap.add_argument("--seed", type=int, default=7)
ap.add_argument("--out", default="docs/evaluation/backtest.json")
a = ap.parse_args()
p = get_providers()
uni, _ = p.universe.constituents()
rows = uni.dropna(subset=["cik"]).to_dict("records")
random.Random(a.seed).shuffle(rows)
samples = []
for r in rows[: a.n]:
    try:
        samples.append({"ticker": r["symbol"], "facts": p.sec.company_facts(int(r["cik"])),
                        "prices": p.market.get_price_history(r["symbol"], period="10y")})
    except Exception as exc:  # recorded, not hidden
        print("skip", r["symbol"], exc)
bench = p.market.get_price_history("SPY", period="10y")
dates = [date(y, 6, 30) for y in range(2018, 2026)]
res = backtest(samples, bench, dates)
res["sample"] = {"requested": a.n, "loaded": len(samples), "seed": a.seed}
Path(a.out).parent.mkdir(parents=True, exist_ok=True)
Path(a.out).write_text(json.dumps(jsonable({k: v for k, v in res.items() if k != "rows"}), indent=1))
print(json.dumps(jsonable({k: v for k, v in res.items() if k != "rows"}), indent=1))
