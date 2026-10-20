import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The engine listens on localhost only; the dev server proxies /api (HTTP and
// WebSocket job progress) to it so the UI uses same-origin URLs everywhere.
const ENGINE = "http://127.0.0.1:8765";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: { "/api": { target: ENGINE, ws: true } },
  },
  test: {
    include: ["src/**/*.test.{ts,tsx}"],
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test-setup.ts"],
  },
});
