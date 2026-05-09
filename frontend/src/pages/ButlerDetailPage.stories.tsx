/**
 * ButlerDetailPage stories — gate-A A2 shape.
 *
 * Each named export is a Ladle story exercising:
 *   - Default  (loaded, status=ok)
 *   - Loading
 *   - ErrorState
 *   - StatusOk / online
 *   - StatusDegraded
 *   - StatusError
 *   - StatusWaiting
 *
 * Run stories: `npm run story`
 * A11y tests:  `npm test` (see ButlerDetailPage.a11y.test.tsx)
 *
 * Bead: bu-sfeuw.4
 */

import type { Story } from "@ladle/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
}

function StoryWrapper({ children }: { children: React.ReactNode }) {
  return (
    <QueryClientProvider client={makeQueryClient()}>
      <MemoryRouter>
        <div style={{ fontFamily: "sans-serif", padding: "1rem" }}>
          {children}
        </div>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

// ---------------------------------------------------------------------------
// ControlledActionsShell
//
// Renders the gate-A A2 actions bar (status pill, Force Run, Pause/Resume,
// Chat) with injected props instead of live API hooks.  Mirrors the DOM
// shape of ButlerDetailActions so visual reviewers and a11y scanners see
// the real element structure.
// ---------------------------------------------------------------------------

interface ShellProps {
  status: string;
  loading?: boolean;
  isPaused?: boolean;
  pauseDisabled?: boolean;
}

function StatusPill({ status }: { status: string }) {
  switch (status) {
    case "ok":
      return (
        <span
          className="inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium bg-emerald-600 text-white"
          data-testid="butler-status-pill"
          role="status"
          aria-label="Butler status: Up"
        >
          Up
        </span>
      );
    case "degraded":
      return (
        <span
          className="inline-flex items-center rounded-full border border-amber-500 px-2.5 py-0.5 text-xs font-medium text-amber-600"
          data-testid="butler-status-pill"
          role="status"
          aria-label="Butler status: Degraded"
        >
          Degraded
        </span>
      );
    case "error":
    case "down":
      return (
        <span
          className="inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium bg-red-600 text-white"
          data-testid="butler-status-pill"
          role="status"
          aria-label="Butler status: Down"
        >
          Down
        </span>
      );
    default:
      return (
        <span
          className="inline-flex items-center rounded-full bg-gray-200 px-2.5 py-0.5 text-xs font-medium text-gray-700"
          data-testid="butler-status-pill"
          role="status"
          aria-label={`Butler status: ${status}`}
        >
          {status}
        </span>
      );
  }
}

function ControlledActionsShell({
  status,
  loading = false,
  isPaused = false,
  pauseDisabled = false,
}: ShellProps) {
  return (
    <div
      className="flex items-center gap-2"
      data-testid="butler-detail-actions"
    >
      <StatusPill status={loading ? "unknown" : status} />

      <button
        type="button"
        className="rounded border px-3 py-1 text-sm"
        data-testid="butler-force-run"
        disabled={loading}
        aria-label="Force run butler"
      >
        {loading ? "Loading…" : "Force Run"}
      </button>

      <button
        type="button"
        className="rounded border px-3 py-1 text-sm"
        data-testid="butler-pause"
        disabled={pauseDisabled}
        aria-label={isPaused ? "Resume butler" : "Pause butler"}
      >
        {isPaused ? "Resume" : "Pause"}
      </button>

      <button
        type="button"
        className="rounded border px-3 py-1 text-sm"
        aria-label="Open chat panel for general"
      >
        Chat
      </button>
    </div>
  );
}

function PageHeading({ name }: { name: string }) {
  return (
    <h1 style={{ fontSize: "1.25rem", fontWeight: 700, marginBottom: "0.5rem" }}>
      {name}
    </h1>
  );
}

// ---------------------------------------------------------------------------
// Stories
// ---------------------------------------------------------------------------

/** Default: butler loaded with status=ok, active in registry. */
export const Default: Story = () => (
  <StoryWrapper>
    <main aria-label="Butler detail: general">
      <PageHeading name="general" />
      <ControlledActionsShell status="ok" />
    </main>
  </StoryWrapper>
);
Default.storyName = "Default (status=ok)";

/** Loading: data not yet fetched — spinner/placeholder state. */
export const LoadingState: Story = () => (
  <StoryWrapper>
    <main aria-label="Butler detail: general">
      <PageHeading name="general" />
      <div aria-label="Loading butler data" role="status" style={{ color: "#888" }}>
        Loading butler…
      </div>
    </main>
  </StoryWrapper>
);
LoadingState.storyName = "Loading";

/** Error: API call failed, no cached data available. */
export const ErrorState: Story = () => (
  <StoryWrapper>
    <main aria-label="Butler detail: general">
      <PageHeading name="general" />
      <div role="alert" aria-live="assertive" style={{ color: "#dc2626" }}>
        Something went wrong: Failed to fetch butler data.
      </div>
    </main>
  </StoryWrapper>
);
ErrorState.storyName = "Error (fetch failed)";

/** Status ok / online: butler is healthy and responsive. */
export const StatusOk: Story = () => (
  <StoryWrapper>
    <main aria-label="Butler detail: general">
      <PageHeading name="general" />
      <ControlledActionsShell status="ok" />
    </main>
  </StoryWrapper>
);
StatusOk.storyName = "Status: ok / online";

/** Status degraded: butler running but with errors or slow response. */
export const StatusDegraded: Story = () => (
  <StoryWrapper>
    <main aria-label="Butler detail: general">
      <PageHeading name="general" />
      <ControlledActionsShell status="degraded" />
    </main>
  </StoryWrapper>
);
StatusDegraded.storyName = "Status: degraded";

/** Status error / down: butler unreachable or crashed. */
export const StatusError: Story = () => (
  <StoryWrapper>
    <main aria-label="Butler detail: general">
      <PageHeading name="general" />
      <ControlledActionsShell status="error" />
    </main>
  </StoryWrapper>
);
StatusError.storyName = "Status: error / down";

/** Status waiting: butler is starting up or paused (catch-all state). */
export const StatusWaiting: Story = () => (
  <StoryWrapper>
    <main aria-label="Butler detail: general">
      <PageHeading name="general" />
      <ControlledActionsShell status="waiting" />
    </main>
  </StoryWrapper>
);
StatusWaiting.storyName = "Status: waiting";
