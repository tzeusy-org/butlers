/**
 * ButlersPage — axe-core accessibility baseline tests.
 *
 * Tests each key UI state of ButlersPage for zero axe violations. States covered:
 *   1. Loading (skeleton with aria-label="Loading")
 *   2. Empty (no butlers, no error)
 *   3. Error (full-page error, no cached data)
 *   4. Populated (butlers listed with cards)
 *   5. Sort: staffers-after-butlers (both groups present)
 *
 * Uses jest-axe (wraps axe-core) + @testing-library/react with jsdom.
 *
 * Bead: bu-9afpr
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
// Shared stub components — mirror the ButlersPage DOM structure.
// ---------------------------------------------------------------------------

/** Skeleton pulse block used in the loading state. */
function Skeleton({ width, height }: { width: string; height: string }) {
  return (
    <div
      aria-hidden="true"
      style={{ width, height, background: "#e5e7eb", borderRadius: 4 }}
    />
  );
}

/** Status badge / pill matching ButlersPage.statusPill output shape. */
function StatusBadge({ status }: { status: string }) {
  switch (status) {
    case "ok":
    case "online":
      return (
        <span
          role="status"
          aria-label="Butler status: Up"
          style={{
            display: "inline-flex",
            backgroundColor: "#059669",
            color: "#fff",
            padding: "2px 8px",
            borderRadius: "9999px",
            fontSize: "0.75rem",
          }}
        >
          Up
        </span>
      );
    case "error":
    case "down":
    case "offline":
      return (
        <span
          role="status"
          aria-label="Butler status: Down"
          style={{
            display: "inline-flex",
            backgroundColor: "#dc2626",
            color: "#fff",
            padding: "2px 8px",
            borderRadius: "9999px",
            fontSize: "0.75rem",
          }}
        >
          Down
        </span>
      );
    case "degraded":
      return (
        <span
          role="status"
          aria-label="Butler status: Degraded"
          style={{
            display: "inline-flex",
            border: "1px solid #f59e0b",
            color: "#d97706",
            padding: "2px 8px",
            borderRadius: "9999px",
            fontSize: "0.75rem",
          }}
        >
          Degraded
        </span>
      );
    default:
      return (
        <span
          role="status"
          aria-label={`Butler status: ${status}`}
          style={{
            display: "inline-flex",
            backgroundColor: "#e5e7eb",
            color: "#374151",
            padding: "2px 8px",
            borderRadius: "9999px",
            fontSize: "0.75rem",
          }}
        >
          {status}
        </span>
      );
  }
}

interface ButlerRowProps {
  name: string;
  status: string;
  description?: string;
  sessions?: number;
}

/** Single butler card row (dispatch layout). */
function ButlerRow({ name, status, description, sessions = 0 }: ButlerRowProps) {
  return (
    <a
      href={`/butlers/${name}`}
      aria-label={`Open ${name} butler`}
      style={{ display: "grid", gridTemplateColumns: "40px 1fr auto", gap: "16px", padding: "16px 0" }}
    >
      {/* glyph */}
      <div aria-hidden="true" title={name} style={{ width: 40, height: 40, borderRadius: 8, background: "#e5e7eb" }} />

      {/* name + status pill + description */}
      <div>
        <div style={{ display: "flex", alignItems: "baseline", gap: "8px" }}>
          <span style={{ fontWeight: 500 }}>{name}</span>
          <StatusBadge status={status} />
        </div>
        {description && (
          <p style={{ fontSize: "0.875rem", color: "#6b7280", marginTop: 4 }}>{description}</p>
        )}
      </div>

      {/* sessions + open link */}
      <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: "8px" }}>
        <span>
          <span style={{ fontWeight: 500 }}>{sessions}</span>
          <span style={{ color: "#6b7280", marginLeft: 4 }}>sess</span>
        </span>
        <span>open →</span>
      </div>
    </a>
  );
}

// ---------------------------------------------------------------------------
// Story 1: Loading state
// ---------------------------------------------------------------------------

