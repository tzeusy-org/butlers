# Dashboard Frontend

The dashboard frontend is a React 18 + TypeScript single-page application providing the administrative interface for the Butlers system. Built with Vite, shadcn/ui, Tailwind CSS, TanStack Query, React Flow, Recharts, cmdk, date-fns, and React Router v7, it provides sidebar navigation, dark mode, responsive layout, and the component/page structure for all dashboard views.

## ADDED Requirements

### Requirement: App shell layout

The application SHALL render a persistent shell layout consisting of three regions: a collapsible sidebar for navigation, a header bar, and a main content area. The header bar SHALL contain a dark mode toggle control and breadcrumb trail. The sidebar and header SHALL remain visible across all routes, with only the main content area changing on navigation. The shell SHALL also include a global command palette accessible via `/` or `Ctrl/Cmd+K`, a keyboard shortcut help dialog accessible via `?` floating button, an error boundary around route content, and toast notifications for mutation feedback.

#### Scenario: Shell layout renders all three regions

- **WHEN** the application loads at any route
- **THEN** the page SHALL display a sidebar on the left, a header bar at the top of the content area with breadcrumb trail, and a main content region filling the remaining space

#### Scenario: Header contains theme toggle

- **WHEN** the application shell renders
- **THEN** the header bar SHALL contain an accessible toggle button for switching between light, dark, and system mode

#### Scenario: Global command palette is accessible

- **WHEN** the user presses `/` or `Ctrl/Cmd+K`
- **THEN** the cmdk command palette dialog SHALL open with grouped search results and persisted recent searches

#### Scenario: Keyboard shortcut help is accessible

- **WHEN** the user presses `?`
- **THEN** a floating dialog SHALL display all available keyboard shortcuts

#### Scenario: Content area updates on navigation

- **WHEN** the user navigates from `/` to `/sessions`
- **THEN** the sidebar and header SHALL remain rendered without unmounting
- **AND** only the main content area SHALL update to display the Sessions page

#### Scenario: Error boundary wraps route content

- **WHEN** a rendering error occurs in the main content area
- **THEN** the error boundary SHALL catch the error and display a recovery UI
- **AND** the sidebar and header SHALL remain fully functional

#### Scenario: Toast notifications appear for mutations

- **WHEN** a write operation completes (e.g., schedule creation, state update)
- **THEN** a success or error toast notification SHALL appear with feedback to the user

---

### Requirement: Sidebar navigation

The sidebar SHALL display the following navigation items in order: a Search trigger (`/` or `Cmd+K`), and then primary navigation entries. The Overview item SHALL display a badge indicating the count of active issues when one or more issues exist. The sidebar footer SHALL display today's estimated spend amount. The sidebar entries are organized as follows:

**Primary Navigation (in order):**
- Overview (`/`)
- Butlers (`/butlers`)
- Sessions (`/sessions`)
- Traces (`/traces`)
- Timeline (`/timeline`)
- Notifications (`/notifications`)
- Issues (`/issues`)
- Audit Log (`/audit-log`)
- Approvals (`/approvals`)
- Contacts (`/contacts`)
- Groups (`/groups`)
- Health (`/health/measurements`)
- Collections (`/collections`)
- Connectors (`/connectors`)
- Memory (`/memory`)
- Entities (`/entities`)
- Settings (`/settings`)

Note: `Costs` (`/costs`) exists as a route but is not a sidebar item.

#### Scenario: All navigation items are rendered in order

- **WHEN** the sidebar renders
- **THEN** it SHALL display the following primary navigation entries: Overview, Butlers, Sessions, Traces, Timeline, Notifications, Issues, Audit Log, Approvals, Contacts, Groups, Health, Collections, Connectors, Memory, Entities, Settings

#### Scenario: Overview badge shows active issue count

- **WHEN** there are 3 active issues detected across the system
- **THEN** the Overview navigation item SHALL display a badge with the number "3"

#### Scenario: Overview badge is hidden when no issues exist

- **WHEN** there are zero active issues
- **THEN** the Overview navigation item SHALL NOT display a badge

