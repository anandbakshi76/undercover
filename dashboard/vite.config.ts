import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  // npm run dev reads .env from the repo root; the tests set their own values.
  envDir: process.env.VITEST ? undefined : "..",
  server: {
    proxy: { "/api": process.env.BACKEND_URL ?? "http://127.0.0.1:8000" },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["src/setup.ts"],
    coverage: {
      include: ["src/**"],
      exclude: ["src/main.tsx"],
      thresholds: { statements: 80 },
    },
  },
});
