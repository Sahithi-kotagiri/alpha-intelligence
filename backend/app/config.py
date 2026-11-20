"""Application configuration.

All settings come from environment variables (or a local `.env` file that is never
committed). Secrets are held as `SecretStr` so they are not printed by accident in
logs or reprs.
"""

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: str = Field(default="local", description="local | test | production")

    # SEC EDGAR requires every automated client to identify itself with a
    # User-Agent of the form "Organisation contact@example.com".
    # See https://www.sec.gov/os/accessing-edgar-data
    sec_user_agent: str | None = Field(default=None)

    # LLM provider is pluggable; nothing in business logic depends on this value.
    llm_provider: str = Field(default="none", description="none | anthropic | ...")
    anthropic_api_key: SecretStr | None = None

    database_url: str = Field(default="sqlite:///./data/investment_intelligence.db")

    http_timeout_seconds: float = 20.0

    @property
    def sec_configured(self) -> bool:
        return bool(self.sec_user_agent and "@" in self.sec_user_agent)

    @property
    def llm_configured(self) -> bool:
        if self.llm_provider == "anthropic":
            return self.anthropic_api_key is not None
        return False


@lru_cache
def get_settings() -> Settings:
    return Settings()
