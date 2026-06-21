// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import { render, fireEvent, createEvent, cleanup } from "@testing-library/react";

import type { SessionSummary } from "@/api/types";
import { SessionTable } from "@/components/sessions/SessionTable";

afterEach(cleanup);

function makeSession(overrides: Partial<SessionSummary> = {}): SessionSummary {
  return {
    id: "sess-abc123",
    butler: "health",
    prompt: "Summarize today's routing failures",
    trigger_source: "telegram",
    // null so the only role=button in the row is the row itself.
    request_id: null,
    success: true,
    started_at: "2026-03-12T00:00:00Z",
    completed_at: "2026-03-12T00:00:02Z",
    duration_ms: 2000,
    input_tokens: 100,
    output_tokens: 200,
    model: null,
    complexity: null,
    ...overrides,
  };
}

describe("SessionTable keyboard accessibility", () => {
  it("exposes interactive rows as focusable role=button", () => {
    const { getByRole } = render(
      <SessionTable sessions={[makeSession()]} isLoading={false} onSessionClick={vi.fn()} />,
    );
    const row = getByRole("button");
    expect(row.getAttribute("tabindex")).toBe("0");
    expect(row.getAttribute("aria-label")).toContain("health");
  });

  it("opens the drawer when Enter is pressed on a row", () => {
    const onSessionClick = vi.fn();
    const { getByRole } = render(
      <SessionTable sessions={[makeSession()]} isLoading={false} onSessionClick={onSessionClick} />,
    );
    fireEvent.keyDown(getByRole("button"), { key: "Enter" });
    expect(onSessionClick).toHaveBeenCalledTimes(1);
  });

  it("opens the drawer and suppresses page scroll when Space is pressed on a row", () => {
    const onSessionClick = vi.fn();
    const { getByRole } = render(
      <SessionTable sessions={[makeSession()]} isLoading={false} onSessionClick={onSessionClick} />,
    );
    const row = getByRole("button");
    // Space must call preventDefault (suppress native page scroll) — the
    // load-bearing difference from the Enter branch.
    const ev = createEvent.keyDown(row, { key: " " });
    fireEvent(row, ev);
    expect(onSessionClick).toHaveBeenCalledTimes(1);
    expect(ev.defaultPrevented).toBe(true);
  });

  it("does not make rows interactive when no click handler is supplied", () => {
    const { queryByRole } = render(
      <SessionTable sessions={[makeSession()]} isLoading={false} />,
    );
    expect(queryByRole("button")).toBeNull();
  });
});
