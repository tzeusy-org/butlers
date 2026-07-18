// @vitest-environment jsdom
/**
 * IssuesPage — unit tests.
 *
 * Covers:
 * - ?q= deep-link (JARVIS audit move 6): a failure row on the Audit Log page
 *   links here with the first line of its error text; this page substring-
 *   filters the currently-loaded feed against it and renders a clearable chip.
 * - Occurrences drill-down wiring: expand state + useIssueOccurrences results
 *   are threaded through to IssuesPanel via props.
 */

import { describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";

import IssuesPage from "@/pages/IssuesPage";
import type { Issue } from "@/api/types";

vi.mock("@/hooks/use-issues", () => ({
  useIssues: vi.fn(),
  useIssueOccurrences: vi.fn(),
  useDismissIssue: vi.fn(),
  useUndismissIssue: vi.fn(),
}));
vi.mock("@/hooks/use-butlers", () => ({
  usePingButler: vi.fn(),
  useForceButlerTick: vi.fn(),
}));

import {
  useDismissIssue,
  useIssueOccurrences,
  useIssues,
  useUndismissIssue,
} from "@/hooks/use-issues";
import { useForceButlerTick, usePingButler } from "@/hooks/use-butlers";

function makeIssue(overrides: Partial<Issue> = {}): Issue {
  return {
    severity: "warning",
    type: "audit_error_group:oauth-token-expired",
    butler: "calendar",
    description: "OAuth token expired (calendar)",
    link: "/audit-log?result=error&actor=calendar",
    error_message: "OAuth token expired",
    occurrences: 3,
    first_seen_at: "2026-06-14T10:00:00.000Z",
    last_seen_at: "2026-06-14T11:00:00.000Z",
    butlers: ["calendar"],
    issue_key: "audit_error_group:oauth-token-expired::calendar",
    dismissed: false,
    ...overrides,
  };
}

function setupDefaults(issues: Issue[] = [], meta: Record<string, unknown> = {}) {
  vi.mocked(useIssues).mockReturnValue({
    data: { data: issues, meta },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useIssues>);
  vi.mocked(useIssueOccurrences).mockReturnValue({
    data: { data: [], meta: { total: 0, offset: 0, limit: 50, has_more: false } },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useIssueOccurrences>);
  vi.mocked(useDismissIssue).mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
  } as unknown as ReturnType<typeof useDismissIssue>);
  vi.mocked(useUndismissIssue).mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
  } as unknown as ReturnType<typeof useUndismissIssue>);
  vi.mocked(usePingButler).mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
    variables: undefined,
  } as unknown as ReturnType<typeof usePingButler>);
  vi.mocked(useForceButlerTick).mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
    variables: undefined,
  } as unknown as ReturnType<typeof useForceButlerTick>);
}

function renderPage(initialPath = "/issues"): { container: HTMLElement; root: Root } {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => {
    root.render(
      <MemoryRouter initialEntries={[initialPath]}>
        <IssuesPage />
      </MemoryRouter>,
    );
  });
  return { container, root };
}

describe("IssuesPage — ?q= deep-link filter", () => {
  it("opens the issue inbox with a labeled verdict region", () => {
    setupDefaults([makeIssue({ severity: "critical" })]);
    const { container, root } = renderPage("/issues");

    expect(container.querySelector('[aria-label="Issues verdict"]')).not.toBeNull();

    act(() => root.unmount());
    container.remove();
  });

  it("shows all issues when ?q= is absent", () => {
    setupDefaults([makeIssue(), makeIssue({ issue_key: "k2", description: "unrelated (health)" })]);
    const { container, root } = renderPage("/issues");

    expect(container.textContent).toContain("OAuth token expired");
    expect(container.textContent).toContain("unrelated (health)");
    expect(container.querySelector('[data-testid="q-filter"]')).toBeNull();

    act(() => root.unmount());
    container.remove();
  });

  it("filters to issues whose error_message matches ?q= (case-insensitive substring)", () => {
    setupDefaults([
      makeIssue(),
      makeIssue({
        issue_key: "k2",
        error_message: "Connection refused",
        description: "Connection refused (health)",
      }),
    ]);
    const { container, root } = renderPage("/issues?q=oauth%20token");

    expect(container.textContent).toContain("OAuth token expired");
    expect(container.textContent).not.toContain("Connection refused");
    expect(container.querySelector('[data-testid="q-filter-chip"]')).toBeTruthy();

    act(() => root.unmount());
    container.remove();
  });

  it("clears the ?q= filter when the chip's dismiss button is clicked", () => {
    setupDefaults([
      makeIssue(),
      makeIssue({
        issue_key: "k2",
        error_message: "Connection refused",
        description: "Connection refused (health)",
      }),
    ]);
    const { container, root } = renderPage("/issues?q=oauth");

    expect(container.textContent).not.toContain("Connection refused");

    const clearButton = container.querySelector(
      '[data-testid="q-filter-chip"] button',
    ) as HTMLButtonElement;
    act(() => {
      clearButton.click();
    });

    expect(container.textContent).toContain("Connection refused");
    expect(container.querySelector('[data-testid="q-filter"]')).toBeNull();

    act(() => root.unmount());
    container.remove();
  });
});

