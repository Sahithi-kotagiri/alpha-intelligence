import { defineConfig } from "@playwright/test";

// E2E against SYNTHETIC fixture data: backend on :8010 (scripts/run_fixture_server.py), Next on :3010.
export default defineConfig({
  testDir: "./e2e",
  timeout: 90_000,
  use: { baseURL: "http://127.0.0.1:3010", trace: "off" },
  webServer: [
    { command: "cd .. && uv run python scripts/run_fixture_server.py --port 8010", url: "http://127.0.0.1:8010/api/health", reuseExistingServer: false, timeout: 60_000 },
    { command: "API_INTERNAL_URL=http://127.0.0.1:8010 npx next start -p 3010", url: "http://127.0.0.1:3010", reuseExistingServer: false, timeout: 60_000 },
  ],
});
