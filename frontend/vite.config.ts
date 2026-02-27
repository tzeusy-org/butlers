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
    allowedHosts: ["tzeusy.parrot-hen.ts.net"],
    proxy: {
      "/api": {
        target: "http://localhost:40200",
        changeOrigin: true,
      },
    },
  },
})