#### Scenario: Sidebar footer displays today's spend

- **WHEN** the sidebar renders and the costs API reports today's total spend as $1.42
- **THEN** the sidebar footer SHALL display a formatted dollar amount of "$1.42"

#### Scenario: Search trigger opens command palette

- **WHEN** the user clicks the Search item in the sidebar or presses `/` or `Cmd+K` (Ctrl+K on non-Mac)
- **THEN** the cmdk command palette dialog SHALL open with grouped search results

---

### Requirement: Theme toggle (light/dark/system)

The application SHALL support light, dark, and system color modes via Tailwind CSS dark mode classes. The user's selected mode SHALL be persisted to `localStorage` under a well-known key. On initial load, the application SHALL read the persisted preference; if no preference exists, it SHALL default to the system's `prefers-color-scheme` setting.

#### Scenario: Toggle between light, dark, and system modes

- **WHEN** the user clicks the theme toggle in the header
- **THEN** the application SHALL cycle through the three modes (light → dark → system → light)

#### Scenario: Light mode is applied correctly

- **WHEN** the user selects light mode
- **THEN** the `<html>` element SHALL NOT receive the `dark` class
- **AND** all shadcn/ui components SHALL render in their light variants
- **AND** the preference "light" SHALL be written to `localStorage`

#### Scenario: Dark mode is applied correctly

- **WHEN** the user selects dark mode
- **THEN** the `<html>` element SHALL receive the `dark` class
- **AND** all shadcn/ui components SHALL render in their dark variants
- **AND** the preference "dark" SHALL be written to `localStorage`

#### Scenario: System mode respects OS preference

- **WHEN** the user selects system mode
- **AND** the operating system's `prefers-color-scheme` is "dark"
- **THEN** the application SHALL render in dark mode
- **AND** the preference "system" SHALL be written to `localStorage`

#### Scenario: System mode follows OS changes

- **WHEN** the user has selected system mode
- **AND** the operating system's color scheme preference changes
- **THEN** the application SHALL automatically adapt to the new OS preference

#### Scenario: Preference is restored on reload

- **WHEN** `localStorage` contains a theme preference of "dark"
- **AND** the application loads
- **THEN** the application SHALL render in dark mode without a flash of light mode

#### Scenario: System preference is used as fallback

- **WHEN** no theme preference exists in `localStorage`
- **AND** the operating system's `prefers-color-scheme` is "dark"
- **THEN** the application SHALL default to dark mode

#### Scenario: Theme preference is manageable in Settings

- **WHEN** the user navigates to `/settings`
- **THEN** the Settings page SHALL display appearance controls for light/dark/system modes
- **AND** the selection SHALL be backed by the persisted theme preference

---

### Requirement: React Router setup

The application SHALL use React Router v7 with the following comprehensive route structure:

