// @vitest-environment jsdom
/**
 * Component tests for LatestInteractionsBlock (entity v3 latest interactions
 * per channel, bu-19u8r).
 *
 * Covers:
 * - most-recent touch per channel, most recent first (spec: "render both rows,
 *   most recent first");
 * - only the latest row survives per channel (older same-channel touches drop);
 * - occurred_at + staleness treatment render per row;
 * - stored summaries render (no generated prose);
 * - empty across all channels hides the section;
 * - nothing renders while loading.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";

import type { EntityTimelineItem, MessageThreadSummary } from "@/api/types";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

vi.mock("@/hooks/use-entities", () => ({
  useEntityMessageThreads: vi.fn(),
  useEntityTimeline: vi.fn(),
}));

import { useEntityMessageThreads, useEntityTimeline } from "@/hooks/use-entities";
import { deriveLatestTouches } from "@/lib/latest-touches";
import { LatestInteractionsBlock } from "./LatestInteractionsBlock";

function thread(over: Partial<MessageThreadSummary>): MessageThreadSummary {
  return {
    source_channel: "telegram",
    thread_identity: "t1",
    sender_identity: "s1",
    message_count: 3,
    last_received_at: "2026-06-12T10:00:00Z",
    last_direction: "inbound",
    last_snippet: "see you tomorrow",
    ...over,
  };
}

function timelineItem(over: Partial<EntityTimelineItem>): EntityTimelineItem {
  return {
    kind: "interaction",
    id: "i1",
    content: "coffee catch-up",
    valid_at: "2026-05-01T09:00:00Z",
    predicate: "interaction_in_person",
    metadata: { src: "relationship" },
    ...over,
  };
}

function mockData(
  threads: MessageThreadSummary[],
  timeline: EntityTimelineItem[],
  isLoading = false,
) {
  vi.mocked(useEntityMessageThreads).mockReturnValue({
    data: threads,
    isLoading,
  } as unknown as ReturnType<typeof useEntityMessageThreads>);
  vi.mocked(useEntityTimeline).mockReturnValue({
    data: timeline,
    isLoading,
  } as unknown as ReturnType<typeof useEntityTimeline>);
}

let container: HTMLDivElement;
let root: Root;

function render() {
  act(() => {
    root.render(<LatestInteractionsBlock entityId="ent-1" />);
  });
}

beforeEach(() => {
  vi.resetAllMocks();
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  document.body.innerHTML = "";
  vi.restoreAllMocks();
});

describe("deriveLatestTouches", () => {
  it("keeps one row per channel, most recent first", () => {
    const touches = deriveLatestTouches(
      [thread({ source_channel: "telegram", last_received_at: "2026-06-12T10:00:00Z" })],
      [timelineItem({ valid_at: "2026-05-01T09:00:00Z" })],
    );
    expect(touches).toHaveLength(2);
    // Telegram thread (Jun 12) is more recent than the in-person interaction (May 1).
    expect(touches[0].key).toBe("thread:telegram");
    expect(touches[1].key).toBe("interaction:in_person");
  });

  it("drops older same-channel touches", () => {
    const touches = deriveLatestTouches(
      [
        thread({ source_channel: "telegram", last_received_at: "2026-06-12T10:00:00Z", last_snippet: "newest" }),
        thread({ source_channel: "telegram", last_received_at: "2026-01-01T10:00:00Z", last_snippet: "oldest" }),
      ],
      [],
    );
    const telegram = touches.filter((t) => t.key === "thread:telegram");
    expect(telegram).toHaveLength(1);
    expect(telegram[0].summary).toBe("newest");
  });

  it("sorts deterministically when timestamps are missing", () => {
    // Two touches with null timestamps both map to NEGATIVE_INFINITY. A
    // subtraction comparator would return NaN here and scramble the order;
    // the comparison comparator must keep it stable and input-ordered.
    const touches = deriveLatestTouches(
      [
        thread({ source_channel: "telegram", last_received_at: null, last_snippet: "tg" }),
        thread({ source_channel: "email", last_received_at: null, last_snippet: "em" }),
      ],
      [],
    );
    expect(touches.map((t) => t.key)).toEqual(["thread:telegram", "thread:email"]);
  });

  it("ignores non-interaction timeline rows", () => {
    const touches = deriveLatestTouches(
      [],
      [
        timelineItem({ kind: "note", predicate: "note", content: "a note" }),
        timelineItem({ kind: "interaction", predicate: "interaction_call", content: "rang" }),
      ],
    );
    expect(touches).toHaveLength(1);
    expect(touches[0].key).toBe("interaction:call");
  });
});

describe("LatestInteractionsBlock", () => {
  it("renders both channels, most recent first, with stored summaries", () => {
    mockData(
      [thread({ source_channel: "telegram", last_received_at: "2026-06-12T10:00:00Z", last_snippet: "see you tomorrow" })],
      [timelineItem({ valid_at: "2026-05-01T09:00:00Z", content: "coffee catch-up" })],
    );
    render();

    expect(container.querySelector('[data-testid="latest-interactions-block"]')).not.toBeNull();
    const rows = container.querySelectorAll('[data-testid^="latest-interaction-row-"]');
    expect(rows.length).toBe(2);
    expect(rows[0].getAttribute("data-testid")).toBe("latest-interaction-row-thread:telegram");
    expect(rows[0].textContent).toContain("see you tomorrow");
    expect(rows[1].textContent).toContain("coffee catch-up");
  });

  it("renders a staleness band per row", () => {
    mockData(
      [thread({ source_channel: "telegram", last_received_at: "2020-01-01T00:00:00Z" })],
      [],
    );
    render();
    const band = container.querySelector('[data-staleness]');
    expect(band).not.toBeNull();
    // A touch from 2020 is far past the 180-day window → stale.
    expect(band?.getAttribute("data-staleness")).toBe("stale");
  });

  it("hides the section when there are no interactions", () => {
    mockData([], []);
    render();
    expect(container.querySelector('[data-testid="latest-interactions-block"]')).toBeNull();
    expect(container.innerHTML).toBe("");
  });

  it("renders nothing while loading", () => {
    mockData([], [], /* isLoading */ true);
    render();
    expect(container.innerHTML).toBe("");
  });
});
