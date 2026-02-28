# Dashboard Application Shell

## Purpose

The Butlers dashboard is the **primary administrative gateway** for operating the entire butler system. It is not a secondary monitoring view -- it IS the control plane through which human operators detect failures, diagnose runtime behavior, and take corrective action. Every butler, session, trace, notification, approval, and domain entity is accessible exclusively through this single-pane-of-glass interface.

The backend is distributed across multiple butlers, modules, and databases. Without a unified UI, operators must jump between logs, DB queries, and daemon endpoints. The dashboard eliminates this by combining cross-butler status, connector health, session/trace visibility, approval governance, domain data browsing, and admin controls into a single pane. This reduces operational latency for three critical loops:

- **Detect:** Identify what is failing, degraded, or expensive.
- **Diagnose:** Inspect sessions, traces, state, and timeline context.
- **Act:** Trigger runs, update schedules, correct state, and debug MCP tools from one UI.

### Scope Boundaries

**In scope:**
- Monitoring and diagnostics across butlers.
- Read-heavy data exploration across domain surfaces.
- Selected write/admin operations through dashboard API endpoints.
- Keyboard-first navigation and quick search for operational speed.

**Out of scope:**
- Replacing chat as the main user interaction path.
- Full CRUD for every domain entity (many domain screens are read-focused).
- End-user workflow UX (this is an operator/admin dashboard).

### Cross-Cutting UX Contracts

All data-bearing surfaces follow consistent state patterns:

- **Loading:** Skeleton placeholders matching the layout of real-data counterparts.
- **Empty:** Explicit empty-state message with contextual guidance toward the creation action.
- **Error:** Explicit error text; in select cases (e.g., butler list), stale cached data remains visible with a warning banner.

The application shell defines the outermost structural frame: the sidebar navigation, page header with breadcrumbs, command palette, keyboard shortcuts, theme system, loading/error/empty state patterns, auto-refresh architecture, and the full UI primitive library. All domain pages render inside this shell and inherit its design system, responsive behavior, and operational affordances.

The technology stack is: React 18 with TypeScript, React Router v7 (browser router), TanStack Query v5 for server state, Tailwind CSS v4 with shadcn/ui components (backed by Radix UI primitives), Lucide icons, Sonner toast notifications, class-variance-authority for variant-driven styling, and Vite as the build tool.

## ADDED Requirements

### Requirement: Application Entry Point and Provider Hierarchy

The application boots via a React 18 StrictMode render. The provider hierarchy is: `StrictMode` > `QueryClientProvider` (TanStack Query) > `RouterProvider` (React Router). ReactQueryDevtools are included in development builds but start closed.

#### Scenario: Application mounts successfully

- **WHEN** the browser loads the root HTML document
- **THEN** React renders the `App` component inside `StrictMode`
- **AND** `QueryClientProvider` wraps the entire router with a shared `QueryClient` instance
- **AND** `RouterProvider` initializes browser-based routing with `createBrowserRouter`
- **AND** the `BASE_URL` from Vite's `import.meta.env` is normalized and passed as the router `basename`

#### Scenario: TanStack Query default configuration

- **WHEN** the `QueryClient` is created
- **THEN** the default `staleTime` for all queries is 30 seconds (30,000ms)
- **AND** the default retry count is 1 (one retry after initial failure)
- **AND** these defaults can be overridden per-query by individual hooks

### Requirement: Root Layout Composition

The root layout is a single route wrapper that composes the shell structure. All page routes render as children of this layout via React Router's `<Outlet />`.

#### Scenario: Root layout renders all shell affordances

- **WHEN** any route within the application is visited
- **THEN** the `Shell` component renders with the `PageHeader` passed as the `header` prop
- **AND** page content renders inside an `ErrorBoundary` within the shell's main content area
- **AND** the `CommandPalette` dialog is mounted (initially closed) outside the shell
- **AND** the `ShortcutHints` floating button and dialog are mounted
- **AND** the `Toaster` (Sonner) is mounted for toast notifications
- **AND** global keyboard shortcuts are registered via `useKeyboardShortcuts`

