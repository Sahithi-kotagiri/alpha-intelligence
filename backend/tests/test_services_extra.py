import os

import pytest

from backend.agents.llm import FakeLLM
from backend.tests.conftest import wait_for


def _run(client, t="TSTA"):
    aid = client.post("/api/analysis", json={"ticker": t, "confirmed": True}).json()["analysis_id"]
    return aid, wait_for(client, aid)["result"]


@pytest.mark.parametrize("q,needle", [
    ("What is the strongest reason not to buy?", ""),
    ("Which metric has deteriorated the most?", ""),
    ("What changed since the last analysis?", "no earlier"),
    ("Which thesis assumption is most at risk?", "Thesis assumptions"),
    ("What would need to happen for the recommendation to change?", "Decision rules"),
    ("How would this stock affect my portfolio?", "No portfolio"),
    ("Summarise the latest earnings", "Latest quarter"),
])
def test_rule_assistant_branches(client, q, needle):
    aid, _ = _run(client)
    a = client.post(f"/api/analysis/{aid}/ask", json={"question": q}).json()
    assert a["mode"] == "rule-based" and a["answer"]
    assert needle.lower() in a["answer"].lower()


def test_llm_assistant_validates_citations(client):
    aid, _ = _run(client)
    llm = FakeLLM(lambda s, u, schema: {"answer": "Valuation is above peers.", "citations": ["asm.trailing_pe", "made.up"],
                                         "needs_fresh_data": False, "uncertainty": "low"})
    client.app.state.llm_factory = lambda: llm
    a = client.post(f"/api/analysis/{aid}/ask", json={"question": "Is it expensive right now?"}).json()
    assert a["mode"] == "llm" and "made.up" not in a["citations"] and "asm.trailing_pe" in a["citations"]


def test_baseline_comparison_with_fake_llm(client):
    aid, _ = _run(client)
    client.app.state.llm_factory = lambda: FakeLLM(lambda s, u, schema: {
        "recommendation": "Buy Now", "confidence_pct": 80, "executive_summary": "Strong company trading at 31313131x sales.",
        "thesis": [{"text": "Revenue grows", "kind": "fact", "evidence_ids": ["fin.bogus"]}], "risks": [],
        "bear_case": "None", "missing_information": []})
    r = client.post("/api/evaluation/baseline", json={"analysis_id": aid, "repeats": 2}).json()
    assert r["available"] and r["baseline"]["consistency"] == 1.0
    rows = {x["metric"]: x for x in r["comparison"]["rows"]}
    assert rows["citation_validity"]["baseline_mean"] == 0.0
    assert rows["unverified_numbers"]["baseline_mean"] >= 1


def test_universe_screen_and_screener(client):
    from backend.database.session import get_sessionmaker
    from backend.database.models import ScreenRun
    from backend.services.universe import run_screen

    client.get("/api/universe")
    with get_sessionmaker()() as db:
        run = ScreenRun(status="queued")
        db.add(run)
        db.commit()
        rid = run.id
    run_screen(get_sessionmaker(), client.providers, rid, workers=2)
    s = client.get("/api/screener").json()
    assert s["screen_run"]["succeeded"] == 13 and s["rows"][0]["metrics"]
    assert any(r.get("sector_percentiles") for r in s["rows"])


def test_universe_change_detection(client):
    client.get("/api/universe")
    co = client.providers.universe.cos.pop("TSTW")
    r = client.post("/api/universe/refresh").json()
    assert r["removed"] == ["TSTW"]
    client.providers.universe.cos["TSTW"] = co
    r2 = client.post("/api/universe/refresh").json()
    assert r2["added"] == ["TSTW"]


def test_disk_cache_roundtrip(tmp_path):
    from backend.data.cache import DiskCache

    c = DiskCache(tmp_path)
    assert c.get("ns", "k", 1) is None
    c.set("ns", "k", {"a": 1})
    assert c.get("ns", "k", 1)[0] == {"a": 1}
    assert c.get("ns", "k", 0) is None  # expired


@pytest.mark.skipif(os.environ.get("RUN_LIVE") != "1", reason="live network test; set RUN_LIVE=1")
def test_live_providers_msft():
    from backend.app.config import get_settings
    from backend.data.registry import build_providers

    p = build_providers(get_settings())
    assert p.market.get_info("MSFT")["quoteType"] == "EQUITY"
    assert len(p.market.get_price_history("MSFT")) > 500
    assert p.sec.ticker_map()["MSFT"]["cik"] == 789019
    df, _ = p.universe.constituents()
    assert len(df) >= 495


def test_needs_fresh_data_for_old_analysis():
    from backend.services.assistant import needs_fresh

    res = {"final": {"meta": {"price_date": "2024-01-02"}}}
    assert needs_fresh("What is the current price right now?", res)
    assert not needs_fresh("Explain the valuation", res)
