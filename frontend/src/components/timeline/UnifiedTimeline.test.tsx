import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";

import UnifiedTimeline from "@/components/timeline/UnifiedTimeline";
import type { TimelineEvent } from "@/api/types";

function makeEvent(overrides: Partial<TimelineEvent> = {}): TimelineEvent {
  return {
    id: "11111111-2222-3333-4444-555555555555",
    type: "session",
    butler: "general",
    timestamp: "2025-03-01T10:00:00Z",
    summary: "Handled a request",
    data: {},
    ...overrides,
  };
}

function render(events: TimelineEvent[]): string {
  return renderToStaticMarkup(
    <MemoryRouter>
      <UnifiedTimeline events={events} isLoading={false} />
    </MemoryRouter>,
  );
}

function renderWith(props: {
  events: TimelineEvent[];
  isLoading?: boolean;
  isError?: boolean;
  onRetry?: () => void;
}): string {
  return renderToStaticMarkup(
    <MemoryRouter>
      <UnifiedTimeline
        events={props.events}
        isLoading={props.isLoading ?? false}
        isError={props.isError}
        onRetry={props.onRetry}
      />
    </MemoryRouter>,
  );
}

describe("UnifiedTimeline — session row links", () => {
  it("links a session event row to /sessions/:id scoped by butler", () => {
    const html = render([makeEvent()]);
    expect(html).toContain(
      'href="/sessions/11111111-2222-3333-4444-555555555555?butler=general"',
    );
    expect(html).toContain("Handled a request");
  });

  it("links an error event row to the session detail page", () => {
    const html = render([makeEvent({ type: "error", summary: "Boom" })]);
    expect(html).toContain(
      'href="/sessions/11111111-2222-3333-4444-555555555555?butler=general"',
    );
  });

  it("omits the butler query param when the event has no butler", () => {
    const html = render([makeEvent({ butler: "" })]);
    expect(html).toContain(
      'href="/sessions/11111111-2222-3333-4444-555555555555"',
    );
    expect(html).not.toContain("?butler=");
  });

  it("does not link a notification event row to the session detail page", () => {
    const html = render([
      makeEvent({ type: "notification", summary: "Notified owner" }),
    ]);
    expect(html).not.toContain("/sessions/11111111-2222-3333-4444-555555555555");
    expect(html).toContain("Notified owner");
  });
});

describe("UnifiedTimeline — error vs empty state", () => {
  it("renders the error state (not the empty state) when isError is true", () => {
    const html = renderWith({ events: [], isError: true, onRetry: () => {} });
    expect(html).toContain("Could not load the timeline.");
    expect(html).toContain('data-testid="timeline-error"');
    expect(html).toContain("Retry");
    // A failed fetch must NOT be presented as genuine no-activity.
    expect(html).not.toContain("No events found.");
  });

  it("renders the empty state only on a successful fetch with zero events", () => {
    const html = renderWith({ events: [], isError: false });
    expect(html).toContain("No events found.");
    expect(html).not.toContain("Could not load the timeline.");
  });
});