### Requirement: Shell Layout Structure

The shell implements a responsive sidebar + main content layout that fills the full viewport height. The sidebar and main area are arranged in a horizontal flex container.

#### Scenario: Desktop layout (viewport >= md breakpoint)

- **WHEN** the viewport width is at or above the `md` Tailwind breakpoint (768px)
- **THEN** the desktop sidebar renders as a persistent `<aside>` element with a right border
- **AND** the sidebar width is 256px (`w-64`) when expanded or 64px (`w-16`) when collapsed
- **AND** width transitions animate over 200ms via `transition-[width]`
- **AND** the mobile drawer is not visible

#### Scenario: Mobile layout (viewport < md breakpoint)

- **WHEN** the viewport width is below the `md` breakpoint
- **THEN** the desktop sidebar is hidden (`hidden md:flex`)
- **AND** a hamburger button appears in the header (left side, before the page header)
- **AND** tapping the hamburger opens the sidebar as a `Sheet` (Radix dialog-based drawer) sliding in from the left at 256px width
- **AND** navigating to a route automatically closes the mobile drawer via the `onNavClick` callback

#### Scenario: Main content area structure

- **WHEN** the shell renders its main area
- **THEN** a header bar of height 56px (`h-14`) renders with horizontal padding of 24px (`px-6`) and a bottom border
- **AND** the main content area below the header fills remaining vertical space with `overflow-y-auto` and 24px padding (`p-6`)
- **AND** the header contains the `PageHeader` component alongside the mobile hamburger button (on small screens)

### Requirement: Sidebar Navigation

The sidebar provides the primary navigation for the entire dashboard. It consists of a brand header, a scrollable navigation list, a footer with spend summary, and a collapse toggle.

#### Scenario: Brand header

- **WHEN** the sidebar is expanded
- **THEN** the brand text "Butlers" renders as a semibold `text-lg` element in the 56px-tall header area
- **WHEN** the sidebar is collapsed
- **THEN** the brand text fades out (`opacity-0 w-0 overflow-hidden`) and the single letter "B" renders instead

#### Scenario: Navigation sections and items configuration

- **WHEN** the sidebar renders
- **THEN** navigation items are organized into three labelled sections displayed in order:
  1. **Main** — Overview (`/`, exact match), Butlers (`/butlers`), Sessions (`/sessions`), Ingestion (`/ingestion`), Approvals (`/approvals`), Memory (`/memory`), Secrets (`/secrets`), Settings (`/settings`)
  2. **Dedicated Butlers** — Relationships group (Contacts `/contacts`, Groups `/groups`; butler-aware on `relationship`), Education (`/education`; butler-aware on `education`), Health (`/health/measurements`), Calendar (`/calendar`)
  3. **Telemetry** — Traces (`/traces`), Timeline (`/timeline`), Notifications (`/notifications`), Issues (`/issues`), Audit Log (`/audit-log`)
- **AND** each section header is a clickable button containing an uppercase `text-[11px]` semibold label with `tracking-wider` and `text-muted-foreground/60` styling, plus a small chevron icon that rotates 90 degrees when expanded
- **AND** clicking a section header toggles its expanded/collapsed state with `max-h` and `opacity` transitions over 200ms
- **AND** Main and Dedicated Butlers sections default to expanded; Telemetry defaults to collapsed (`defaultExpanded: false`)
- **AND** a section auto-expands when any of its items (including group children) matches the current active route
- **AND** when the sidebar is collapsed (icon-only mode), section headers are hidden and sections are visually separated by a thin horizontal `border-border` divider
- **AND** sections with no visible items (all butler-filtered) are excluded from rendering
- **AND** each item renders a first-letter icon placeholder (the first character of the label in a 24x24 rounded `bg-muted` container) and the label text

#### Scenario: Active state highlighting

- **WHEN** a navigation item's path matches the current URL
- **THEN** the item receives `bg-accent text-accent-foreground` styling
- **AND** for the Overview item (`/`), only exact path match triggers the active state (via the `end` prop)
- **AND** for all other items, both exact match and prefix match (e.g., `/sessions/abc` activates `/sessions`) trigger the active state

