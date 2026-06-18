# Butlers Dashboard

A modern web dashboard for monitoring and managing the Butlers personal AI agent system. Built as a single-page application (SPA) that provides real-time visibility into butler operations, sessions, health metrics, relationships, and system telemetry.

## Tech Stack

- **React 18** - UI framework
- **TypeScript** - Type-safe development
- **Vite** - Build tool and dev server
- **React Router 7** - Client-side routing
- **TanStack Query** - Server state management and caching
- **shadcn/ui** - Component library (New York style)
- **Tailwind CSS 4** - Utility-first styling
- **Lucide React** - Icon library
- **Recharts** - Data visualization
- **XYFlow React** - Interactive topology graphs
- **date-fns** - Date formatting
- **Sonner** - Toast notifications

## Prerequisites

- Node.js 18+ with npm
- PostgreSQL running (e.g. `docker compose up -d postgres` from the project root)
- Dashboard API backend running on `http://localhost:41200`

### Starting the Backend

From the project root:

```bash
# Start PostgreSQL (if not already running)
docker compose up -d postgres

# Start the Dashboard API
uv run butlers dashboard --port 41200
```

Or use Docker Compose to run both together:

```bash
docker compose up -d postgres dashboard-api
```

## Getting Started

### Installation

```bash
npm install
```

### Development

Start the dev server with hot module replacement:

```bash
npm run dev
```

The dashboard will be available at `http://localhost:41173` with API requests proxied to the backend at `http://localhost:41200`.

Alternatively, run the entire stack (Postgres + API + frontend) via Docker Compose from the project root:

```bash
docker compose --profile dev up
```

### Build

Compile TypeScript and build production assets:

```bash
npm run build
```

Output will be in the `dist/` directory.

### Preview

Preview the production build locally:

```bash
npm run preview
```

### Linting

Run ESLint checks:

```bash
npm run lint
```

### Stories (Ladle)

