from datetime import timedelta

import pytest

from backend.tests.conftest import wait_for


def start(client, ticker="TSTA", **kw):
    body = {"ticker": ticker, "confirmed": True, **kw}
    r = client.post("/api/analysis", json=body)
    assert r.status_code == 202, r.text
    return r.json()["analysis_id"]


def test_health_and_universe(client):
    h = client.get("/api/health").json()
    assert h["capabilities"]["llm"] == "not_configured"
    u = client.get("/api/universe").json()
    assert u["count"] == 13 and u["rows"][0]["symbol"].startswith("TST")


def test_search_resolve_and_confirmation_required(client):
    res = client.get("/api/companies/search", params={"q": "bravo"}).json()["results"]
    assert res[0]["symbol"] == "TSTB" and res[0]["in_sp500"]
    ident = client.get("/api/companies/TSTA").json()
    assert ident["resolved"] and ident["identity"]["sub_industry"] == "Application Software"
    unk = client.get("/api/companies/ZZZZ").json()
    assert not unk["resolved"] and unk["needs_confirmation"]
    assert client.post("/api/analysis", json={"ticker": "TSTA"}).status_code == 428
    assert client.get("/api/companies/bad$$").status_code == 422


def test_full_journey(client):
    aid = start(client)
    run = wait_for(client, aid)
    assert run["status"] == "completed", run.get("error")
    res = run["result"]
    assert res["final"]["recommendation"] in ("Buy Now", "Wait", "Watchlist", "Reject")
    assert 0 < res["final"]["confidence"] <= 0.9
    assert res["mode"] == "deterministic"
    assert set(res["reports"]) == {"fundamental", "technical", "news", "risk_portfolio", "devils_advocate"}
    assert res["data_quality"]["status"] in ("good", "acceptable")
    assert res["final"]["meta"]["versions"]["thresholds"].startswith("thresholds-")
    # events: real, ordered, terminal
    ev = client.get(f"/api/analysis/{aid}/events", params={"stream": False}).json()
    seqs = [e["seq"] for e in ev["events"]]
    assert seqs == sorted(seqs) and ev["events"][-1]["message"].startswith("Analysis complete")
    stages = {e["stage"] for e in ev["events"]}
    assert {"resolve", "retrieve_prices", "retrieve_financials", "validate", "calculate_metrics", "committee", "save"} <= stages
    # SSE stream replays from Last-Event-ID
    with client.stream("GET", f"/api/analysis/{aid}/events", headers={"Last-Event-ID": str(seqs[-3])}) as s:
        body = "".join(s.iter_text())
    assert body.count("event: progress") == 2 and "event: end" in body
    # evidence & provenance
    evd = client.get(f"/api/analysis/{aid}/evidence", params={"category": "financial", "limit": 5}).json()
    assert evd["items"] and all(i["source_name"] for i in evd["items"])
    dps = client.get(f"/api/analysis/{aid}/data-points", params={"field": "revenue"}).json()["data_points"]
    assert dps and all("period_end" in d for d in dps)
    mets = client.get(f"/api/analysis/{aid}/metrics").json()
    assert any(m["key"] == "peg_forward" for m in mets["metrics"])
    agents = client.get(f"/api/analysis/{aid}/agents").json()["agents"]
    assert len(agents) == 5
    # briefing derived from saved report
    b = client.get(f"/api/analysis/{aid}/briefing").json()
    assert [s["title"] for s in b["sections"]][0] == "Recommendation" and "not a probability of profit" in b["text"]
    assert client.get(f"/api/analysis/{aid}/briefing", params={"agent": "technical"}).json()["text"].startswith("Technical")
    # rule-based assistant
    a = client.post(f"/api/analysis/{aid}/ask", json={"question": f"Why did the committee choose {res['final']['recommendation']}?"}).json()
    assert a["mode"] == "rule-based" and res["final"]["recommendation"] in a["answer"]
    a2 = client.post(f"/api/analysis/{aid}/ask", json={"question": "Compare the moat with its two closest competitors"}).json()
    assert a2["mode"] == "unsupported" and "LLM" in a2["answer"]
    assert len(client.get(f"/api/analysis/{aid}/messages").json()["messages"]) == 4
    # comment / user action attributed, analysis immutable
    c = client.post(f"/api/analysis/{aid}/comment", json={"comment": "Disagree on valuation", "user_action": "not_acted"}).json()
    assert c["user_comment"].startswith("[local@localhost]")
    # history and thesis
    hist = client.get("/api/companies/TSTA/history").json()["analyses"]
    assert hist[0]["id"] == aid
    th = client.get("/api/thesis/TSTA").json()
    assert th["current"]["status"] == "active" and th["current"]["assumptions"]


