// @vitest-environment jsdom
/**
 * Tests for AuditLogTable -- identifier pivots (bu-86c4c.4, JARVIS audit
 * move 2b: drill-down sweep).
 *
 * Every actor cell links to /audit-log?actor=<actor> (the page's own
 * filter bar already understands ?actor=). A target cell links out to its
 * owning surface when the scheme is recognised (butler:, u:); unrecognised
 * schemes (e.g. rule:<id>, which has no per-rule deep link yet) render as
 * plain text rather than a misleading link.
 */

import { act } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createRoot, type Root } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";

import AuditLogTable from "./AuditLogTable";
import type { AuditLogEntry } from "@/api/types";

vi.mock("@/components/ui/time", () => ({
  Time: ({ value }: { value: string }) => <time dateTime={value}>{value}</time>,
}));

function entry(overrides: Partial<AuditLogEntry> = {}): AuditLogEntry {
  return {
    id: 1,
    ts: "2026-07-01T12:00:00.000Z",
    actor: "owner",
    action: "model.priority",
    target: null,
    note: null,
    ip: null,
    request_id: null,
    ...overrides,
  };
}

function render(entries: AuditLogEntry[]): string {
  return renderToStaticMarkup(
    <MemoryRouter>
      <AuditLogTable entries={entries} isLoading={false} isError={false} />
    </MemoryRouter>,
  );
}

describe("AuditLogTable -- actor pivot", () => {
  it("links the actor cell to /audit-log?actor=<actor>", () => {
    const html = render([entry({ actor: "finance" })]);
    expect(html).toContain('href="/audit-log?actor=finance"');
  });

  it("URI-encodes actor values", () => {
    const html = render([entry({ actor: "qa patrol" })]);
    expect(html).toContain('href="/audit-log?actor=qa%20patrol"');
  });
});

describe("AuditLogTable -- target pivot", () => {
  it("links a butler: target to /butlers/:name", () => {
    const html = render([entry({ target: "butler:qa" })]);
    expect(html).toContain('href="/butlers/qa"');
  });

  it("links a u: (credential) target to the secrets passport focus route", () => {
    const html = render([entry({ target: "u:google" })]);
    expect(html).toContain('href="/secrets?focus=u%3Agoogle"');
  });

  it("renders an unrecognised scheme (e.g. rule:) as plain text, not a link", () => {
    const html = render([entry({ target: "rule:42" })]);
    expect(html).toContain("rule:42");
    // No anchor should target a page that doesn't understand this predicate.
    expect(html).not.toMatch(/<a[^>]*>rule:42<\/a>/);
  });

  it("renders the em-dash placeholder (no link) when target is absent", () => {
    const html = render([entry({ target: null })]);
    expect(html).toContain("—");
  });
});

describe("AuditLogTable -- expanded detail panel pivots", () => {
  let container: HTMLElement;
  let root: Root;

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
  });

  it("also links actor and butler: target inside the expanded detail row", async () => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => {
      root.render(
        <MemoryRouter>
          <AuditLogTable
            entries={[entry({ id: 7, actor: "health", target: "butler:health" })]}
            isLoading={false}
            isError={false}
          />
        </MemoryRouter>,
      );
    });

    // Expand the row.
    const row = container.querySelector("tr");
    act(() => {
      row!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    const links = Array.from(container.querySelectorAll("a")).map((a) => a.getAttribute("href"));
    expect(links).toContain("/audit-log?actor=health");
    expect(links).toContain("/butlers/health");
  });

  it("clicking the actor link does not also toggle the row's expanded state", async () => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => {
      root.render(
        <MemoryRouter>
          <AuditLogTable
            entries={[entry({ id: 3, actor: "finance", note: "detail note" })]}
            isLoading={false}
            isError={false}
          />
        </MemoryRouter>,
      );
    });

    const actorLink = container.querySelector('a[href^="/audit-log?actor="]') as HTMLAnchorElement;
    act(() => {
      actorLink.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    // The detail panel (which renders the row's `note`) must not appear --
    // the click on the nested link must not also toggle row expansion.
    expect(container.textContent).not.toContain("detail note");
  });
});
