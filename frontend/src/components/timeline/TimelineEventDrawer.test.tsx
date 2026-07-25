// @vitest-environment jsdom
/**
 * Tests for TimelineEventDrawer's per-type "View" doors (bu-ep4ks.7,
 * last-hop door repair pack).
 *
 * A notification-type event's drawer must deep-link to the specific
 * notification (via NotificationsPage's `?notification=` highlight param),
 * not the bare unfiltered /notifications list.
 */

import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";

import { TimelineEventDrawer } from "./TimelineEventDrawer";
import type { TimelineEvent } from "@/api/types.ts";

function event(overrides: Partial<TimelineEvent> = {}): TimelineEvent {
  return {
    id: "evt-001",
    type: "session",
    butler: "finance",
    timestamp: "2026-07-01T12:00:00Z",
    summary: "Ran a scheduled task",
    is_heartbeat: false,
    data: {},
    ...overrides,
  };
}

function render(evt: TimelineEvent): string {
  return renderToStaticMarkup(
    <MemoryRouter>
      <TimelineEventDrawer event={evt} onClose={() => {}} />
    </MemoryRouter>,
  );
}

describe("TimelineEventDrawer -- notification door", () => {
  it("links to the specific notification via ?notification=<id>, not the bare list", () => {
    const html = render(event({ id: "notif-xyz", type: "notification" }));
    expect(html).toContain('data-testid="drawer-notification-link"');
    expect(html).toContain('href="/notifications?notification=notif-xyz"');
  });

  it("URI-encodes the notification id", () => {
    const html = render(event({ id: "notif abc", type: "notification" }));
    expect(html).toContain('href="/notifications?notification=notif%20abc"');
  });
});

describe("TimelineEventDrawer -- session door", () => {
  it("links to the session transcript, scoped by butler when known", () => {
    const html = render(event({ id: "sess-1", type: "session", butler: "finance" }));
    expect(html).toContain('data-testid="drawer-session-link"');
    expect(html).toContain('href="/sessions/sess-1?butler=finance"');
  });
});
