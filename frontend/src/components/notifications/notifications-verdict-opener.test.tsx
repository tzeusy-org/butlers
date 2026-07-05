// @vitest-environment jsdom
/**
 * Tests for <NotificationsVerdictOpener> (bu-y0v0c, JARVIS pursuit move 9
 * slice 3).
 *
 * Verifies the /notifications opener composes the windowed stats response
 * into "N failed notifications in the last 24h; M from <butler>" -- and
 * honors the isError-suppression contract, including the source_available
 * degraded-mode flag (a 200 with all-zero counts must never read as a
 * truthful all-clear).
 */

import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";

import {
  NotificationsVerdictOpener,
  NOTIFICATIONS_VERDICT_WINDOW_HOURS,
} from "@/components/notifications/notifications-verdict-opener";
import type { NotificationStats } from "@/api/index.ts";

function render(ui: React.ReactElement): string {
  return renderToStaticMarkup(<MemoryRouter>{ui}</MemoryRouter>);
}

function stats(overrides: Partial<NotificationStats> = {}): NotificationStats {
  return {
    total: 0,
    sent: 0,
    failed: 0,
    by_channel: {},
    by_butler: {},
    source_available: true,
    ...overrides,
  };
}

describe("NotificationsVerdictOpener -- all clear", () => {
  it("renders the calm line when nothing failed", () => {
    const html = render(
      <NotificationsVerdictOpener stats={stats()} isLoading={false} isError={false} />,
    );
    expect(html).toContain('data-testid="notifications-verdict-all-clear"');
    expect(html).toContain(
      `No failed notifications in the last ${NOTIFICATIONS_VERDICT_WINDOW_HOURS}h.`,
    );
  });

  it("folds the sent count into the calm line", () => {
    const html = render(
      <NotificationsVerdictOpener
        stats={stats({ sent: 42 })}
        isLoading={false}
        isError={false}
      />,
    );
    expect(html).toContain("(42 sent).");
  });
});

describe("NotificationsVerdictOpener -- clauses", () => {
  it("composes the failed count and dominant butler as doors", () => {
    const html = render(
      <NotificationsVerdictOpener
        stats={stats({ failed: 5, by_butler: { finance: 3, chronicler: 2 } })}
        isLoading={false}
        isError={false}
      />,
    );
    expect(html).toContain('data-testid="notifications-verdict-clauses"');
    expect(html).toContain(
      `5 failed notifications in the last ${NOTIFICATIONS_VERDICT_WINDOW_HOURS}h`,
    );
    expect(html).toContain('href="/notifications?status=failed"');
    expect(html).toContain("3 from finance");
    expect(html).toContain('href="/notifications?status=failed&amp;butler=finance"');
  });

  it("pluralizes correctly for a single failure", () => {
    const html = render(
      <NotificationsVerdictOpener
        stats={stats({ failed: 1, by_butler: { finance: 1 } })}
        isLoading={false}
        isError={false}
      />,
    );
    expect(html).toContain(
      `1 failed notification in the last ${NOTIFICATIONS_VERDICT_WINDOW_HOURS}h`,
    );
  });

  it("omits the top-butler clause when by_butler is empty", () => {
    const html = render(
      <NotificationsVerdictOpener
        stats={stats({ failed: 2, by_butler: {} })}
        isLoading={false}
        isError={false}
      />,
    );
    expect(html).toContain("2 failed notifications");
    expect(html).not.toContain(" from ");
  });
});

describe("NotificationsVerdictOpener -- isError-suppression contract", () => {
  it("renders the skeleton while loading", () => {
    const html = render(
      <NotificationsVerdictOpener stats={undefined} isLoading isError={false} />,
    );
    expect(html).toContain('data-testid="notifications-verdict-skeleton"');
  });

  it("names the errored source and never renders the all-clear alongside it", () => {
    const html = render(
      <NotificationsVerdictOpener stats={undefined} isLoading={false} isError />,
    );
    expect(html).toContain('data-testid="notifications-verdict-clauses"');
    expect(html).toContain("notification stats unavailable");
    expect(html).not.toContain("No failed notifications");
  });

  it("treats source_available=false as a degraded source even on a 200 with all-zero counts", () => {
    const html = render(
      <NotificationsVerdictOpener
        stats={stats({ source_available: false })}
        isLoading={false}
        isError={false}
      />,
    );
    expect(html).toContain('data-testid="notifications-verdict-clauses"');
    expect(html).toContain("notification stats unavailable");
    expect(html).not.toContain("No failed notifications");
  });
});
