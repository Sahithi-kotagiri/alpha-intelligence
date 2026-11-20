"""Run the API against SYNTHETIC fixture providers (offline, deterministic) for browser E2E tests.

Usage: uv run python scripts/run_fixture_server.py --port 8010
Never use for real research: all data is fictional.
"""

import argparse
import os
import tempfile

import uvicorn

parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int, default=8010)
args = parser.parse_args()
tmp = tempfile.mkdtemp(prefix="ii-fixture-")
os.environ.update(DATABASE_URL=f"sqlite:///{tmp}/fixture.db", SCHEDULER_ENABLED="false", LLM_PROVIDER="none",
                  AUTH_MODE="local", SEC_USER_AGENT="Fixture fixture@example.invalid", CACHE_DIR=f"{tmp}/cache")

from backend.app import main  # noqa: E402
from backend.tests.fixtures import fake_providers  # noqa: E402

providers = fake_providers()
main.get_providers = lambda: providers
uvicorn.run(main.app, host="127.0.0.1", port=args.port, log_level="warning")
