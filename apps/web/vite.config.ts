/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const API_URL = process.env.CLAIMLENS_API_URL ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    // The browser calls /api/*; the dev server forwards it to FastAPI, so no CORS setup is needed.
    proxy: {
      "/api": { target: API_URL, changeOrigin: true, rewrite: (p) => p.replace(/^\/api/, "") },
    },
  },
  test: { environment: "jsdom", globals: true, setupFiles: ["./src/test-setup.ts"] },
});
