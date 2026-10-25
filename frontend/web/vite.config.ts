import { defineConfig } from "vite";
declare const process: { env: Record<string, string | undefined> };
import react from "@vitejs/plugin-react";
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { "/api": process.env.AMAT_API_URL || "http://127.0.0.1:8765" },
  },
});
