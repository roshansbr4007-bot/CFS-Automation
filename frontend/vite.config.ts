import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// 127.0.0.1, not localhost: on Windows Node may resolve localhost to ::1 while runserver
// listens on IPv4 only. changeOrigin stays false so Django's CSRF origin check sees :5173.
const api = { "/api": { target: "http://127.0.0.1:8000", changeOrigin: false } };
// Phase 8: WebSocket upgrades for /ws/ go to the same Django server (Daphne runserver). The
// browser's Origin (localhost:5173) is kept so Django's origin check sees the real page.
const realtime = { "/ws": { target: "ws://127.0.0.1:8000", ws: true, changeOrigin: false } };

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { ...api, ...realtime } },
  preview: { port: 5173, proxy: { ...api, ...realtime } },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    css: false,
    testTimeout: 10000,
  },
});

