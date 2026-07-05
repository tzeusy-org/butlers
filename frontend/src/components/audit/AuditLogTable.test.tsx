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
    metadata: null,
    result: null,
    error: null,
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

describe("AuditLogTable -- action pivot (bu-qvnce.13)", () => {
  it("links the action cell to /audit-log?action=<action>", () => {
    const html = render([entry({ action: "model.priority" })]);
    expect(html).toContain('href="/audit-log?action=model.priority"');
  });

  it("URI-encodes action values", () => {
    const html = render([entry({ action: "credential set" })]);
    expect(html).toContain('href="/audit-log?action=credential%20set"');
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

describe("AuditLogTable -- Outcome column (JARVIS audit move 6)", () => {
  it("renders a green Success badge for result=success", () => {
    const html = render([entry({ result: "success" })]);
    expect(html).toContain('data-testid="outcome-success"');
    expect(html).toContain("Success");
  });

  it("renders a red Error badge for result=error", () => {
    const html = render([entry({ result: "error" })]);
    expect(html).toContain('data-testid="outcome-error"');
    expect(html).toContain("Error");
  });

  it("renders an Unknown badge when result is null (pre-core_122 rows)", () => {
    const html = render([entry({ result: null })]);
    expect(html).toContain('data-testid="outcome-unknown"');
    expect(html).toContain("Unknown");
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

  it("renders the detail row immediately adjacent to the expanded row, not appended after every row", async () => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => {
      root.render(
        <MemoryRouter>
          <AuditLogTable
            entries={[
              entry({ id: 1, actor: "finance", note: "first row note" }),
              entry({ id: 2, actor: "health", note: "second row note" }),
            ]}
            isLoading={false}
            isError={false}
          />
        </MemoryRouter>,
      );
    });

    const summaryRows = container.querySelectorAll('[data-testid="audit-log-row"]');
    expect(summaryRows.length).toBe(2);

    // Expand the SECOND row.
    act(() => {
      summaryRows[1].dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    // The detail row must be the second row's very next sibling -- not
    // appended once at the bottom of the table regardless of which row
    // was expanded.
    const detailRow = summaryRows[1].nextElementSibling;
    expect(detailRow?.getAttribute("data-testid")).toBe("audit-log-detail-row");
    expect(detailRow?.textContent).toContain("second row note");
    expect(detailRow?.textContent).not.toContain("first row note");
  });

  it("links a result=error row with an error message to the Issues feed", async () => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => {
      root.render(
        <MemoryRouter>
          <AuditLogTable
            entries={[
              entry({
                id: 9,
                actor: "finance",
                result: "error",
                error: "OAuth token expired\nstack trace line 2",
              }),
            ]}
            isLoading={false}
            isError={false}
          />
        </MemoryRouter>,
      );
    });

    const row = container.querySelector('[data-testid="audit-log-row"]');
    act(() => {
      row!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    const issuesLink = container.querySelector(
      '[data-testid="audit-log-issues-link"]',
    ) as HTMLAnchorElement;
    expect(issuesLink).toBeTruthy();
    expect(issuesLink.getAttribute("href")).toBe(
      `/issues?q=${encodeURIComponent("OAuth token expired")}`,
    );
  });

  it("does not render an Issues link for a success row", async () => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => {
      root.render(
        <MemoryRouter>
          <AuditLogTable
            entries={[entry({ id: 11, result: "success" })]}
            isLoading={false}
            isError={false}
          />
        </MemoryRouter>,
      );
    });

    const row = container.querySelector('[data-testid="audit-log-row"]');
    act(() => {
      row!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(container.querySelector('[data-testid="audit-log-issues-link"]')).toBeNull();
  });
});
