// @vitest-environment jsdom
/**
 * Tests for AttentionList -- rule-separated attention rows.
 *
 * Covers:
 * - Empty state ("Nothing waiting.")
 * - Ordinary rows with an href action
 * - Source-error rows (bu-86c4c.2 -- truth amnesty): role="alert" lives on
 *   the inner title+detail column, NOT the outer row, so the ARIA list
 *   contract (every direct child of role="list" is a listitem) stays intact;
 *   a Retry button is offered and wired when onRetry is provided.
 */

import { act } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createRoot, type Root } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";

import { AttentionList, type AttentionListItem } from "./AttentionList";

function render(items: AttentionListItem[]): string {
  return renderToStaticMarkup(
    <MemoryRouter>
      <AttentionList items={items} />
    </MemoryRouter>,
  );
}

describe("AttentionList", () => {
  it("renders the empty state when items is empty", () => {
    const html = render([]);
    expect(html).toContain("Nothing waiting.");
    expect(html).not.toContain('role="list"');
  });

  it("renders an ordinary row with role=listitem and a view link", () => {
    const html = render([
      { id: "1", severity: "high", title: "Stale butler", detail: "No heartbeat", href: "/butlers/finance" },
    ]);
    expect(html).toContain('role="listitem"');
    expect(html).not.toContain('role="alert"');
    expect(html).toContain('href="/butlers/finance"');
  });

  it("source-error row: role=alert lives on the inner column, role=listitem stays on the row (bu-86c4c.2)", () => {
    const html = render([
      {
        id: "issues:source-error",
        severity: "high",
        title: "Issues feed unavailable",
        detail: "Could not load recent issues.",
        href: null,
        isSourceError: true,
      },
    ]);
    expect(html).toContain('role="listitem"');
    expect(html).toContain('role="alert"');
    expect(html).toContain("Issues feed unavailable");
  });

  it("renders a Retry button for a source-error row with onRetry and no href", () => {
    const html = render([
      {
        id: "health:insights:source-error",
        severity: "high",
        title: "Health signals unavailable",
        detail: "Could not load the attention index.",
        href: null,
        isSourceError: true,
        onRetry: () => {},
      },
    ]);
    expect(html).toContain("Retry");
  });

  it("does not render a Retry button or arrow when neither href nor onRetry is set", () => {
    const html = render([
      {
        id: "no-action",
        severity: "high",
        title: "Signal unavailable",
        isSourceError: true,
      },
    ]);
    expect(html).not.toContain("Retry");
    expect(html).not.toContain("<a ");
  });
});

describe("AttentionList -- Retry click", () => {
  let container: HTMLElement;
  let root: Root;

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
  });

  it("calls onRetry when the Retry button is clicked", async () => {
    const onRetry = vi.fn();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => {
      root.render(
        <MemoryRouter>
          <AttentionList
            items={[
              {
                id: "health:insights:source-error",
                severity: "high",
                title: "Health signals unavailable",
                href: null,
                isSourceError: true,
                onRetry,
              },
            ]}
          />
        </MemoryRouter>,
      );
    });
    const retryBtn = container.querySelector("button");
    expect(retryBtn?.textContent).toBe("Retry");
    act(() => {
      retryBtn!.click();
    });
    expect(onRetry).toHaveBeenCalledOnce();
  });
});

// ---------------------------------------------------------------------------
// Inline approve/deny/defer verbs (bu-86c4c.14 -- Act loop / hot queue):
// approve/deny/defer executable from the dashboard's attention list without
// leaving the pane.
// ---------------------------------------------------------------------------