#### Scenario: Collapsible navigation groups

- **WHEN** a group header (e.g., "Relationships") is clicked
- **THEN** the group toggles between expanded and collapsed states
- **AND** expansion animates via `max-h` and `opacity` transitions over 200ms
- **AND** a chevron icon rotates 90 degrees when expanded
- **WHEN** any child route within the group is the current active route
- **THEN** the group auto-expands regardless of user toggle state
- **WHEN** the sidebar is collapsed (icon-only mode)
- **THEN** groups render as a single icon link pointing to the first child route

#### Scenario: Butler-aware nav filtering

- **WHEN** the sidebar renders and the butlers API returns the roster
- **THEN** navigation items with a `butler` field are only shown if a butler with that name exists in the roster
- **AND** the Relationships group is only visible when the "relationship" butler is in the roster
- **WHEN** the butlers API is loading or returns an error
- **THEN** all navigation items are shown (graceful degradation)

#### Scenario: Sidebar collapse toggle

- **WHEN** the user clicks the collapse toggle button at the bottom of the desktop sidebar
- **THEN** the sidebar toggles between expanded (256px) and collapsed (64px) states
- **AND** the toggle button shows a double-chevron-left icon that rotates 180 degrees when collapsed
- **AND** the collapse state is managed in the Shell component's local state (not persisted across page loads)

#### Scenario: Sidebar footer