def test_versioning_and_compare(client):
    a1 = start(client)
    wait_for(client, a1)
    a2 = start(client)
    r2 = wait_for(client, a2)
    assert r2["version"] == 2 and r2["parent_id"] == a1
    cmp = client.get("/api/analysis/compare", params={"a": a1, "b": a2}).json()
    assert cmp["a"]["id"] == a1 and "drivers" in cmp
    th = client.get("/api/thesis/TSTA").json()
    assert [v["status"] for v in th["versions"]] == ["active", "superseded"]
    evs = client.get("/api/thesis/TSTA/events").json()["events"]
    assert any(e["type"] == "reassessment" for e in evs)
    # idempotency
    k = client.post("/api/analysis", json={"ticker": "TSTB", "confirmed": True, "idempotency_key": "abc"}).json()
    k2 = client.post("/api/analysis", json={"ticker": "TSTB", "confirmed": True, "idempotency_key": "abc"}).json()
    assert k["analysis_id"] == k2["analysis_id"]
    wait_for(client, k["analysis_id"])


def test_failed_provider_is_reported_and_retryable(client):
    client.providers.market.fail.add("TSTC")
    aid = start(client, "TSTC")
    run = wait_for(client, aid)
    assert run["status"] == "completed"
    f = run["result"]["final"]
    assert run["result"]["data_quality"]["status"] == "critical"
    assert "Buy Now" not in f["allowed_recommendations"]
    assert run["result"]["data_quality"]["failed_sources"]
    client.providers.market.fail.discard("TSTC")


def test_unknown_ticker_fails_honestly_then_retry(client):
    from backend.database.session import get_sessionmaker
    from backend.services.analysis_service import create_analysis

    client.get("/api/auth/me")  # creates the local user
    with get_sessionmaker()() as db:
        run = create_analysis(db, 1, "NOPE", {"horizon": "12 months", "benchmark": "SPY"})
    client.app.state.runner.submit(run.id)
    r = wait_for(client, run.id)
    assert r["status"] == "failed" and "not an S&P 500" in r["error"]
    assert client.post(f"/api/analysis/{run.id}/retry").status_code == 202


def test_portfolio_and_candidate_impact(client):
    pf = {"cash": 10000, "risk_tolerance": "moderate", "horizon": "5 years", "excluded_sectors": ["Utilities"],
          "holdings": [{"ticker": "TSTB", "quantity": 100}, {"ticker": "TSTG", "market_value": 20000}]}
    p = client.post("/api/portfolio", json=pf).json()
    assert len(p["holdings"]) == 2
    risk = client.get("/api/portfolio/risk", params={"candidate": "TSTK"}).json()
    assert risk["available"] and risk["volatility"] > 0 and abs(sum(risk["weights"].values()) + risk["cash_weight"] - 1) < 1e-9
    assert risk["candidate_impact"]["constraint_breach"].startswith("TSTK")
    aid = start(client, "TSTA", include_portfolio=True)
    res = wait_for(client, aid)["result"]
    assert res["portfolio"]["available"]
    alloc = res["final"]["allocation"]
    assert alloc["available"] or "Allocation" in alloc["text"]
    aid2 = start(client, "TSTK", include_portfolio=True)
    res2 = wait_for(client, aid2)["result"]
    assert any(g["id"] == "portfolio_constraint" for g in res2["final"]["gates"])
    assert client.post("/api/portfolio", json={"holdings": [{"ticker": "TSTA"}]}).status_code == 422