| Route | Surface | Notes |
|------|---------|-------|
| `/` | Overview dashboard | Topology + aggregate health + failed notifications + active issues |
| `/butlers` | Butler list | Status cards for all registered butlers |
| `/butlers/:name` | Butler detail | Multi-tab control and observability surface (tabs: Overview, Sessions, Config, Skills, Schedules, Trigger, State, CRM, Memory; conditionally: Health, Collections, Entities, Routing Log, Registry) |
| `/sessions` | Session list | Cross-butler sessions with filters + drawer detail |
| `/sessions/:id` | Session detail | Full metadata/prompt/result/error view |
| `/traces` | Trace list | Distributed trace index |
| `/traces/:traceId` | Trace detail | Metadata + span waterfall |
| `/timeline` | Unified timeline | Cross-butler event stream with filters |
| `/notifications` | Notifications center | Delivery stats + filtered feed |
| `/issues` | Issues center | Active alerts and operator-dismissable issue list |
| `/audit-log` | Audit log | Filterable operation history |
| `/approvals` | Approvals queue | Pending action queue + filters + decision workflows |
| `/approvals/rules` | Approval rules | Standing rules list/detail/revoke flows |
| `/contacts` | Contacts list | Search/filter contacts with pagination |
| `/contacts/:contactId` | Contact detail | Profile + tabs (Notes, Interactions, Gifts, Loans, Activity) |
| `/groups` | Groups list | Relationship groups and membership metrics |
| `/health/measurements` | Health measurements | Measurement trend visualization + filters |
| `/health/medications` | Health medications | Medication cards + dose log/adherence |
| `/health/conditions` | Health conditions | Paginated condition status table |
| `/health/symptoms` | Health symptoms | Severity trend table with filters |
| `/health/meals` | Health meals | Grouped-by-day meals table |
| `/health/research` | Health research | Search/tag-filtered research notes |
| `/collections` | General collections | Collection cards and entity counts |
| `/entities` | General entities | Search/filter entity browser |
| `/entities/:entityId` | Entity detail | Metadata + full JSON payload |
| `/connectors` | Connectors overview | Connector cards + volume chart + fanout matrix + error log |
| `/connectors/:connectorType/:endpointIdentity` | Connector detail | Full stats + timeseries + health history |
| `/costs` | Costs and usage | Summary stats + chart + butler breakdown |
| `/memory` | Memory system | Tier cards + browser (Facts/Rules/Episodes tabs) + activity timeline |
| `/settings` | Settings | Theme preference, live-refresh defaults, search history controls |

The butler detail page (`/butlers/:name`) SHALL support tab navigation via query parameters (e.g., `?tab=sessions`). Tab URL semantics are defined as: active tab is controlled by `?tab=` query param, `overview` is default and removes the query param, and accepted deep-link values include all base tabs, health tabs (for health butler), general tabs (for general butler), and switchboard tabs (for switchboard butler). The memory browser surfaces Facts, Rules, and Episodes tabs on both `/memory` and the Butler Detail Memory tab, scoped to the butler when viewed from butler detail. Navigating to an unknown route SHALL render a 404 page.

#### Scenario: Root route renders the Overview page

- **WHEN** the user navigates to `/`
- **THEN** the Overview page component SHALL be rendered in the main content area

#### Scenario: Session detail route receives session ID param and butler context

- **WHEN** the user navigates to `/sessions/abc-123`
- **THEN** the Session detail page SHALL render with `sessionId` set to `"abc-123"`
- **AND** supports optional `?butler=<name>` query param for scoped fetch with fallback to global fetch

#### Scenario: Trace detail route receives traceId param

- **WHEN** the user navigates to `/traces/abc-123`
- **THEN** the Trace detail page SHALL render with `traceId` set to `"abc-123"`

#### Scenario: Butler detail page with tab support

- **WHEN** the user navigates to `/butlers/health?tab=schedules`
- **THEN** the Butler detail page SHALL render for the "health" butler
- **AND** the Schedules tab SHALL be active

#### Scenario: Butler detail page defaults to overview tab

- **WHEN** the user navigates to `/butlers/health` without a tab parameter
- **THEN** the Butler detail page SHALL render with the default overview tab active
- **AND** no `?tab=` query param SHALL be present in the URL

#### Scenario: Butler detail tabs vary by butler type

- **WHEN** the user navigates to `/butlers/health`
- **THEN** the Health tab SHALL be available (in addition to base tabs)
- **WHEN** the user navigates to `/butlers/general`
- **THEN** Collections and Entities tabs SHALL be available (in addition to base tabs)
- **WHEN** the user navigates to `/butlers/switchboard`
- **THEN** Routing Log and Registry tabs SHALL be available (in addition to base tabs)

#### Scenario: Memory browser is accessible from both /memory and Butler Detail

- **WHEN** the user navigates to `/memory`
- **THEN** the Memory page SHALL display Facts, Rules, and Episodes tabs with global scope
- **WHEN** the user navigates to `/butlers/relationship?tab=memory`
- **THEN** the Memory tab SHALL display Facts, Rules, and Episodes tabs scoped to the "relationship" butler

#### Scenario: Contact detail page with sub-tabs

