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
