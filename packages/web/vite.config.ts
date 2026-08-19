import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // The API is served from the same origin in the cluster; in development it
    // runs separately, so proxy rather than turning on permissive CORS.
    proxy: { "/api": "http://localhost:8000" },
  },
  build: { outDir: "dist", sourcemap: true },
});