describe("IssuesPage — degraded feed threading (bu-tpudw.3)", () => {
  it("threads meta.sources_degraded into the panel so the all-clear is suppressed", () => {
    setupDefaults([], { sources_degraded: ["audit-groups"] });
    const { container, root } = renderPage("/issues");

    const note = container.querySelector('[data-testid="issues-feed-degraded"]');
    expect(note).toBeTruthy();
    expect(note?.textContent).toContain("audit-groups");
    expect(container.textContent).not.toContain("No issues recorded.");

    act(() => root.unmount());
    container.remove();
  });

  it("renders the honest empty state when meta carries no degraded sources", () => {
    setupDefaults([], {});
    const { container, root } = renderPage("/issues");

    expect(container.querySelector('[data-testid="issues-feed-degraded"]')).toBeNull();
    expect(container.textContent).toContain("No issues recorded.");

    act(() => root.unmount());
    container.remove();
  });
});

describe("IssuesPage — capped audit-group feed", () => {
  it("names a capped result set and suppresses the all-clear", () => {
    setupDefaults([], { truncated: true });
    const { container, root } = renderPage("/issues");

    const note = container.querySelector('[data-testid="issues-feed-truncated"]');
    expect(note).toBeTruthy();
    expect(note?.getAttribute("role")).toBe("alert");
    expect(note?.textContent).toContain("500-group cap reached");
    expect(note?.textContent).toContain("some audit-derived issues may be missing");
    expect(container.textContent).not.toContain("No issues recorded.");

    act(() => root.unmount());
    container.remove();
  });

  it("keeps the normal all-clear when the result set is not capped", () => {
    setupDefaults([], {});
    const { container, root } = renderPage("/issues");

    expect(container.querySelector('[data-testid="issues-feed-truncated"]')).toBeNull();
    expect(container.textContent).toContain("No issues recorded.");

    act(() => root.unmount());
    container.remove();
  });
});