- **WHEN** the sidebar is expanded
- **THEN** a footer section renders below the navigation with a "Today's spend" label
- **AND** it fetches live cost data via the `useCostSummary("today")` hook (same data source as the dashboard overview's "Est. Cost Today" card)
- **AND** while loading or when data is unavailable, the value displays "--"
- **AND** when data is available, the value displays the formatted cost (e.g., "$26.27")
- **WHEN** the sidebar is collapsed
- **THEN** the footer content is hidden

### Requirement: Full Route Map

The router defines all application routes as children of the root layout. All routes share the shell, header, error boundary, and sidebar.

#### Scenario: Top-level routes

- **WHEN** the router is initialized
- **THEN** the following routes are registered:
  - `/` -- Overview dashboard
  - `/butlers` -- Butler list
  - `/butlers/:name` -- Butler detail (parameterized)
  - `/sessions` -- Session list
  - `/sessions/:id` -- Session detail (parameterized)
  - `/traces` -- Trace list
  - `/traces/:traceId` -- Trace detail (parameterized)
  - `/timeline` -- Unified timeline
  - `/notifications` -- Notifications center
  - `/issues` -- Issues center
  - `/audit-log` -- Audit log
  - `/approvals` -- Approvals queue
  - `/approvals/rules` -- Approval standing rules
  - `/calendar` -- Calendar workspace
  - `/contacts` -- Contacts list
  - `/contacts/:contactId` -- Contact detail (parameterized)
  - `/groups` -- Groups list
  - `/costs` -- Costs and usage (not in sidebar)
  - `/memory` -- Memory system
  - `/settings` -- Local UI settings
  - `/secrets` -- Secrets management

#### Scenario: Health sub-routes

- **WHEN** the Health section is navigated to
- **THEN** the following health sub-routes are available:
  - `/health/measurements` -- Health measurements (sidebar entry point)
  - `/health/medications` -- Medications
  - `/health/conditions` -- Conditions
  - `/health/symptoms` -- Symptoms
  - `/health/meals` -- Meals
  - `/health/research` -- Research

#### Scenario: Ingestion routes with legacy redirects

- **WHEN** the ingestion section is navigated to
- **THEN** `/ingestion` renders the ingestion overview page
- **AND** `/ingestion/connectors/:connectorType/:endpointIdentity` renders the connector detail page
- **WHEN** a user visits the legacy `/connectors` path
- **THEN** they are redirected to `/ingestion?tab=connectors` via `<Navigate replace />`
- **WHEN** a user visits `/connectors/:connectorType/:endpointIdentity`
- **THEN** they are redirected to `/ingestion/connectors/:connectorType/:endpointIdentity` with query params preserved

### Requirement: Page Header with Breadcrumbs

The page header renders inside the shell's header bar and provides breadcrumb navigation, a search trigger, and a theme toggle.

#### Scenario: Auto-generated breadcrumbs

- **WHEN** the page header renders without explicit breadcrumb props
- **THEN** breadcrumbs are auto-generated from the current URL pathname
- **AND** the first crumb is always "Home" linking to `/`
- **AND** each URL segment becomes a crumb with the segment name capitalized
- **AND** the last crumb (current page) renders as plain text without a link
- **AND** crumbs are separated by `/` characters

#### Scenario: Custom breadcrumbs and title

- **WHEN** the page header is provided explicit `breadcrumbs` and `title` props
- **THEN** the custom breadcrumbs override auto-generation
- **AND** the title renders as an `<h1>` with `text-lg font-semibold` styling below the breadcrumb trail

#### Scenario: Header action buttons

- **WHEN** the page header renders
- **THEN** a search icon button (magnifying glass) appears on the right side, triggering the command palette on click
- **AND** a theme toggle button appears next to the search button
- **AND** both buttons use the `ghost` variant at `sm` size with 32x32px dimensions

### Requirement: Command Palette (Global Search)

The command palette is a modal overlay providing cross-entity search with keyboard navigation, recent search history, and grouped results by category.

#### Scenario: Opening the command palette

- **WHEN** the user presses `Cmd+K` (macOS) or `Ctrl+K` (other platforms)
- **OR** the user presses `/` (when not in an input/textarea/contentEditable)
- **OR** the user clicks the search icon in the page header
- **THEN** the command palette dialog opens at 20% from the top of the viewport
- **AND** the search input is auto-focused
- **AND** the previous query and selection state are reset

#### Scenario: Search behavior

- **WHEN** the user types fewer than 2 characters
- **THEN** no search API call is made
- **AND** recent searches are displayed (if any exist, up to 5)
- **WHEN** the user types 2 or more characters
- **THEN** a debounced search request is sent to the backend search API
- **AND** results are grouped by category (sessions, state, contacts, etc.) with category headers
- **AND** each result shows a title, optional snippet, and a butler badge

#### Scenario: Keyboard navigation within results

- **WHEN** the command palette has results and the user presses Arrow Down
- **THEN** the selection index moves to the next result (clamped to the last result)
- **WHEN** the user presses Arrow Up
- **THEN** the selection index moves to the previous result (clamped to 0)
- **WHEN** the user presses Enter
- **THEN** the selected result's URL is navigated to, the query is saved to recent searches, and the dialog closes
- **AND** mouse hover on a result also updates the selection index

#### Scenario: Recent searches persistence

- **WHEN** a search query leads to navigation
- **THEN** the query is saved to localStorage under the `butlers:recent-searches` key
- **AND** duplicates are deduplicated (most recent first)
- **AND** the history is capped at 5 entries
- **AND** the recent search list can be cleared from the Settings page

#### Scenario: Loading, error, and empty states

- **WHEN** a search is in progress
- **THEN** skeleton placeholders render in the results area (two group headers with line items)
- **WHEN** the search API returns an error
- **THEN** the text "Search failed. Please try again." renders in destructive color
- **WHEN** the search completes with zero results
- **THEN** the text "No results found" renders in muted foreground color

#### Scenario: Footer keyboard hints

- **WHEN** results are displayed in the command palette
- **THEN** a footer bar shows keyboard hints: up/down arrows to navigate, Enter to open
- **AND** an ESC keyboard hint is shown next to the search input

### Requirement: Keyboard Shortcuts System

The application supports vim-inspired two-key navigation shortcuts and search shortcuts, registered globally via the `useKeyboardShortcuts` hook.

#### Scenario: Search shortcuts

- **WHEN** the user presses `Cmd+K` or `Ctrl+K` (regardless of focus context)
- **THEN** the command palette opens
- **WHEN** the user presses `/` outside of input/textarea/contentEditable elements
- **THEN** the command palette opens

#### Scenario: Two-key "g" navigation

- **WHEN** the user presses `g` followed by a second key within 1 second
- **THEN** the application navigates to the corresponding route:
  - `g` then `o` -- Overview (`/`)
  - `g` then `b` -- Butlers (`/butlers`)
  - `g` then `s` -- Sessions (`/sessions`)
  - `g` then `t` -- Timeline (`/timeline`)
  - `g` then `r` -- Traces (`/traces`)
  - `g` then `n` -- Notifications (`/notifications`)
  - `g` then `i` -- Issues (`/issues`)
  - `g` then `a` -- Audit Log (`/audit-log`)
  - `g` then `m` -- Memory (`/memory`)
  - `g` then `c` -- Contacts (`/contacts`)
  - `g` then `h` -- Health (`/health/measurements`)
  - `g` then `e` -- Ingestion (`/ingestion`)
- **AND** the pending "g" state expires after 1 second if no second key is pressed
- **AND** shortcuts do not fire when focus is in an input, textarea, or contentEditable element

#### Scenario: Shortcut hints dialog

- **WHEN** the user clicks the floating "?" button in the bottom-right corner of the viewport
- **THEN** a dialog opens listing all available keyboard shortcuts with their key combinations
- **AND** the button has `opacity-60` by default and `opacity-100` on hover
- **AND** the button is fixed-positioned at `bottom-4 right-4` with `z-50`

### Requirement: Dark Mode and Theme System

The dashboard supports three theme modes (light, dark, system) using a CSS class-based dark mode strategy with localStorage persistence.

#### Scenario: Theme initialization

- **WHEN** the application loads
- **THEN** the stored theme preference is read from `localStorage` under the key `theme`
- **AND** valid values are `light`, `dark`, and `system`; invalid or missing values default to `system`
- **AND** the `system` mode resolves to the OS preference via `prefers-color-scheme` media query

#### Scenario: Theme application

- **WHEN** the resolved theme is `dark`
- **THEN** the `dark` class is added to the `<html>` element
- **WHEN** the resolved theme is `light`
- **THEN** the `dark` class is removed from the `<html>` element
- **AND** theme changes are persisted to `localStorage` immediately

#### Scenario: System theme reactivity

- **WHEN** the theme is set to `system` and the OS preference changes
- **THEN** the resolved theme updates reactively via a `change` event listener on the `prefers-color-scheme` media query
- **AND** the UI updates without requiring a page reload

#### Scenario: Theme toggle in header

- **WHEN** the user clicks the theme toggle button in the header
- **THEN** the theme cycles: if currently `system`, toggle to the opposite of the resolved theme; if explicit `light` or `dark`, toggle to the other
- **AND** the button icon shows a sun (for switching to light) when in dark mode and a moon (for switching to dark) when in light mode

### Requirement: CSS Design Token System

The design system uses CSS custom properties (design tokens) defined in `:root` and overridden in `.dark`, using the OKLCH color space for perceptual uniformity. All tokens are mapped into Tailwind's color system via a `@theme inline` block.

#### Scenario: Light mode color tokens

- **WHEN** the light theme is active
- **THEN** the following semantic tokens are defined:
  - `--background`: pure white (`oklch(1 0 0)`)
  - `--foreground`: near-black (`oklch(0.145 0 0)`)
  - `--primary` / `--primary-foreground`: dark neutral / near-white
  - `--secondary` / `--secondary-foreground`: very light neutral / dark neutral
  - `--muted` / `--muted-foreground`: light neutral background / mid-gray text
  - `--accent` / `--accent-foreground`: light neutral / dark neutral (matches secondary)
  - `--destructive`: red-orange (`oklch(0.577 0.245 27.325)`)
  - `--border` / `--input`: light gray (`oklch(0.922 0 0)`)
  - `--ring`: mid-gray for focus rings
  - Five chart colors for data visualization
  - Sidebar-specific tokens mirroring the main palette

#### Scenario: Dark mode color tokens

- **WHEN** the dark theme is active
- **THEN** background inverts to near-black (`oklch(0.145 0 0)`)
- **AND** foreground inverts to near-white (`oklch(0.985 0 0)`)
- **AND** card and popover backgrounds use a slightly lighter dark (`oklch(0.205 0 0)`)
- **AND** borders use semi-transparent white (`oklch(1 0 0 / 10%)`)
- **AND** chart colors shift to higher-chroma variants optimized for dark backgrounds
- **AND** sidebar tokens follow the dark card background

#### Scenario: Border radius tokens

- **WHEN** any component uses rounded corners
- **THEN** the base `--radius` is `0.625rem` (10px)
- **AND** derived radii (`sm`, `md`, `lg`, `xl`, `2xl`, `3xl`, `4xl`) are computed relative to the base

#### Scenario: Typography defaults

- **WHEN** the application renders text
- **THEN** the root font stack is `system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif`
- **AND** base line height is 1.5, font weight is 400
- **AND** font smoothing is enabled (`-webkit-font-smoothing: antialiased`, `-moz-osx-font-smoothing: grayscale`)
- **AND** font synthesis is disabled for consistent rendering

### Requirement: UI Primitive Component Library

The dashboard uses shadcn/ui as its component library, which generates local component files backed by Radix UI headless primitives and styled with Tailwind CSS via class-variance-authority (CVA).

#### Scenario: Button component variants

- **WHEN** a `Button` is rendered
- **THEN** the following variants are available:
  - `default`: primary background with primary foreground
  - `destructive`: red background with white text
  - `outline`: bordered with background, subtle shadow
  - `secondary`: secondary background colors
  - `ghost`: transparent background with hover accent
  - `link`: text-only with underline on hover
- **AND** the following sizes are available: `default` (h-9), `xs` (h-6), `sm` (h-8), `lg` (h-10), `icon` (size-9), `icon-xs` (size-6), `icon-sm` (size-8), `icon-lg` (size-10)
- **AND** all buttons include focus-visible ring styles and disabled opacity

#### Scenario: Badge component variants

- **WHEN** a `Badge` is rendered
- **THEN** variants include: `default`, `secondary`, `destructive`, `outline`, `ghost`, `link`
- **AND** badges render as rounded-full pill shapes with `px-2 py-0.5 text-xs font-medium`
- **AND** the `asChild` prop enables Radix Slot composition

#### Scenario: Card component structure

- **WHEN** a `Card` is rendered
- **THEN** it uses `bg-card text-card-foreground` with `rounded-xl border shadow-sm` and `py-6`
- **AND** sub-components `CardHeader`, `CardTitle`, `CardDescription`, `CardAction`, `CardContent`, and `CardFooter` compose the internal layout
- **AND** `CardHeader` uses a CSS grid layout with auto-rows and optional action slot

#### Scenario: Dialog component (modals)

- **WHEN** a `Dialog` is rendered
- **THEN** it uses Radix Dialog primitives with a backdrop overlay (`bg-black/50`)
- **AND** content centers at `top-50% left-50%` with translate transforms
- **AND** open/close animations include fade-in/fade-out and zoom-in-95/zoom-out-95
- **AND** an optional close button (X icon) renders in the top-right corner
- **AND** the `showCloseButton` prop controls its visibility (defaults to true)

#### Scenario: Sheet component (drawers)

- **WHEN** a `Sheet` is rendered
- **THEN** it uses Radix Dialog primitives configured as a slide-in panel
- **AND** the `side` prop controls slide direction: `left`, `right`, `top`, or `bottom`
- **AND** open animation duration is 500ms, close animation is 300ms
- **AND** the mobile sidebar uses the `left` side variant at `w-64`

#### Scenario: Table component

- **WHEN** a `Table` is rendered
- **THEN** it wraps in a container with `overflow-x-auto` for horizontal scrolling
- **AND** rows have hover highlight (`hover:bg-muted/50`) and border-bottom
- **AND** header cells use `font-medium` with `h-10` height

#### Scenario: Form components (Input, Select, Textarea, Checkbox, Label)

- **WHEN** form components are rendered
- **THEN** `Input` renders at `h-9` with border, focus-visible ring, and placeholder styling
- **AND** `Select` uses Radix Select primitives with animated dropdown content, check indicators, and scroll buttons
- **AND** `Textarea` uses `field-sizing-content` for auto-height with a minimum of `min-h-16`
- **AND** `Checkbox` renders as a 16x16 rounded-sm box with check indicator animation
- **AND** `Label` renders as `text-sm font-medium` with peer-disabled opacity

#### Scenario: Tabs component

- **WHEN** `Tabs` are rendered
- **THEN** two list variants are available: `default` (muted background pill) and `line` (underline indicator)
- **AND** tabs support both horizontal and vertical orientations
- **AND** active tab triggers show `bg-background` with shadow in default variant, or a bottom/side underline in line variant
- **AND** the `line` variant uses a pseudo-element (`after:`) for the active indicator

#### Scenario: Tooltip component

- **WHEN** a `Tooltip` wraps an element
- **THEN** it uses Radix Tooltip primitives with `bg-foreground text-background` (inverted colors)
- **AND** the tooltip includes a directional arrow
- **AND** the default `delayDuration` on the provider is 0ms (instant show)

#### Scenario: Dropdown menu component

- **WHEN** a `DropdownMenu` is rendered
- **THEN** it uses Radix DropdownMenu primitives with animated content (fade + zoom + slide)
- **AND** menu items support `default` and `destructive` variants
- **AND** checkbox items, radio items, sub-menus, separators, labels, and shortcut hints are all available

### Requirement: Skeleton Loading Components

The dashboard provides a library of reusable skeleton loaders that match the layout of their real-data counterparts, ensuring perceived performance during data fetching.

#### Scenario: Base skeleton primitive

- **WHEN** a `Skeleton` element renders
- **THEN** it applies `bg-accent animate-pulse rounded-md` for a pulsing placeholder effect

#### Scenario: Card skeleton

- **WHEN** a `CardSkeleton` renders
- **THEN** it shows a card with optional header placeholders (title line at `h-5 w-40`, description at `h-4 w-64`)
- **AND** a configurable number of content lines (default 3) with the last line at 75% width

#### Scenario: Table skeleton

- **WHEN** a `TableSkeleton` renders
- **THEN** it shows a table with skeleton header cells and a configurable number of rows (default 5)
- **AND** column widths and alignment are specified per-column to match the real table layout
- **AND** a pre-configured `NotificationTableSkeleton` variant matches the notification feed layout

#### Scenario: Stats skeleton

- **WHEN** a `StatsSkeleton` renders
- **THEN** it shows a responsive grid of stat cards (2 columns on mobile, 4 on desktop)
- **AND** each card has a title placeholder, a circular icon placeholder, and a value placeholder

#### Scenario: Chart skeleton

- **WHEN** a `ChartSkeleton` renders
- **THEN** it shows a card with title and description placeholders and a large rectangular area (default `h-64`)

### Requirement: Error Boundary

A React class-based error boundary wraps all route content to catch and recover from rendering errors without crashing the entire application.

#### Scenario: Error is caught during render

- **WHEN** a child component throws an error during rendering
- **THEN** the error boundary catches it via `getDerivedStateFromError`
- **AND** the error is logged to `console.error` with component stack info
- **AND** a fallback UI renders: centered layout with min-height 400px, a "Something went wrong" heading in destructive color, the error message (or "An unexpected error occurred"), and a "Try again" outline button
- **WHEN** the user clicks "Try again"
- **THEN** the error state resets and the child content attempts to re-render

### Requirement: Empty State Pattern

A reusable `EmptyState` component provides consistent empty-data messaging across all pages.

#### Scenario: Empty state renders

- **WHEN** a page or section has no data to display
- **THEN** the `EmptyState` component renders centered content with 64px vertical padding
- **AND** an optional icon renders at `text-4xl` in `text-muted-foreground`
- **AND** the title renders as `text-lg font-semibold`
- **AND** the description renders as `text-sm text-muted-foreground` with a max width of `max-w-sm`
- **AND** an optional action slot renders below the description with 16px top margin

### Requirement: Toast Notification System

The dashboard uses Sonner for toast notifications, providing feedback for mutations, errors, and informational messages.

#### Scenario: Toast rendering

- **WHEN** a toast is triggered (via `toast()`, `toast.success()`, `toast.error()`, etc.)
- **THEN** the Sonner toaster renders the notification using the current theme
- **AND** custom icons are used: `CircleCheckIcon` for success, `InfoIcon` for info, `TriangleAlertIcon` for warning, `OctagonXIcon` for error, `Loader2Icon` (spinning) for loading
- **AND** toast styling uses CSS variables mapped to the design token system (`--popover`, `--popover-foreground`, `--border`, `--radius`)

### Requirement: Auto-Refresh Architecture

Pages with live data provide a user-controllable auto-refresh mechanism with configurable intervals, pause/resume, and localStorage persistence.

#### Scenario: Auto-refresh default state

- **WHEN** a page using `useAutoRefresh` mounts for the first time
- **THEN** auto-refresh is enabled by default (reading from `localStorage` key `butlers:auto-refresh:enabled`, falling back to `true`)
- **AND** the default interval is 10 seconds (reading from `localStorage` key `butlers:auto-refresh:interval`, falling back to 10,000ms)
- **AND** the hook returns a `refetchInterval` value compatible with TanStack Query (the interval number when enabled, `false` when disabled)

#### Scenario: Interval options

- **WHEN** the user changes the refresh interval
- **THEN** the available options are: 5s (5,000ms), 10s (10,000ms), 30s (30,000ms), 60s (60,000ms)
- **AND** invalid interval values are silently rejected
- **AND** the selected interval is persisted to `localStorage`

#### Scenario: Auto-refresh toggle UI

- **WHEN** the `AutoRefreshToggle` component renders
- **THEN** it shows a "Live" badge (emerald green) when enabled
- **AND** an interval selector dropdown is shown (disabled when auto-refresh is paused)
- **AND** a Pause/Resume button toggles the enabled state
- **AND** the component uses `Button` size `sm` at height `h-8` with `text-xs`

#### Scenario: Settings page control

- **WHEN** the user visits `/settings`
- **THEN** the "Live Refresh Defaults" card shows the `AutoRefreshToggle` component
- **AND** changes to enabled state and interval are persisted and apply as defaults to all pages using `useAutoRefresh`

### Requirement: Settings Page

The settings page provides local-only (browser-scoped) preferences for the dashboard operator.

#### Scenario: Appearance settings

- **WHEN** the user visits `/settings`
- **THEN** the Appearance card shows a theme selector with options: System, Light, Dark
- **AND** the currently resolved theme is displayed as text below the selector
- **AND** theme changes take immediate effect

#### Scenario: Command palette settings

- **WHEN** the user visits `/settings`
- **THEN** the Command Palette card shows the count of saved recent searches
- **AND** a "Clear recent searches" button removes all saved searches from `localStorage`
- **AND** the button is disabled when the count is 0

### Requirement: Utility Infrastructure

Shared utilities underpin component styling and settings persistence.

#### Scenario: Class name merging

- **WHEN** the `cn()` utility function is called with class value arguments
- **THEN** it composes classes via `clsx` and merges Tailwind conflicts via `tailwind-merge`
- **AND** this ensures that component prop-based class overrides correctly take precedence over base styles

#### Scenario: Command palette event bridge

- **WHEN** `dispatchOpenCommandPalette()` is called from any location (header button, keyboard shortcut)
- **THEN** a `CustomEvent` named `open-search` is dispatched on the `window` object
- **AND** the `CommandPalette` component listens for this event and opens the dialog

#### Scenario: Local settings resilience

- **WHEN** `localStorage` read or write operations fail (e.g., in private browsing or quota exceeded)
- **THEN** all settings functions silently catch errors and return fallback values
- **AND** the application continues to function with default settings