def test_watchlist_and_alerts(client):
    assert client.post("/api/watchlist", json={"ticker": "TSTA", "target_price": 1e6}).json()["saved"]
    items = client.get("/api/watchlist").json()["items"]
    assert items[0]["ticker"] == "TSTA"
    out = client.post("/api/monitoring/run").json()
    assert out["checked"] == 0
    alerts = client.get("/api/alerts").json()["alerts"]
    assert any(a["kind"] == "price_target" for a in alerts)
    assert client.post(f"/api/alerts/{alerts[0]['id']}/read").json()["ok"]
    assert client.delete("/api/watchlist/TSTA").json()["removed"]


def test_thesis_monitoring_detects_change_and_reassesses(client):
    aid = start(client)
    wait_for(client, aid)
    co = client.providers.market.cos["TSTA"]
    # deteriorate the business: margins collapse in the latest quarters
    co.op_margin = 0.05
    client.providers.sec.extra_filings[co.cik] = [{
        "accessionNumber": "0000900001-26-999999", "form": "8-K", "filingDate": (__import__("datetime").date.today()).isoformat(),
        "reportDate": "", "items": "4.02", "primaryDocument": "x.htm", "url": "https://example.invalid/402"}]
    out = client.post("/api/monitoring/run").json()
    assert out["checked"] == 1 and out["events"] >= 1 and out["reassessments"]
    th = client.get("/api/thesis/TSTA").json()["current"]
    om = next(a for a in th["assumptions"] if a["key"] == "operating_margin_ttm")
    assert om["status"] in ("At risk", "Invalidated") and om["current_value"] < om["original_value"]
    evs = client.get("/api/thesis/TSTA/events").json()["events"]
    assert any(e["type"] == "sec_filing" and e["materiality"] == "high" for e in evs)
    new = wait_for(client, out["reassessments"][0])
    assert new["trigger"] == "monitoring" and new["parent_id"] == aid
    re_ = new["result"]["final"]["reassessment"]
    assert {i["agent"] for i in re_["items"]} >= {"fundamental", "news"}
    fund = new["result"]["reports"]["fundamental"]
    assert any(c["text"].startswith("Reassessment trigger") for c in fund["claims"])
    # the reassessment cannot recommend Buy Now with an unresolved 4.02 non-reliance filing
    assert "Buy Now" not in new["result"]["final"]["allowed_recommendations"]
    # second run: no duplicate events / no new reassessment within cooldown
    out2 = client.post("/api/monitoring/run").json()
    assert not out2["reassessments"]


def test_performance_tracking(client, monkeypatch):
    aid = start(client)
    wait_for(client, aid)
    from backend.database import models as M
    from backend.database.session import get_sessionmaker

    with get_sessionmaker()() as db:  # move the decision back in time so horizons have elapsed
        rec = db.query(M.Recommendation).filter_by(analysis_id=aid).one()
        rec.price_date = rec.price_date - timedelta(days=500)
        db.commit()
    out = client.post("/api/performance/update").json()
    assert out["completed"] == 4
    perf = client.get("/api/performance").json()
    obs = perf["recommendations"][0]["observations"]
    assert all(o["status"] == "complete" and o["excess_return"] is not None for o in obs)
    assert perf["summary"]["observations_complete"] == 4 and perf["summary"]["by_recommendation"][0]["small_sample"]


def test_baseline_requires_llm(client):
    aid = start(client)
    wait_for(client, aid)
    r = client.post("/api/evaluation/baseline", json={"analysis_id": aid}).json()
    assert not r["available"] and r["platform"]["citation_validity"] in (None, 1.0)


def test_voice_endpoints_without_provider(client):
    cfg = client.get("/api/voice/config").json()
    assert cfg["server_provider"] == "none" and cfg["browser_fallback"] and not cfg["stores_audio"]
    r = client.post("/api/voice/transcribe", files={"audio": ("a.webm", b"\x00\x01", "audio/webm")})
    assert r.status_code == 501
    aid = start(client)
    wait_for(client, aid)
    r = client.post("/api/voice/speech", data={"analysis_id": aid, "kind": "briefing"})
    assert r.status_code == 501
    r = client.post("/api/voice/speech", data={"analysis_id": aid, "kind": "arbitrary text"})
    assert r.status_code == 422


