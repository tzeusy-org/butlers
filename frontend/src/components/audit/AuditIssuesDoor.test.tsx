// @vitest-environment jsdom
/**
 * The exact Audit -> Issues evidence door (bu-6jv4m.3).
 *
 * The old affordance was an unconditional `View in Issues ->` link to
 * `/issues?q=<first line of the error>`. It asserted three things it had not
 * established: that a group exists, that the client-side text guess matches the
 * server's grouping normalization, and that the destination's default window
 * contains it. When any of those was wrong the user landed on an empty Issues
 * page -- which reads as "nothing is wrong".
 *
 * These tests pin the replacement's THREE distinguishable states. The two
 * non-success states are the point: a resolved absence and an unavailable
 * lookup must never render the same way, and neither may render as calm.
 */

import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";

const useAuditIssueGroup = vi.fn();
vi.mock("@/hooks/use-issues", () => ({
  useAuditIssueGroup: (...args: unknown[]) => useAuditIssueGroup(...args),
}));

import { AuditIssuesDoor } from "./AuditIssuesDoor";
import type { AuditIssueGroupRef } from "@/api/types";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

let container: HTMLDivElement;
let root: Root;

function mount(node: React.ReactNode) {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => {
    root.render(<MemoryRouter>{node}</MemoryRouter>);
  });
}

afterEach(() => {
  act(() => root?.unmount());
  container?.remove();
  vi.clearAllMocks();
});

beforeEach(() => {
  useAuditIssueGroup.mockReset();
});

function groupRef(overrides: Partial<AuditIssueGroupRef> = {}): AuditIssueGroupRef {
  return {
    audit_id: 42,
    window: "24h",
    found: true,
    issue_key: "audit_error_group:0123456789abcdef",
    severity: "warning",
    error_message: "KeyError: 'access_token'",
    occurrences: 12,
    first_seen_at: "2026-08-21T09:00:00.000Z",
    last_seen_at: "2026-08-22T09:00:00.000Z",
    butlers: ["calendar"],
    issues_href: "/issues?window=24h&group=audit_error_group%3A0123456789abcdef",
    ...overrides,
  };
}

function setResult(result: Record<string, unknown>) {
  useAuditIssueGroup.mockReturnValue({
    data: undefined,
    isPending: false,
    isError: false,
    error: null,
    ...result,
  });
}

describe("AuditIssuesDoor -- found", () => {
  it("links to the server-computed group key, not a text search", () => {
    setResult({ data: { data: groupRef() } });
    mount(<AuditIssuesDoor auditId={42} />);

    const link = container.querySelector<HTMLAnchorElement>(
      '[data-testid="audit-log-issues-link"]',
    );
    expect(link).not.toBeNull();
    const href = link!.getAttribute("href") ?? "";
    expect(href).toContain("group=audit_error_group%3A0123456789abcdef");
    expect(href).toContain("window=24h");
    // The fuzzy predicate must be gone entirely.
    expect(href).not.toContain("q=");
  });

  it("names the window it resolved in so a widened lookup is visible", () => {
    setResult({
      data: {
        data: groupRef({
          window: "all",
          issues_href: "/issues?window=all&group=audit_error_group%3A0123456789abcdef",
        }),
      },
    });
    mount(<AuditIssuesDoor auditId={42} />);

    expect(container.textContent).toContain("all time");
  });

  it("shows the group's real occurrence count as evidence", () => {
    setResult({ data: { data: groupRef({ occurrences: 12 }) } });
    mount(<AuditIssuesDoor auditId={42} />);

    expect(container.textContent).toContain("12");
  });
});

describe("AuditIssuesDoor -- resolved absence", () => {
  it("states that no current group exists instead of linking to an empty page", () => {
    setResult({
      data: {
        data: groupRef({
          found: false,
          reason: "no-current-group",
          issue_key: null,
          issues_href: null,
          occurrences: null,
        }),
      },
    });
    mount(<AuditIssuesDoor auditId={42} />);

    expect(container.querySelector('[data-testid="audit-log-issues-link"]')).toBeNull();
    const note = container.querySelector('[data-testid="audit-log-issues-absent"]');
    expect(note).not.toBeNull();
    expect(note!.textContent).toContain("No current issue group");
    // Absence is scoped -- it is a statement about a window, not the fleet.
    expect(note!.textContent).toContain("24h");
  });

  it("distinguishes a non-failure row from a failure with no group", () => {
    setResult({
      data: { data: groupRef({ found: false, reason: "not-a-failure", issues_href: null }) },
    });
    mount(<AuditIssuesDoor auditId={42} />);

    const note = container.querySelector('[data-testid="audit-log-issues-absent"]');
    expect(note!.textContent).toContain("did not fail");
    expect(note!.textContent).not.toContain("No current issue group");
  });
});

describe("AuditIssuesDoor -- unavailable lookup", () => {
  it("reports the lookup as unavailable and never as an absence", () => {
    setResult({ isError: true, error: new Error("boom") });
    mount(<AuditIssuesDoor auditId={42} />);

    const degraded = container.querySelector('[data-testid="audit-log-issues-degraded"]');
    expect(degraded).not.toBeNull();
    expect(degraded!.getAttribute("role")).toBe("alert");
    expect(degraded!.textContent).toContain("unavailable");
    // Neither of the two confident renderings may appear.
    expect(container.querySelector('[data-testid="audit-log-issues-link"]')).toBeNull();
    expect(container.querySelector('[data-testid="audit-log-issues-absent"]')).toBeNull();
    expect(container.textContent).not.toContain("No current issue group");
  });

  it("renders neither a link nor an absence while the lookup is still pending", () => {
    setResult({ isPending: true });
    mount(<AuditIssuesDoor auditId={42} />);

    expect(container.querySelector('[data-testid="audit-log-issues-link"]')).toBeNull();
    expect(container.querySelector('[data-testid="audit-log-issues-absent"]')).toBeNull();
    expect(container.querySelector('[data-testid="audit-log-issues-pending"]')).not.toBeNull();
  });
});

describe("AuditIssuesDoor -- laziness", () => {
  it("only enables the lookup for the row it is rendered for", () => {
    setResult({ data: { data: groupRef() } });
    mount(<AuditIssuesDoor auditId={7} window="7d" />);

    expect(useAuditIssueGroup).toHaveBeenCalledWith(7, true, "7d");
  });
});
