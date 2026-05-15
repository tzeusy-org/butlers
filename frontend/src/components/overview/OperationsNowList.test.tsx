/**
 * Tests for OperationsNowList -- Operations/Now signal list.
 *
 * Covers:
 * - Compact zero state (single serif italic line when no rows)
 * - Approval, QA, notification, and activity rows render with correct labels
 * - Kind badges render correctly
 * - Click targets link to canonical routes
 * - Count badges are shown only when count > 0
 */

import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";

import { OperationsNowList } from "./OperationsNowList";
import type { OverviewNowRow } from "./model";

function render(rows: OverviewNowRow[]): string {
  return renderToStaticMarkup(
    <MemoryRouter>
      <OperationsNowList rows={rows} />
    </MemoryRouter>,
  );
}

describe("OperationsNowList", () => {
  it("renders compact zero state when rows is empty", () => {
    const html = render([]);
    expect(html).toContain("Nothing scheduled.");
    // Zero state should be a single sentence, not a list of separate items
    expect(html).not.toContain("listitem");
  });

  it("renders a pending approvals row with label and kind badge", () => {
    const html = render([
      {
        id: "now:approvals",
        kind: "approval",
        label: "3 pending approvals",
        detail: "Awaiting owner decision.",
        href: "/approvals",
        count: 3,
      },
    ]);
    expect(html).toContain("3 pending approvals");
    expect(html).toContain("approval");
    expect(html).toContain(">3<");
  });

  it("renders a QA state row with label and kind badge", () => {
    const html = render([
      {
        id: "now:qa",
        kind: "qa",
        label: "QA patrol failed",
        detail: "log scanner failed",
        href: "/qa",
      },
    ]);
    expect(html).toContain("QA patrol failed");
    expect(html).toContain("qa");
    expect(html).toContain('href="/qa"');
  });

  it("renders a notification pressure row linking to /notifications", () => {
    const html = render([
      {
        id: "now:notifications",
        kind: "notification",
        label: "2 failed notifications",
        detail: "Delivery failures are present.",
        href: "/notifications",
        count: 2,
      },
    ]);
    expect(html).toContain("2 failed notifications");
    expect(html).toContain("notif");
    expect(html).toContain('href="/notifications"');
    expect(html).toContain(">2<");
  });

  it("renders a timeline activity row linking to /timeline", () => {
    const html = render([
      {
        id: "now:activity:evt-1",
        kind: "activity",
        label: "general ran health check",
        detail: "general · session",
        href: "/timeline",
      },
    ]);
    expect(html).toContain("general ran health check");
    expect(html).toContain("activity");
    expect(html).toContain('href="/timeline"');
  });

  it("renders multiple rows without empty state", () => {
    const html = render([
      {
        id: "now:approvals",
        kind: "approval",
        label: "1 pending approval",
        detail: "Awaiting owner decision.",
        href: "/approvals",
        count: 1,
      },
      {
        id: "now:notifications",
        kind: "notification",
        label: "4 failed notifications",
        detail: "Delivery failures.",
        href: "/notifications",
        count: 4,
      },
    ]);
    expect(html).toContain("1 pending approval");
    expect(html).toContain("4 failed notifications");
    expect(html).not.toContain("Nothing scheduled.");
  });

  it("omits count badge when count is undefined", () => {
    const html = render([
      {
        id: "now:qa",
        kind: "qa",
        label: "QA patrol failed",
        detail: "error",
        href: "/qa",
        // no count
      },
    ]);
    expect(html).toContain("QA patrol failed");
    // Should not have a numeric badge
    expect(html).not.toMatch(/>[0-9]+<\/span>/);
  });

  it("renders all four kind badge labels correctly", () => {
    const rows: OverviewNowRow[] = [
      { id: "a", kind: "approval", label: "A", detail: "", href: "/approvals" },
      { id: "q", kind: "qa", label: "Q", detail: "", href: "/qa" },
      { id: "n", kind: "notification", label: "N", detail: "", href: "/notifications" },
      { id: "t", kind: "activity", label: "T", detail: "", href: "/timeline" },
    ];
    const html = render(rows);
    expect(html).toContain("approval");
    expect(html).toContain("qa");
    expect(html).toContain("notif");
    expect(html).toContain("activity");
  });

  it("renders the Now section eyebrow", () => {
    const html = render([]);
    expect(html).toContain("Now");
  });

  it("click targets link to correct canonical routes", () => {
    const rows: OverviewNowRow[] = [
      { id: "a", kind: "approval", label: "1 pending approval", detail: "", href: "/approvals" },
      { id: "q", kind: "qa", label: "QA issue", detail: "", href: "/qa" },
      { id: "n", kind: "notification", label: "1 failed notification", detail: "", href: "/notifications" },
      { id: "t", kind: "activity", label: "session ran", detail: "", href: "/timeline" },
    ];
    const html = render(rows);
    expect(html).toContain('href="/approvals"');
    expect(html).toContain('href="/qa"');
    expect(html).toContain('href="/notifications"');
    expect(html).toContain('href="/timeline"');
  });
});
