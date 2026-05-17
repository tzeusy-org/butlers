import path from "path"
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  test: {
    // Installs global browser API stubs (e.g. ResizeObserver) before each test
    // file so that jsdom-incompatible primitives work without per-file boilerplate.
    setupFiles: ["./src/test/setup.ts"],
    // Exclude Playwright e2e specs — they import @playwright/test which
    // conflicts with vitest's own test() when picked up by vitest's file scan.
    exclude: ["**/tests/e2e/**", "**/node_modules/**"],
  },
  plugins: [tailwindcss(), react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  optimizeDeps: {
    include: ["@dagrejs/dagre"],
  },
  server: {
    allowedHosts: ["localhost", "127.0.0.1", "dashboard-api", ".ts.net"],
    proxy: {
      "/api": {
        target: process.env.VITE_PROXY_TARGET || "http://localhost:41200",
        changeOrigin: true,
      },
    },
  },
})
