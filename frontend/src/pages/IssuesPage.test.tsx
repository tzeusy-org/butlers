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

function setupDefaults(issues: Issue[] = []) {
  vi.mocked(useIssues).mockReturnValue({
    data: { data: issues, meta: {} },
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
