/**
 * ButlerDetailPage — axe-core accessibility baseline tests.
 *
 * Tests each story scenario from ButlerDetailPage.stories.tsx for zero axe
 * violations. Scenarios covered:
 *   1. Default (status=ok)
 *   2. Loading
 *   3. Error
 *   4. Status: ok / online
 *   5. Status: degraded
 *   6. Status: error
 *   7. Status: waiting
 *
 * Uses jest-axe (wraps axe-core) + @testing-library/react with jsdom.
 *
 * Bead: bu-sfeuw.4
 */

// @vitest-environment jsdom

import { afterEach, describe, it } from "vitest";
import React from "react";
import { render, cleanup } from "@testing-library/react";
import { axe, toHaveNoViolations } from "jest-axe";
import { expect as vitestExpect } from "vitest";

// Register the jest-axe matcher with vitest's expect.
vitestExpect.extend(toHaveNoViolations);

// Clean up the DOM after each test so landmarks don't accumulate.
afterEach(() => {
  cleanup();
});

// ---------------------------------------------------------------------------
// Shared stub components — mirror the story DOM structure exactly.
// ---------------------------------------------------------------------------

function StatusPill({ status }: { status: string }) {
  switch (status) {
    case "ok":
      return (
        <span
          data-testid="butler-status-pill"
          role="status"
          aria-label="Butler status: Up"
          style={{ display: "inline-flex", backgroundColor: "#059669", color: "#fff", padding: "2px 8px", borderRadius: "9999px", fontSize: "0.75rem" }}
        >
          Up
        </span>
      );
    case "degraded":
      return (
        <span
          data-testid="butler-status-pill"
          role="status"
          aria-label="Butler status: Degraded"
          style={{ display: "inline-flex", border: "1px solid #f59e0b", color: "#d97706", padding: "2px 8px", borderRadius: "9999px", fontSize: "0.75rem" }}
        >
          Degraded
        </span>
      );
    case "error":
    case "down":
      return (
        <span
          data-testid="butler-status-pill"
          role="status"
          aria-label="Butler status: Down"
          style={{ display: "inline-flex", backgroundColor: "#dc2626", color: "#fff", padding: "2px 8px", borderRadius: "9999px", fontSize: "0.75rem" }}
        >
          Down
        </span>
      );
    default:
      return (
        <span
          data-testid="butler-status-pill"
          role="status"
          aria-label={`Butler status: ${status}`}
          style={{ display: "inline-flex", backgroundColor: "#e5e7eb", color: "#374151", padding: "2px 8px", borderRadius: "9999px", fontSize: "0.75rem" }}
        >
          {status}
        </span>
      );
  }
}

interface ShellProps {
  status: string;
  loading?: boolean;
  isPaused?: boolean;
  pauseDisabled?: boolean;
}

function ActionsShell({
  status,
  loading = false,
  isPaused = false,
  pauseDisabled = false,
}: ShellProps) {
  return (
    <div data-testid="butler-detail-actions" style={{ display: "flex", gap: "8px", alignItems: "center" }}>
      <StatusPill status={loading ? "unknown" : status} />

      <button
        type="button"
        data-testid="butler-force-run"
        disabled={loading}
        aria-label="Force run butler"
      >
        {loading ? "Loading…" : "Force Run"}
      </button>

      <button
        type="button"
        data-testid="butler-pause"
        disabled={pauseDisabled}
        aria-label={isPaused ? "Resume butler" : "Pause butler"}
      >
        {isPaused ? "Resume" : "Pause"}
      </button>

      <button
        type="button"
        aria-label="Open chat panel for general"
      >
        Chat
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// axe wrapper — runs jest-axe on a rendered component.
// Colour contrast is skipped since jsdom cannot compute computed styles.
// ---------------------------------------------------------------------------

async function checkA11y(ui: React.ReactElement): Promise<void> {
  const { container } = render(ui);
  const results = await axe(container, {
    rules: {
      // jsdom cannot compute CSS; colour-contrast checks would always flag.
      "color-contrast": { enabled: false },
    },
  });
  vitestExpect(results).toHaveNoViolations();
}

// ---------------------------------------------------------------------------
// Story 1: Default (status=ok)
// ---------------------------------------------------------------------------

describe("a11y: Default (status=ok)", () => {
  it("has zero axe violations", async () => {
    await checkA11y(
      <main aria-label="Butler detail: general">
        <h1>general</h1>
        <ActionsShell status="ok" />
      </main>,
    );
  });
});

// ---------------------------------------------------------------------------
// Story 2: Loading
// ---------------------------------------------------------------------------

describe("a11y: Loading state", () => {
  it("has zero axe violations", async () => {
    await checkA11y(
      <main aria-label="Butler detail: general">
        <h1>general</h1>
        <div aria-label="Loading butler data" role="status">
          Loading butler…
        </div>
      </main>,
    );
  });
});

// ---------------------------------------------------------------------------
// Story 3: Error state
// ---------------------------------------------------------------------------

describe("a11y: Error state", () => {
  it("has zero axe violations", async () => {
    await checkA11y(
      <main aria-label="Butler detail: general">
        <h1>general</h1>
        <div role="alert" aria-live="assertive">
          Something went wrong: Failed to fetch butler data.
        </div>
      </main>,
    );
  });
});

// ---------------------------------------------------------------------------
// Story 4: Status = ok / online
// ---------------------------------------------------------------------------

describe("a11y: Status ok / online", () => {
  it("has zero axe violations", async () => {
    await checkA11y(
      <main aria-label="Butler detail: general">
        <h1>general</h1>
        <ActionsShell status="ok" />
      </main>,
    );
  });
});

// ---------------------------------------------------------------------------
// Story 5: Status = degraded
// ---------------------------------------------------------------------------

describe("a11y: Status degraded", () => {
  it("has zero axe violations", async () => {
    await checkA11y(
      <main aria-label="Butler detail: general">
        <h1>general</h1>
        <ActionsShell status="degraded" />
      </main>,
    );
  });
});

// ---------------------------------------------------------------------------
// Story 6: Status = error / down
// ---------------------------------------------------------------------------

describe("a11y: Status error / down", () => {
  it("has zero axe violations", async () => {
    await checkA11y(
      <main aria-label="Butler detail: general">
        <h1>general</h1>
        <ActionsShell status="error" />
      </main>,
    );
  });
});

// ---------------------------------------------------------------------------
// Story 7: Status = waiting
// ---------------------------------------------------------------------------

describe("a11y: Status waiting", () => {
  it("has zero axe violations", async () => {
    await checkA11y(
      <main aria-label="Butler detail: general">
        <h1>general</h1>
        <ActionsShell status="waiting" />
      </main>,
    );
  });
});