def test_openai_voice_provider_with_mock_transport():
    import httpx

    from backend.app.config import Settings
    from backend.voice.providers import OpenAIVoiceProvider, VoiceError

    def handler(req):
        if req.url.path.endswith("/audio/transcriptions"):
            assert req.headers["authorization"] == "Bearer sk-voice"
            return httpx.Response(200, json={"text": "why wait"})
        if req.url.path.endswith("/audio/speech"):
            return httpx.Response(200, content=b"ID3fakeMP3")
        return httpx.Response(404)

    s = Settings(_env_file=None, voice_provider="openai", openai_api_key="sk-voice")
    p = OpenAIVoiceProvider(s, transport=httpx.MockTransport(handler))
    assert p.transcribe(b"abc", "audio/webm") == "why wait"
    audio, mime = p.speech("hello", None, 1.0)
    assert audio.startswith(b"ID3") and mime == "audio/mpeg"
    with pytest.raises(VoiceError):
        p.transcribe(b"x" * (21 * 1024 * 1024), "audio/webm")


def test_settings_and_delete_my_data(client):
    assert client.put("/api/settings", json={"voice_rate": 1.25, "default_benchmark": "QQQ"}).json()["ok"]
    s = client.get("/api/settings").json()
    assert s["voice"]["rate"] == 1.25 and s["default_benchmark"] == "QQQ" and s["voice"]["store_audio"] is False
    aid = start(client)
    wait_for(client, aid)
    assert client.delete("/api/user/data").json()["deleted"]
    assert client.get("/api/analysis").json()["analyses"] == []


def test_token_auth_isolation(env, providers, monkeypatch):
    from fastapi.testclient import TestClient

    from backend.app import main
    from backend.tests.conftest import _clear_caches

    monkeypatch.setenv("AUTH_MODE", "token")
    _clear_caches()
    monkeypatch.setattr(main, "get_providers", lambda: providers)
    with TestClient(main.app) as c:
        assert c.get("/api/analysis").status_code == 401
        ta = c.post("/api/auth/register", json={"email": "a@example.com", "password": "correct horse battery"}).json()["token"]
        tb = c.post("/api/auth/register", json={"email": "b@example.com", "password": "another long password"}).json()["token"]
        ha, hb = {"Authorization": f"Bearer {ta}"}, {"Authorization": f"Bearer {tb}"}
        assert c.post("/api/auth/login", json={"email": "a@example.com", "password": "wrong password!!"}).status_code == 401
        aid = c.post("/api/analysis", json={"ticker": "TSTA", "confirmed": True}, headers=ha).json()["analysis_id"]
        assert c.get(f"/api/analysis/{aid}", headers=hb).status_code == 404
        assert c.get("/api/analysis", headers=hb).json()["analyses"] == []
        c.post("/api/portfolio", json={"holdings": [{"ticker": "TSTB", "weight": 50}]}, headers=ha)
        assert c.get("/api/portfolio", headers=hb).json()["portfolios"] == []
        c.post("/api/auth/logout", headers=ha)
        assert c.get("/api/auth/me", headers=ha).status_code == 401


def test_alert_preferences_suppress_alerts(client):
    aid = start(client)
    wait_for(client, aid)
    client.post("/api/watchlist", json={"ticker": "TSTA", "alert_prefs": {"thesis": False, "filings": False, "price": False, "earnings": False}})
    co = client.providers.market.cos["TSTA"]
    co.op_margin = 0.05
    out = client.post("/api/monitoring/run").json()
    assert out["events"] >= 1
    assert not [a for a in client.get("/api/alerts").json()["alerts"] if a["ticker"] == "TSTA"]


def test_migrations_create_missing_data_directory(tmp_path):
    from backend.database.session import run_migrations

    db = tmp_path / "does" / "not" / "exist" / "x.db"
    run_migrations(f"sqlite:///{db}")
    assert db.exists()
