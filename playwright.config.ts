import { defineConfig } from "@playwright/test";

const backend = "http://127.0.0.1:8001";
const agent = "http://127.0.0.1:8003";
const auth = "http://127.0.0.1:8004";

export default defineConfig({
  testDir: "e2e",
  outputDir: "e2e/test-results",
  reporter: "list",
  use: { baseURL: "http://localhost:5174" },
  webServer: [
    {
      command: "node e2e/agent-stand-in.js",
      url: `${agent}/requests`,
      reuseExistingServer: false,
    },
    {
      command: "node e2e/auth-stand-in.js",
      url: `${auth}/auth/v1/.well-known/jwks.json`,
      reuseExistingServer: false,
    },
    {
      command:
        "uv run -m backend.db --reset && uv run -m uvicorn backend.main:app --port 8001",
      url: `${backend}/api/health`,
      env: {
        UNDERCOVER_DB: "e2e/test.db",
        UNDERCOVER_AGENT: agent,
        VITE_SUPABASE_URL: auth,
      },
      reuseExistingServer: false,
    },
    {
      command: "npx vite dashboard --port 5174 --strictPort",
      url: "http://localhost:5174",
      env: {
        BACKEND_URL: backend,
        VITE_SUPABASE_URL: auth,
        VITE_SUPABASE_PUBLISHABLE_KEY: "sb_publishable_e2e",
      },
      reuseExistingServer: true,
    },
  ],
});
