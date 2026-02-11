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
  server: {
    allowedHosts: ["tzeusy.parrot-hen.ts.net"],
    proxy: {
      "/api": {
        target: "http://localhost:8200",
        changeOrigin: true,
      },
    },
  },
})
