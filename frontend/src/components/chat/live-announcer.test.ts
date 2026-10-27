// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  IDLE_FLUSH_MS,
  LiveAnnouncer,
  REPLY_COMPLETE_MARKER,
  REPLY_COMPLETE_MIN_MS,
} from "./live-announcer.ts";

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("LiveAnnouncer — sentence-boundary batching", () => {
  it("splits on [.!?] followed by whitespace and emits only complete sentences", () => {
    const onChange = vi.fn();
    const announcer = new LiveAnnouncer(onChange, 0);

    announcer.feed("First sentence. Second sent");
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenLastCalledWith(["First sentence."]);

    announcer.feed("First sentence. Second sentence! Third star");
    expect(onChange).toHaveBeenCalledTimes(2);
    expect(onChange).toHaveBeenLastCalledWith(["First sentence.", "Second sentence!"]);
  });

  it("never emits a per-token update — only on a sentence boundary or idle flush", () => {
    const onChange = vi.fn();
    const announcer = new LiveAnnouncer(onChange, 0);

    const tokens = ["No", " punc", "tuation", " here", " yet"];
    let acc = "";
    for (const token of tokens) {
      acc += token;
      announcer.feed(acc);
    }
    expect(onChange).not.toHaveBeenCalled();
  });

  it("flushes the trailing remainder after the idle timer fires", () => {
    const onChange = vi.fn();
    const announcer = new LiveAnnouncer(onChange, 0);

    announcer.feed("Trailing clause with no terminal punctuation");
    expect(onChange).not.toHaveBeenCalled();

    vi.advanceTimersByTime(IDLE_FLUSH_MS);
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenLastCalledWith(["Trailing clause with no terminal punctuation"]);
  });

  it("resets the idle timer on every feed so only a real pause flushes", () => {
    const onChange = vi.fn();
    const announcer = new LiveAnnouncer(onChange, 0);

    announcer.feed("Partial");
    vi.advanceTimersByTime(IDLE_FLUSH_MS - 1);
    announcer.feed("Partial two");
    vi.advanceTimersByTime(IDLE_FLUSH_MS - 1);
    expect(onChange).not.toHaveBeenCalled();

    vi.advanceTimersByTime(1);
    expect(onChange).toHaveBeenCalledTimes(1);
  });

  it("appends 'Reply complete' only when the reply took longer than 3s", () => {
    const onChangeFast = vi.fn();
    const fast = new LiveAnnouncer(onChangeFast, 0);
    fast.feed("Quick reply. ");
    onChangeFast.mockClear();
    fast.complete(REPLY_COMPLETE_MIN_MS);
    expect(onChangeFast).not.toHaveBeenCalled();

    const onChangeSlow = vi.fn();
    const slow = new LiveAnnouncer(onChangeSlow, 0);
    slow.feed("Slow reply. ");
    onChangeSlow.mockClear();
    slow.complete(REPLY_COMPLETE_MIN_MS + 1);
    expect(onChangeSlow).toHaveBeenCalledWith(["Slow reply.", REPLY_COMPLETE_MARKER]);
  });

  it("flushes any buffered remainder on complete, even without the idle timer firing", () => {
    const onChange = vi.fn();
    const announcer = new LiveAnnouncer(onChange, 0);
    announcer.feed("No terminal punctuation");
    announcer.complete(1);
    expect(onChange).toHaveBeenCalledWith(["No terminal punctuation"]);
  });

  it("is idempotent — a second complete() call is a no-op", () => {
    const onChange = vi.fn();
    const announcer = new LiveAnnouncer(onChange, 0);
    announcer.feed("Done.");
    announcer.complete(REPLY_COMPLETE_MIN_MS + 1);
    onChange.mockClear();
    announcer.complete(REPLY_COMPLETE_MIN_MS + 1);
    expect(onChange).not.toHaveBeenCalled();
  });
});
