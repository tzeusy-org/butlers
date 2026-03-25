import path from "path"
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
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
