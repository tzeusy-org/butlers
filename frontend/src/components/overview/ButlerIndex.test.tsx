// @vitest-environment jsdom
/**
 * Tests for ButlerIndex -- the dashboard's fleet roster.
 *
 * bu-86c4c.4 (JARVIS audit move 2b -- drill-down sweep): rows were fully
 * inert (no link, no focusable element). Every row is now a real
 * react-router Link to /butlers/:name so cmd-click/middle-click open a new
 * tab and the row participates in normal browser navigation, not just an
 * onClick handler.
 */

import { cleanup, render as renderDom, screen } from "@testing-library/react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it } from "vitest";

import { ButlerIndex } from "./ButlerIndex";
import type { OverviewButlerIndexRow } from "./model";

function row(overrides: Partial<OverviewButlerIndexRow> = {}): OverviewButlerIndexRow {
  return {
    name: "finance",
    status: "ok",
    sessions24h: 3,
    costUsd: 0.42,
    lastSessionAt: "2026-07-01T12:00:00.000Z",
    activeSessionCount: 0,
    heartbeatAgeSeconds: 30,
    runtimeState: "healthy",
    needsAttention: false,
    ...overrides,
  };
}

function render(butlers: OverviewButlerIndexRow[], butlersError = false): string {
  return renderToStaticMarkup(
    <MemoryRouter>
      <ButlerIndex butlers={butlers} butlersError={butlersError} />
    </MemoryRouter>,
  );
}

afterEach(cleanup);

describe("ButlerIndex", () => {
  it("renders each butler row as a real <a> to /butlers/:name (not a div-onClick)", () => {
    const html = render([row({ name: "finance" })]);
    expect(html).toContain('href="/butlers/finance"');
    // The whole row (name + activity + last-seen) is inside the anchor --
    // not just a trailing glyph.
    const anchorMatch = html.match(
      /<a[^>]*href="\/butlers\/finance"[^>]*>([\s\S]*?)<\/a>/,
    );
    expect(anchorMatch).not.toBeNull();
    expect(anchorMatch![1]).toContain("finance");
  });

  it("URI-encodes butler names with special characters", () => {
    const html = render([row({ name: "qa/patrol" })]);
    expect(html).toContain('href="/butlers/qa%2Fpatrol"');
  });

  it("keeps each row both a list item and a native link", () => {
    renderDom(
      <MemoryRouter>
        <ButlerIndex butlers={[row({ name: "finance" })]} />
      </MemoryRouter>,
    );

    const list = screen.getByRole("list", { name: "Operations" });
    const item = screen.getByRole("listitem");
    const link = screen.getByRole("link", { name: "View finance" });

    expect(item.parentElement).toBe(list);
    expect(item.contains(link)).toBe(true);
  });

  it("gives every row an accessible name via aria-label", () => {
    const html = render([row({ name: "finance" })]);
    expect(html).toContain('aria-label="View finance"');
  });

  it("renders multiple rows, each linking to its own butler", () => {
    const html = render([row({ name: "finance" }), row({ name: "health" })]);
    expect(html).toContain('href="/butlers/finance"');
    expect(html).toContain('href="/butlers/health"');
  });

  it("renders the degraded source-error state (no link) when butlersError is set and the list is empty", () => {
    const html = render([], true);
    expect(html).toContain("Butler health source unavailable.");
    expect(html).not.toContain("<a ");
  });
});
