"""FastAPI entry point.

Milestone 0 exposes only a health endpoint that reports, honestly, which external
capabilities are configured. It never claims a provider is connected unless the
configuration needed to reach it is present.
"""

from typing import Annotated

from fastapi import Depends, FastAPI

from backend.app.config import Settings, get_settings

app = FastAPI(title="Investment Intelligence API", version="0.0.1")


@app.get("/api/health")
def health(settings: Annotated[Settings, Depends(get_settings)]) -> dict:
    return {
        "status": "ok",
        "environment": settings.environment,
        "capabilities": {
            "sec_edgar": "configured" if settings.sec_configured else "not_configured",
            "market_prices": "not_implemented",
            "news": "not_implemented",
            "llm": "configured" if settings.llm_configured else "not_configured",
        },
    }
