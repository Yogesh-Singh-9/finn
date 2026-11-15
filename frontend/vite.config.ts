import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  // --host: reachable from a phone on the same wifi. /api goes to the FastAPI backend
  // (only used for a ticker with no precomputed research snapshot).
  server: { port: 5173, host: true, proxy: { "/api": "http://127.0.0.1:8000" } },
});