Component stories are powered by [Ladle](https://www.ladle.dev/) — a lightweight
Storybook alternative. Stories live next to their components as `*.stories.tsx`
files.

```bash
# Serve stories in the browser with hot-reload
npm run story

# Production build of the story catalogue
npm run story:build
```

Stories are located in `src/pages/` and `src/components/`:

| File | What it covers |
|---|---|
| `src/pages/ButlerDetailPage.stories.tsx` | Gate-A A2 actions slot: all status states, loading, error |

### Accessibility (axe-core)

A11y baseline tests run as part of the normal test suite using
[jest-axe](https://github.com/nickvdyck/jest-axe) + `@testing-library/react`:

```bash
# Run all tests (includes a11y)
npm test

# Run only the a11y baseline tests
npm test -- --run src/pages/ButlerDetailPage.a11y.test.tsx
```

A11y test files are named `*.a11y.test.tsx` and live next to their page file.
Each test renders a story-equivalent fixture and asserts zero axe violations.
Colour-contrast rules are disabled because jsdom cannot compute computed styles.

## Project Structure

```
frontend/
├── src/
│   ├── api/              # API client and type definitions
│   │   ├── client.ts     # Generated API client methods
│   │   ├── types.ts      # TypeScript types from backend
│   │   └── index.ts      # Public API exports
│   ├── components/       # React components organized by domain
│   │   ├── activity/     # Activity feed components
│   │   ├── audit/        # Audit log UI
│   │   ├── butler-detail/# Butler detail views
│   │   ├── costs/        # Cost tracking components
│   │   ├── general/      # General butler components
│   │   ├── issues/       # Issue tracker components
│   │   ├── layout/       # Layout components (nav, sidebar, theme)
│   │   ├── memory/       # Memory system visualization
│   │   ├── notifications/# Notification center
│   │   ├── relationship/ # Relationship butler components
│   │   ├── schedules/    # Scheduler components
│   │   ├── sessions/     # Session log viewers
│   │   ├── skeletons/    # Loading state skeletons
│   │   ├── state/        # State store viewer
│   │   ├── timeline/     # Timeline visualizations
│   │   └── topology/     # System topology graph
│   ├── hooks/            # Custom React hooks
│   │   ├── use-butlers.ts
│   │   ├── use-contacts.ts
│   │   ├── use-spend.ts
│   │   ├── use-general.ts
│   │   ├── use-health.ts
│   │   ├── use-issues.ts
│   │   ├── use-keyboard-shortcuts.ts
│   │   ├── use-memory.ts
│   │   ├── use-notifications.ts
│   │   ├── use-schedules.ts
│   │   ├── use-search.ts
│   │   ├── use-sessions.ts
│   │   ├── use-state.ts
│   │   ├── use-timeline.ts
│   │   ├── use-traces.ts
│   │   ├── use-audit-log.ts
│   │   ├── use-auto-refresh.ts
│   │   └── useDarkMode.ts
│   ├── layouts/          # Layout wrappers
│   │   └── RootLayout.tsx
│   ├── lib/              # Utilities
│   │   ├── query-client.ts
│   │   └── utils.ts
│   ├── pages/            # Route pages
│   │   ├── AuditLogPage.tsx
│   │   ├── ButlerDetailPage.tsx
│   │   ├── ButlersPage.tsx
│   │   ├── CollectionsPage.tsx
│   │   ├── ConditionsPage.tsx
│   │   ├── ContactsPage.tsx
│   │   ├── CostsPage.tsx
│   │   ├── DashboardPage.tsx
│   │   ├── EntitiesPage.tsx
│   │   ├── EntityDetailPage.tsx
│   │   ├── GroupsPage.tsx
│   │   ├── MealsPage.tsx
│   │   ├── MeasurementsPage.tsx
│   │   ├── MedicationsPage.tsx
│   │   ├── MemoryPage.tsx
│   │   ├── NotificationsPage.tsx
│   │   ├── ResearchPage.tsx
│   │   ├── SessionDetailPage.tsx
│   │   ├── SessionsPage.tsx
│   │   ├── SettingsPage.tsx
│   │   ├── SymptomsPage.tsx
│   │   ├── TimelinePage.tsx
│   │   ├── TraceDetailPage.tsx
│   │   └── TracesPage.tsx
│   ├── App.tsx           # Root app component
│   ├── main.tsx          # Entry point
│   ├── router.tsx        # Route definitions
│   ├── App.css
│   └── index.css         # Global styles and Tailwind directives
├── components.json       # shadcn/ui configuration
├── vite.config.ts        # Vite configuration
├── tsconfig.json         # TypeScript base config
├── tsconfig.app.json     # App TypeScript config
├── tsconfig.node.json    # Node TypeScript config
└── package.json          # Dependencies and scripts
```

## Backend Connection

The dashboard connects to the Butlers FastAPI backend via:

- **Development proxy**: Vite proxies `/api` requests to `http://localhost:41200` (configured in `vite.config.ts`)
- **API client**: Auto-generated TypeScript client in `src/api/client.ts` provides type-safe methods for all backend endpoints
- **TanStack Query**: Manages server state, caching, and automatic refetching

## Key Features

- **Butler Management**: Monitor all butlers, their status, schedules, and modules
- **Session Logs**: View Claude Code session transcripts and outcomes
- **Telemetry**: Distributed tracing with OpenTelemetry spans
- **State Store**: Browse butler JSONB state key-value pairs
- **Notifications**: Unified notification center across all butlers
- **Relationships**: Contact management, groups, and interaction history
- **Health Tracking**: Measurements, medications, conditions, symptoms, meals, research
- **Memory System**: Visualize episodes, facts, and rules in the memory butler
- **Audit Log**: Complete audit trail of all system operations
- **Cost Tracking**: Token usage and API costs per butler and session
- **Timeline**: Chronological activity feed across the system
- **Topology Graph**: Interactive visualization of butler dependencies

## Path Aliases

The project uses TypeScript path aliases (configured in `tsconfig.app.json` and `vite.config.ts`):

- `@/` → `src/`
- `@/components` → `src/components`
- `@/lib` → `src/lib`
- `@/hooks` → `src/hooks`

Example:
```typescript
import { Button } from "@/components/ui/button"
import { useButlers } from "@/hooks/use-butlers"
```

## Component Library

The dashboard uses [shadcn/ui](https://ui.shadcn.com/) components in the **New York** style with:
- Neutral base color
- CSS variables for theming
- Dark mode support via `next-themes`
- Lucide icons

## Development Notes

- Hot module replacement (HMR) is enabled for instant feedback
- ESLint is configured with React Hooks and React Refresh plugins
- TypeScript strict mode is enabled for type safety
- All API calls are wrapped in TanStack Query hooks for caching and error handling
- The layout includes a command palette (Cmd/Ctrl+K) for quick navigation