describe("AttentionList -- inline approve/deny/defer verbs (bu-86c4c.14)", () => {
  let container: HTMLElement | undefined;
  let root: Root | undefined;

  afterEach(() => {
    if (root) {
      act(() => {
        root!.unmount();
      });
    }
    container?.remove();
    container = undefined;
    root = undefined;
  });

  function renderLive(items: AttentionListItem[]) {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    const r = root;
    act(() => {
      r.render(
        <MemoryRouter>
          <AttentionList items={items} />
        </MemoryRouter>,
      );
    });
  }

  function findButton(label: string): HTMLButtonElement | undefined {
    return Array.from(container!.querySelectorAll("button")).find(
      (b) => b.textContent?.trim() === label,
    );
  }

  it("renders verb-labeled Approve/Deny/Defer buttons when handlers are provided", () => {
    const html = renderToStaticMarkup(
      <MemoryRouter>
        <AttentionList
          items={[
            {
              id: "approvals:a1",
              severity: "medium",
              title: "send email",
              detail: "general · awaiting decision",
              href: "/approvals/a1",
              onApprove: () => {},
              onDeny: () => {},
              onDefer: () => {},
            },
          ]}
        />
      </MemoryRouter>,
    );
    expect(html).toContain(">Approve<");
    expect(html).toContain(">Deny<");
    expect(html).toContain(">Defer<");
    // The drill-down link survives alongside the inline verbs.
    expect(html).toContain('href="/approvals/a1"');
  });

  it("calls onApprove/onDeny/onDefer when their buttons are clicked", () => {
    const onApprove = vi.fn();
    const onDeny = vi.fn();
    const onDefer = vi.fn();
    renderLive([
      {
        id: "approvals:a1",
        severity: "medium",
        title: "send email",
        detail: "general",
        href: "/approvals/a1",
        onApprove,
        onDeny,
        onDefer,
      },
    ]);

    act(() => {
      findButton("Approve")!.click();
    });
    expect(onApprove).toHaveBeenCalledOnce();

    act(() => {
      findButton("Deny")!.click();
    });
    expect(onDeny).toHaveBeenCalledOnce();

    act(() => {
      findButton("Defer")!.click();
    });
    expect(onDefer).toHaveBeenCalledOnce();
  });

  it("disables and relabels only the pending verb's own button, not its siblings", () => {
    renderLive([
      {
        id: "approvals:a1",
        severity: "medium",
        title: "send email",
        href: "/approvals/a1",
        onApprove: () => {},
        onDeny: () => {},
        approvePending: true,
      },
    ]);

    const approveBtn = findButton("Approving…");
    expect(approveBtn).toBeDefined();
    expect(approveBtn?.disabled).toBe(true);

    const denyBtn = findButton("Deny");
    expect(denyBtn).toBeDefined();
    expect(denyBtn?.disabled).toBe(false);
  });

  it("renders only the verbs that have handlers wired (no dead buttons)", () => {
    renderLive([
      {
        id: "approvals:a1",
        severity: "medium",
        title: "send email",
        href: "/approvals/a1",
        onApprove: () => {},
      },
    ]);

    expect(findButton("Approve")).toBeDefined();
    expect(findButton("Deny")).toBeUndefined();
    expect(findButton("Defer")).toBeUndefined();
  });

  it("falls back to the plain arrow link when no verb handlers are present", () => {
    const html = render([
      {
        id: "approvals:more",
        severity: "low",
        title: "3 more pending approvals",
        href: "/approvals",
      },
    ]);
    expect(html).not.toContain(">Approve<");
    expect(html).toContain('href="/approvals"');
  });
});

// ---------------------------------------------------------------------------
// Full-row drill-down (bu-86c4c.4 -- JARVIS audit move 2b): a row without its
// own inline actions is a real <a> covering the whole row, not just the 16px
// trailing arrow glyph -- cmd/middle-click and screen-reader "link"
// announcement work from anywhere in the row.
// ---------------------------------------------------------------------------

describe("AttentionList -- full-row drill-down (bu-86c4c.4)", () => {
  it("wraps the entire row (title + detail) in a single real <a>, not just the arrow", () => {
    const html = render([
      {
        id: "runtime:finance:stale",
        severity: "high",
        title: "finance heartbeat is stale",
        detail: "Last heartbeat 20m ago",
        href: "/butlers/finance",
      },
    ]);
    const anchorMatch = html.match(
      /<a[^>]*href="\/butlers\/finance"[^>]*>([\s\S]*?)<\/a>/,
    );
    expect(anchorMatch).not.toBeNull();
    expect(anchorMatch![1]).toContain("finance heartbeat is stale");
    expect(anchorMatch![1]).toContain("Last heartbeat 20m ago");
  });

  it("keeps role=listitem on the anchor itself (ARIA list contract)", () => {
    const html = render([
      { id: "1", severity: "high", title: "Stale butler", href: "/butlers/finance" },
    ]);
    expect(html).toMatch(/<a[^>]*role="listitem"[^>]*href="\/butlers\/finance"/);
  });

  it("does not wrap the row in an <a> when inline actions are present (invalid nested-interactive HTML)", () => {
    const html = render([
      {
        id: "approvals:a1",
        severity: "medium",
        title: "send email",
        href: "/approvals/a1",
        onApprove: () => {},
      },
    ]);
    // The row itself must not be an anchor -- only the small trailing link
    // (kept for the arrow) may be one, alongside the Approve <button>.
    expect(html).not.toMatch(/<a[^>]*role="listitem"/);
    expect(html).toContain(">Approve<");
  });
});
