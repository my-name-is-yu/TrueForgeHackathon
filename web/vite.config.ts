import { defineConfig } from "vitest/config";

export default defineConfig({
  server: {
    port: 5173,
    proxy: { "/api": "http://127.0.0.1:8713", "/health": "http://127.0.0.1:8713" },
  },
  test: { environment: "jsdom" },
});
