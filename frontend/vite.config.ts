import path from 'node:path'
import { fileURLToPath } from 'node:url'
import type { Plugin } from 'vite'
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const frontendRoot = path.dirname(fileURLToPath(import.meta.url))

const CHUNK_WARNING_LIMIT_KB = 1_500

function packageNameFromModuleId(id: string): string | undefined {
  const nodeModulesMarker = '/node_modules/'
  const nodeModulesIndex = id.lastIndexOf(nodeModulesMarker)
  if (nodeModulesIndex === -1) return undefined

  const segments = id.slice(nodeModulesIndex + nodeModulesMarker.length).split('/')
  const [scopeOrName, name] = segments
  if (!scopeOrName) return undefined

  return scopeOrName.startsWith('@') && name ? `${scopeOrName}/${name}` : scopeOrName
}

/**
 * Keep the app shell and the few measured third-party domains independently
 * cacheable. Do not add a catch-all node_modules rule: it would make future
 * lazy feature dependencies eager by accident.
 */
export function manualChunks(id: string): string | undefined {
  const packageName = packageNameFromModuleId(id)
  if (!packageName) return undefined

  // MapWidgetInner is already React.lazy-loaded. Its rendering dependencies
  // must stay on that boundary rather than joining an eager shared vendor chunk.
  if (packageName === 'maplibre-gl' || packageName === 'h3-js') return 'vendor-map'

  if (packageName === 'recharts' || packageName.startsWith('d3-')) return 'vendor-charts'
  if (packageName.startsWith('@xyflow/') || packageName === '@dagrejs/dagre') return 'vendor-graph'
  if (
    packageName === 'react' ||
    packageName === 'react-dom' ||
    packageName === 'react-router' ||
    packageName.startsWith('@tanstack/') ||
    packageName === 'scheduler'
  ) {
    return 'vendor-framework'
  }
  if (packageName === 'date-fns' || packageName === 'date-fns-tz') return 'vendor-date'
  if (
    packageName.startsWith('@radix-ui/') ||
    packageName === 'radix-ui' ||
    packageName === 'lucide-react' ||
    packageName === 'sonner' ||
    packageName === 'tailwind-merge' ||
    packageName === 'class-variance-authority' ||
    packageName === 'clsx'
  ) {
    return 'vendor-ui'
  }

  return undefined
}

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
  // Dependencies are shared between worktrees through a node_modules symlink,
  // so generated Vite artifacts must live in the writable worktree instead.
  cacheDir: '.vite',
  build: {
    // The statically routed app shell and the intentionally lazy map renderer
    // each remain below this measured budget after the explicit domain splits.
    // Keep the warning active for meaningful growth in either boundary.
    chunkSizeWarningLimit: CHUNK_WARNING_LIMIT_KB,
    rollupOptions: {
      output: { manualChunks },
    },
  },
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
      "@": path.resolve(frontendRoot, './src'),
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