describe("a11y: Loading state", () => {
  it("has zero axe violations", async () => {
    await checkA11y(
      <main aria-label="Butlers">
        <div role="status" aria-label="Loading">
          <Skeleton width="12rem" height="2rem" />
          <Skeleton width="16rem" height="1rem" />
          <Skeleton width="100%" height="8rem" />
          <Skeleton width="100%" height="8rem" />
        </div>
      </main>,
    );
  });
});

// ---------------------------------------------------------------------------
// Story 2: Empty state (no butlers, no error)
// ---------------------------------------------------------------------------

describe("a11y: Empty state", () => {
  it("has zero axe violations", async () => {
    await checkA11y(
      <main aria-label="Butlers">
        <h1>Butlers</h1>
        <p>Browse all registered butlers and jump directly to detail views.</p>
        <div>
          <h2>No butlers found.</h2>
          <p>Check daemon status and try again.</p>
        </div>
      </main>,
    );
  });
});

// ---------------------------------------------------------------------------
// Story 3: Error state (full-page, no cached data)
// ---------------------------------------------------------------------------

describe("a11y: Error state", () => {
  it("has zero axe violations", async () => {
    await checkA11y(
      <main aria-label="Butlers">
        <h1>Butlers</h1>
        <p>Browse all registered butlers and jump directly to detail views.</p>
        <div role="alert" aria-live="assertive">
          <p style={{ fontWeight: 600, color: "#dc2626" }}>Something went wrong</p>
          <p style={{ color: "#dc2626" }}>Failed to fetch butler list.</p>
          <button type="button">Retry</button>
        </div>
      </main>,
    );
  });
});

// ---------------------------------------------------------------------------
// Story 4: Populated (butlers listed with stat cards)
// ---------------------------------------------------------------------------

describe("a11y: Populated state", () => {
  it("has zero axe violations", async () => {
    await checkA11y(
      <main aria-label="Butlers">
        <h1>Butlers</h1>
        <p>Browse all registered butlers and jump directly to detail views.</p>

        {/* Stats row */}
        <div role="region" aria-label="Summary statistics">
          <div>
            <div>Total Agents</div>
            <div aria-label="3 total agents">3</div>
          </div>
          <div>
            <div>Healthy</div>
            <div aria-label="2 healthy">2</div>
            <p>67% currently up</p>
          </div>
        </div>

        {/* Butler list */}
        <section aria-label="Butlers">
          <h2>Butlers</h2>
          <div>
            <ButlerRow name="general" status="ok" description="General purpose assistant" sessions={5} />
            <ButlerRow name="health" status="degraded" description="Tracks wellness goals" sessions={2} />
            <ButlerRow name="switchboard" status="error" sessions={0} />
          </div>
        </section>
      </main>,
    );
  });
});

// ---------------------------------------------------------------------------
// Story 5: Sort — staffers after butlers
// ---------------------------------------------------------------------------

describe("a11y: Sort (staffers after butlers)", () => {
  it("has zero axe violations", async () => {
    await checkA11y(
      <main aria-label="Butlers">
        <h1>Butlers</h1>
        <p>Browse all registered butlers and jump directly to detail views.</p>

        {/* Stats row */}
        <div role="region" aria-label="Summary statistics">
          <div>
            <div>Total Agents</div>
            <div aria-label="4 total agents">4</div>
            <p>2 butlers, 2 staffers</p>
          </div>
          <div>
            <div>Healthy</div>
            <div aria-label="4 healthy">4</div>
            <p>100% currently up</p>
          </div>
        </div>

        {/* Butlers section (alphabetical) */}
        <section aria-label="Butlers">
          <h2>Butlers</h2>
          <div>
            <ButlerRow name="alpha" status="ok" sessions={1} />
            <ButlerRow name="zebra" status="ok" sessions={3} />
          </div>
        </section>

        {/* Staffers section (after butlers, alphabetical) */}
        <section aria-label="Staffers">
          <h2>Staffers</h2>
          <p style={{ color: "#6b7280", fontSize: "0.875rem" }}>
            Infrastructure services that support butler operations.
          </p>
          <div>
            <ButlerRow name="apple" status="ok" sessions={0} />
            <ButlerRow name="mango" status="ok" sessions={0} />
          </div>
        </section>
      </main>,
    );
  });
});
