from fastapi.testclient import TestClient

from backend.app.config import Settings, get_settings
from backend.app.main import app


def _client_with(settings: Settings) -> TestClient:
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app)


def teardown_function() -> None:
    app.dependency_overrides.clear()


def test_health_reports_unconfigured_providers_honestly() -> None:
    client = _client_with(Settings(_env_file=None, sec_user_agent=None, llm_provider="none"))
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["capabilities"]["sec_edgar"] == "not_configured"
    assert body["capabilities"]["llm"] == "not_configured"
    assert body["capabilities"]["market_prices"] == "not_implemented"


def test_sec_requires_contact_email_in_user_agent() -> None:
    assert not Settings(_env_file=None, sec_user_agent="NoEmail").sec_configured
    assert Settings(_env_file=None, sec_user_agent="Research me@example.com").sec_configured


def test_api_key_is_not_leaked_in_repr() -> None:
    s = Settings(_env_file=None, llm_provider="anthropic", anthropic_api_key="sk-secret-value")
    assert s.llm_configured
    assert "sk-secret-value" not in repr(s)
