import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Served behind a path prefix (/sorghum-intel/) and at the root locally, so every
// asset and API URL is relative.
export default defineConfig({
  base: "./",
  plugins: [react()],
  build: {
    outDir: "../backend/sorghum_intel/static",
    emptyOutDir: true,
    chunkSizeWarningLimit: 1500,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes("node_modules")) return "vendor";
          return undefined;
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: { "/api": { target: "http://127.0.0.1:8770", changeOrigin: false } },
  },
});