- **WHEN** the user navigates to `/contacts/contact-123`
- **THEN** the Contact detail page SHALL render with sub-tabs: Notes, Interactions, Gifts, Loans, Activity

#### Scenario: Approvals routes support action queue and rules management

- **WHEN** the user navigates to `/approvals`
- **THEN** the approvals page SHALL display pending action queue + filters + metrics + decision workflows
- **WHEN** the user navigates to `/approvals/rules`
- **THEN** the approval rules page SHALL display standing rules list/detail/create/revoke flows

#### Scenario: Unknown route renders 404 page

- **WHEN** the user navigates to `/nonexistent-page`
- **THEN** a 404 Not Found page SHALL be rendered with a link to navigate back to `/`

---

### Requirement: Keyboard shortcuts

The application SHALL support the following keyboard shortcuts for enhanced navigation and interaction:

**Command Palette and Search:**
- `/` (forward slash) and `Ctrl/Cmd+K` open the command palette with grouped search results and recent-search persistence
- `?` (question mark) opens the keyboard shortcut help dialog

**Route Jump Shortcuts:**
- `g` then `o` jumps to Overview (`/`)
- `g` then `b` jumps to Butlers (`/butlers`)
- `g` then `s` jumps to Sessions (`/sessions`)
- `g` then `t` jumps to Traces (`/traces`)
- `g` then `r` jumps to Timeline (`/timeline`)
- `g` then `n` jumps to Notifications (`/notifications`)
- `g` then `i` jumps to Issues (`/issues`)
- `g` then `a` jumps to Approvals (`/approvals`)
- `g` then `m` jumps to Memory (`/memory`)
- `g` then `c` jumps to Contacts (`/contacts`)
- `g` then `h` jumps to Health (`/health/measurements`)

#### Scenario: Command palette opens with keyboard

- **WHEN** the user presses `/` or `Ctrl/Cmd+K`
- **THEN** the command palette dialog SHALL open
- **AND** focus SHALL move to the search input

#### Scenario: Shortcut help dialog opens with question mark

- **WHEN** the user presses `?`
- **THEN** a floating help dialog SHALL display all available keyboard shortcuts
- **AND** the dialog SHALL be closeable via Escape or clicking outside

#### Scenario: Route jump shortcuts navigate to correct page

- **WHEN** the user presses `g` then `s` while on any page
- **THEN** the application SHALL navigate to `/sessions`
- **AND** the Sessions page SHALL render

#### Scenario: Route jump shortcuts are only active when not typing

- **WHEN** the user is typing in a text input field
- **THEN** pressing `g` then `s` SHALL NOT trigger a route jump
- **AND** the characters SHALL be entered into the input field instead

#### Scenario: Command palette displays grouped search results

- **WHEN** the command palette is open
- **THEN** search results SHALL be organized into groups (e.g., "Butlers", "Recent", "Pages")
- **AND** users SHALL be able to browse and select results within groups

#### Scenario: Recent searches are persisted

- **WHEN** the user searches for "health measurements" in the command palette
- **THEN** the search query SHALL be persisted to `localStorage`
- **AND** on next load, "health measurements" SHALL appear in the recent searches section of the command palette

#### Scenario: Clear search history option in Settings

- **WHEN** the user navigates to `/settings`
- **THEN** a command palette maintenance action SHALL be available to clear locally stored recent searches

---

### Requirement: TanStack Query configuration

The application SHALL create a `QueryClient` instance with sensible defaults for a dashboard application. The default stale time SHALL be set to a value between 30 seconds and 2 minutes to balance freshness with request volume. Refetch on window focus SHALL be enabled. A global error handler SHALL be configured to surface API errors via the toast notification system.

#### Scenario: Data is treated as stale after the configured interval

- **WHEN** a query fetches data successfully
- **AND** more than the configured stale time elapses
- **AND** the component re-renders or the window regains focus
- **THEN** TanStack Query SHALL refetch the data from the API

#### Scenario: Window focus triggers refetch for stale queries

