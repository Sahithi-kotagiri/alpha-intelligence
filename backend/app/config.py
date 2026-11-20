"""Application configuration.

All settings come from environment variables (or a local `.env` file that is never
committed). Secrets are held as `SecretStr` so they are not printed by accident in
logs or reprs.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: str = Field(default="local", description="local | test | production")

    # --- Data providers -------------------------------------------------------------
    # SEC EDGAR requires every automated client to identify itself with a
    # User-Agent of the form "Organisation contact@example.com".
    # See https://www.sec.gov/os/accessing-edgar-data
    sec_user_agent: str | None = Field(default=None)
    sec_max_requests_per_second: float = 8.0  # SEC fair-access limit is 10/s
    market_data_provider: str = "yfinance"
    universe_source_url: str = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    default_benchmark: str = "SPY"
    cache_dir: Path = REPO_ROOT / "data" / "cache"
    cache_ttl_hours_statements: float = 12.0
    cache_ttl_hours_prices: float = 6.0
    cache_ttl_hours_info: float = 24.0
    cache_ttl_hours_universe: float = 24.0 * 7
    universe_screen_concurrency: int = 4

    # --- LLM (provider-agnostic; business logic never imports a vendor SDK) -----------
    llm_provider: str = Field(default="none", description="none | anthropic")
    anthropic_api_key: SecretStr | None = None
    # Pinned explicitly so an ANTHROPIC_BASE_URL meant for other tools is never picked up.
    anthropic_base_url: str = "https://api.anthropic.com"
    llm_model: str = "claude-opus-5-5"
    llm_effort: str = "high"
    llm_max_tokens: int = 16000
    llm_timeout_seconds: float = 300.0
    llm_server_fallback: bool = True

    # --- Voice (server-side providers are optional; the browser provider needs no key)
    voice_provider: str = Field(default="none", description="none | openai")
    openai_api_key: SecretStr | None = None
    openai_base_url: str = "https://api.openai.com/v1"

    # --- Persistence and jobs -----------------------------------------------------------
    database_url: str = f"sqlite:///{REPO_ROOT / 'data' / 'investment_intelligence.db'}"
    job_workers: int = 2
    scheduler_enabled: bool = True
    monitoring_interval_hours: float = 24.0
    performance_interval_hours: float = 24.0

    # --- Security -------------------------------------------------------------------------
    auth_mode: str = Field(default="local", description="local (single user) | token")
    allow_registration: bool = True
    session_ttl_hours: int = 24 * 7
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    http_timeout_seconds: float = 20.0

    @property
    def sec_configured(self) -> bool:
        return bool(self.sec_user_agent and "@" in self.sec_user_agent)

    @property
    def llm_configured(self) -> bool:
        if self.llm_provider == "anthropic":
            return self.anthropic_api_key is not None
        return False

    @property
    def voice_configured(self) -> bool:
        return self.voice_provider == "openai" and self.openai_api_key is not None


@lru_cache
def get_settings() -> Settings:
    return Settings()