describe("IssuesPage — occurrences drill-down wiring", () => {
  it("calls useIssueOccurrences with enabled=false until a row is expanded", () => {
    setupDefaults([makeIssue()]);
    const { root, container } = renderPage("/issues");

    const call = vi.mocked(useIssueOccurrences).mock.calls.at(-1);
    expect(call?.[0]).toBeNull();
    expect(call?.[1]).toBe(false);

    act(() => root.unmount());
    container.remove();
  });

  it("threads the active window and a 50-row default limit to useIssueOccurrences (bu-hmdqz.4)", () => {
    setupDefaults([makeIssue()]);
    const { root, container } = renderPage("/issues?window=24h");

    const call = vi.mocked(useIssueOccurrences).mock.calls.at(-1);
    expect(call?.[2]).toBe("24h");
    expect(call?.[3]).toBe(50);

    act(() => root.unmount());
    container.remove();
  });

  it("resets the occurrences limit to 50 when a row is toggled (bu-hmdqz.4)", () => {
    setupDefaults([makeIssue()]);
    const { root, container } = renderPage("/issues");

    const trigger = container.querySelector(
      '[data-testid="issue-row"] [role="button"]',
    ) as HTMLElement;
    act(() => trigger.dispatchEvent(new MouseEvent("click", { bubbles: true })));

    const call = vi.mocked(useIssueOccurrences).mock.calls.at(-1);
    expect(call?.[0]).toBe("audit_error_group:oauth-token-expired::calendar");
    expect(call?.[1]).toBe(true);
    expect(call?.[3]).toBe(50);

    act(() => root.unmount());
    container.remove();
  });

  it("passes the occurrences total through to IssuesPanel for the 'Showing X of N' count", () => {
    setupDefaults([makeIssue()]);
    vi.mocked(useIssueOccurrences).mockReturnValue({
      data: {
        data: [
          {
            id: 1,
            ts: "2026-06-14T10:30:00.000Z",
            actor: "calendar",
            action: "oauth_refresh",
            target: null,
            note: null,
            ip: null,
            request_id: null,
          },
        ],
        meta: { total: 7, offset: 0, limit: 50, has_more: true },
      },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useIssueOccurrences>);
    const { root, container } = renderPage("/issues");

    const trigger = container.querySelector(
      '[data-testid="issue-row"] [role="button"]',
    ) as HTMLElement;
    act(() => trigger.dispatchEvent(new MouseEvent("click", { bubbles: true })));

    expect(container.textContent).toContain("Showing 1 of 7");

    act(() => root.unmount());
    container.remove();
  });
});

// ---------------------------------------------------------------------------
// j/k list-triage over issue rows (bu-qvnce.11 slice 4): IssuesPage adopts
// the shared useListTriage hook extracted from ApprovalsPage's own former
// hand-rolled j/k/a/d/x implementation. Only the wiring is covered here --
// useListTriage's own navigation/act-key mechanics are unit-tested directly
// in use-list-triage.test.tsx.
// ---------------------------------------------------------------------------

describe("IssuesPage — j/k list-triage (bu-qvnce.11 slice 4)", () => {
  function press(key: string) {
    window.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true }));
  }

  it("j selects the first issue row, moving focus onto it", () => {
    setupDefaults([makeIssue(), makeIssue({ issue_key: "k2", description: "unrelated (health)" })]);
    const { container, root } = renderPage("/issues");

    act(() => press("j"));

    const rows = container.querySelectorAll('[data-testid="issue-row"]');
    expect(rows.length).toBe(2);
    const first = rows[0] as HTMLElement;
    expect(first.getAttribute("data-issue-key")).toBe(
      document.activeElement?.getAttribute("data-issue-key"),
    );

    act(() => root.unmount());
    container.remove();
  });

  it("a acknowledges the selected row via dismiss.mutate", () => {
    const issue = makeIssue();
    setupDefaults([issue]);
    const dismissMutate = vi.fn();
    vi.mocked(useDismissIssue).mockReturnValue({
      mutate: dismissMutate,
      isPending: false,
    } as unknown as ReturnType<typeof useDismissIssue>);
    const { container, root } = renderPage("/issues");

    act(() => press("j"));
    act(() => press("a"));

    expect(dismissMutate).toHaveBeenCalledWith({
      issueKey: issue.issue_key,
      lastSeenAt: issue.last_seen_at,
    });

    act(() => root.unmount());
    container.remove();
  });

  it("a restores the selected row via undismiss.mutate in the acknowledged view", () => {
    const issue = makeIssue({ dismissed: true });
    const dismissMutate = vi.fn();
    const undismissMutate = vi.fn();
    setupDefaults([issue]);
    vi.mocked(useDismissIssue).mockReturnValue({
      mutate: dismissMutate,
      isPending: false,
    } as unknown as ReturnType<typeof useDismissIssue>);
    vi.mocked(useUndismissIssue).mockReturnValue({
      mutate: undismissMutate,
      isPending: false,
    } as unknown as ReturnType<typeof useUndismissIssue>);
    const { container, root } = renderPage("/issues");

    const showAcknowledged = Array.from(container.querySelectorAll("button")).find(
      (button) => button.textContent === "Show acknowledged",
    );
    expect(showAcknowledged).toBeTruthy();
    act(() => showAcknowledged!.click());
    act(() => press("j"));
    act(() => press("a"));

    expect(undismissMutate).toHaveBeenCalledTimes(1);
    expect(undismissMutate).toHaveBeenCalledWith(issue.issue_key);
    expect(dismissMutate).not.toHaveBeenCalled();
    expect(container.textContent).toContain("Restore selected");

    act(() => root.unmount());
    container.remove();
  });

  it("renders the footer hint strip advertising the exact bound keys", () => {
    setupDefaults([makeIssue()]);
    const { container, root } = renderPage("/issues");

    act(() => press("j"));

    expect(container.textContent).toContain("Next item");
    expect(container.textContent).toContain("Previous item");
    expect(container.textContent).toContain("Acknowledge selected");

    act(() => root.unmount());
    container.remove();
  });

  it("renders no footer hint strip when there are no issues", () => {
    setupDefaults([]);
    const { container, root } = renderPage("/issues");

    expect(container.querySelector('[aria-label="Keyboard shortcuts for this list"]')).toBeNull();

    act(() => root.unmount());
    container.remove();
  });
});