- **WHEN** the browser window loses and regains focus
- **AND** cached query data is stale
- **THEN** TanStack Query SHALL automatically refetch the stale queries

#### Scenario: API errors trigger toast notifications via global handler

- **WHEN** a query or mutation encounters an API error
- **THEN** the global error handler SHALL invoke the toast notification system with an error message describing the failure

#### Scenario: QueryClientProvider wraps the application

- **WHEN** the application renders
- **THEN** a `QueryClientProvider` with the configured `QueryClient` SHALL wrap all routed page components

---

### Requirement: API client

The application SHALL provide a base API client module at `api/client.ts` that wraps the `fetch` API. The base URL SHALL be configurable via the `VITE_API_URL` environment variable, defaulting to `/api` for production (same-origin) and `http://localhost:8200/api` for development. All responses SHALL be parsed as typed JSON using TypeScript generics. Non-2xx responses SHALL throw a typed `ApiError` containing the status code, status text, and response body.

#### Scenario: Successful GET request returns typed data

- **WHEN** `apiClient.get<Butler[]>("/butlers")` is called
- **AND** the API returns a 200 response with a JSON array of butlers
- **THEN** the client SHALL return the parsed array typed as `Butler[]`

#### Scenario: Base URL is read from VITE_API_URL

- **WHEN** the `VITE_API_URL` environment variable is set to `"http://localhost:8200/api"`
- **AND** `apiClient.get("/butlers")` is called
- **THEN** the fetch request SHALL be sent to `"http://localhost:8200/api/butlers"`

#### Scenario: Non-2xx response throws ApiError

- **WHEN** `apiClient.get("/butlers/nonexistent")` is called
- **AND** the API returns a 404 response with body `{"detail": "Butler not found"}`
- **THEN** the client SHALL throw an `ApiError` with `status: 404` and `body.detail: "Butler not found"`

#### Scenario: POST request sends JSON body

- **WHEN** `apiClient.post("/butlers/health/trigger", { prompt: "Check vitals" })` is called
- **THEN** the fetch request SHALL include `Content-Type: application/json` header
- **AND** the request body SHALL be the JSON-serialized payload

---

### Requirement: Responsive design

The application layout SHALL be responsive. On viewports narrower than the `md` breakpoint (768px), the sidebar SHALL collapse into a hidden off-screen drawer accessible via a hamburger menu button in the header. On viewports at or above the `md` breakpoint, the sidebar SHALL be visible in its expanded or collapsed state as controlled by the user. The collapsed sidebar state SHALL be persisted to `localStorage`.

#### Scenario: Sidebar is visible on desktop viewport

- **WHEN** the viewport width is 1024px or wider
- **THEN** the sidebar SHALL be visible in its current state (expanded or collapsed)
- **AND** no hamburger menu button SHALL be displayed in the header

#### Scenario: Sidebar collapses to drawer on mobile

- **WHEN** the viewport width is below 768px
- **THEN** the sidebar SHALL be hidden off-screen
- **AND** a hamburger menu button SHALL appear in the header

#### Scenario: Hamburger menu opens the sidebar drawer on mobile

- **WHEN** the viewport is below 768px
- **AND** the user taps the hamburger menu button
- **THEN** the sidebar SHALL slide in as an overlay drawer
- **AND** tapping outside the drawer or pressing Escape SHALL close it

#### Scenario: Sidebar collapsed state persists across reloads

- **WHEN** the user collapses the sidebar on a desktop viewport
- **AND** the page is reloaded
- **THEN** the sidebar SHALL render in its collapsed state

#### Scenario: Collapsible sidebar on desktop shows icons only

- **WHEN** the user collapses the sidebar on a viewport at or above 768px
- **THEN** the sidebar SHALL render in a narrow icon-only mode
- **AND** navigation items SHALL display only their icons without labels

---

### Requirement: Loading states

The application SHALL display skeleton loaders in place of content while data is being fetched. Data tables SHALL show skeleton rows matching the expected column layout. Chart components SHALL show a skeleton placeholder matching the chart's dimensions. Skeleton loaders SHALL use a pulsing animation to indicate loading.

