import path from "path"
import type { Plugin } from 'vite'
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// When the app is served under a non-root base (e.g. `--base /butlers-dev/`
// behind the Tailscale `/butlers-dev` path mount), the Vite dev server 404s a
// request for the bare base path *without* a trailing slash (`/butlers-dev`)
// and only serves the app at `/butlers-dev/`. React Router's `useHref` emits
// exactly that slashless form for a `to="/"` link under a basename (the
// "Overview" nav item), so landing on / reloading that URL breaks. Redirect
// the bare base path to its trailing-slash form so both work.
function baseNoSlashRedirect(): Plugin {
  return {
    name: 'base-no-slash-redirect',
    configureServer(server) {
      const base = server.config.base
      if (base === '/') return
      const bare = base.replace(/\/$/, '')
      server.middlewares.use((req, res, next) => {
        const [pathname, query] = (req.url || '').split('?')
        if (pathname === bare) {
          res.statusCode = 301
          res.setHeader('Location', query ? `${base}?${query}` : base)
          res.end()
          return
        }
        next()
      })
    },
  }
}

// https://vite.dev/config/
export default defineConfig({
  test: {
    // Pin the runner timezone to UTC so date/time-sensitive specs are
    // deterministic on any machine. Several CalendarWorkspacePage grid-drag and
    // find-time specs render the grid in the *workspace* timezone (UTC in those
    // fixtures) while the grid's day columns are derived from browser-local
    // dates; when the runner's zone differs from UTC (e.g. a dev box in
    // Asia/Singapore, UTC+8) events/overlays bucket onto a neighbouring day
    // column and date math goes off-by-one. CI runs in UTC, so pinning UTC here
    // aligns local runs with CI rather than weakening any assertion.
    env: { TZ: "UTC" },
    // Installs global browser API stubs (e.g. ResizeObserver) before each test
    // file so that jsdom-incompatible primitives work without per-file boilerplate.
    setupFiles: ["./src/test/setup.ts"],
    // Exclude Playwright e2e specs — they import @playwright/test which
    // conflicts with vitest's own test() when picked up by vitest's file scan.
    exclude: ["**/tests/e2e/**", "**/node_modules/**"],
  },
  plugins: [tailwindcss(), react(), baseNoSlashRedirect()],
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