#### Scenario: Data table shows skeleton rows while loading

- **WHEN** a page containing a data table is rendered
- **AND** the TanStack Query for the table data is in a loading state
- **THEN** the table SHALL display skeleton rows with pulsing placeholder cells matching the column layout
- **AND** the skeleton rows SHALL number between 5 and 10 to approximate a realistic data density

#### Scenario: Chart shows skeleton placeholder while loading

- **WHEN** a page containing a Recharts chart is rendered
- **AND** the TanStack Query for the chart data is in a loading state
- **THEN** a skeleton placeholder matching the chart's width and height SHALL be displayed with a pulsing animation

#### Scenario: Skeleton is replaced by data on fetch completion

- **WHEN** a query transitions from loading to success
- **THEN** the skeleton loader SHALL be replaced by the actual rendered data
- **AND** no layout shift SHALL occur beyond the content difference between skeleton and real data

---

### Requirement: Error boundaries

The application SHALL implement React error boundaries to catch rendering errors in page components. When a rendering error is caught, the error boundary SHALL display a recovery UI showing a user-friendly error message, the error details (in a collapsible section), and a "Try again" button that resets the error boundary and re-renders the child tree. A top-level error boundary SHALL wrap the entire routed content area. Individual pages MAY implement their own error boundaries for more granular recovery.

#### Scenario: Rendering error is caught and recovery UI is shown

- **WHEN** a page component throws a rendering error
- **THEN** the error boundary SHALL catch the error
- **AND** display a recovery UI with the message "Something went wrong"
- **AND** the error message and stack trace SHALL be available in a collapsible details section

#### Scenario: Try again button resets the error boundary

- **WHEN** the error boundary is displaying the recovery UI
- **AND** the user clicks the "Try again" button
- **THEN** the error boundary SHALL reset its error state
- **AND** attempt to re-render the child component tree

#### Scenario: Error boundary does not affect sibling components

- **WHEN** a rendering error occurs in the main content area
- **THEN** the sidebar and header SHALL remain fully functional
- **AND** navigation to other pages SHALL still work

#### Scenario: Top-level error boundary wraps routed content

- **WHEN** the application renders
- **THEN** an error boundary component SHALL wrap the React Router `<Outlet>` or equivalent routed content area
- **AND** the sidebar layout SHALL be outside this error boundary

---

### Requirement: Toast notifications

The application SHALL provide a toast notification system for surfacing transient messages to the user. API errors caught by the TanStack Query global error handler SHALL trigger an error toast displaying the error message. Successful write operations (trigger, schedule CRUD, state set/delete) SHALL trigger a success toast confirming the action. Toasts SHALL auto-dismiss after a configurable duration (default 5 seconds) and SHALL be dismissible by the user before the timeout. Multiple toasts SHALL stack vertically.

#### Scenario: API error triggers an error toast

- **WHEN** an API request fails with error message "Failed to fetch butler status"
- **THEN** an error-styled toast SHALL appear with the text "Failed to fetch butler status"
- **AND** the toast SHALL auto-dismiss after 5 seconds

#### Scenario: Successful write operation triggers a success toast

- **WHEN** the user triggers a butler and the API returns a 200 response
- **THEN** a success-styled toast SHALL appear confirming the action (e.g., "Butler triggered successfully")

#### Scenario: Toast is manually dismissible

- **WHEN** an error toast is displayed
- **AND** the user clicks the dismiss button on the toast
- **THEN** the toast SHALL be removed immediately without waiting for the auto-dismiss timeout

#### Scenario: Multiple toasts stack vertically

- **WHEN** two API errors occur in rapid succession
- **THEN** two error toasts SHALL be displayed simultaneously, stacked vertically
- **AND** each toast SHALL have its own independent auto-dismiss timer

#### Scenario: Toast appears in a fixed viewport position

- **WHEN** a toast notification is triggered
- **THEN** the toast SHALL appear in a fixed position (bottom-right of the viewport)
- **AND** the toast SHALL not cause layout shifts in the main content area
