/**
 * Sentence-boundary batching for the chat sr-only live region (bu-0ynlk.13,
 * a11y-keyboard #1 / WCAG 4.1.3 Status Messages).
 *
 * Streaming a reply token-by-token into an `aria-live` region would spam a
 * screen reader with a new announcement per token. Instead, this buffers
 * incoming content and only pushes a completed span to the live region:
 *   - immediately, when the buffered tail crosses a sentence boundary
 *     ([.!?] followed by whitespace), or
 *   - after `IDLE_FLUSH_MS` of no new content (so a reply that ends without
 *     terminal punctuation, or pauses mid-sentence, still reaches the
 *     region), or
 *   - on `complete()`, which flushes any remainder and appends a final
 *     "Reply complete" marker — but only when the whole reply took longer
 *     than `REPLY_COMPLETE_MIN_MS`, since announcing completion of a reply
 *     that was already fully read out is just noise.
 */

export const SENTENCE_BOUNDARY_RE = /(?<=[.!?])\s+/;
export const IDLE_FLUSH_MS = 1500;
export const REPLY_COMPLETE_MIN_MS = 3000;
export const REPLY_COMPLETE_MARKER = "Reply complete";

export class LiveAnnouncer {
  private emittedLength = 0;
  private pendingTail = "";
  private spans: string[] = [];
  private readonly startedAt: number;
  private readonly onChange: (spans: readonly string[]) => void;
  private idleTimer: ReturnType<typeof setTimeout> | null = null;
  private done = false;

  constructor(onChange: (spans: readonly string[]) => void, now: number = Date.now()) {
    this.onChange = onChange;
    this.startedAt = now;
  }

  /** Feed the FULL accumulated streamed content so far (not a delta). */
  feed(fullContent: string): void {
    if (this.done) return;
    const delta = fullContent.slice(this.emittedLength);
    if (!delta) return;
    this.emittedLength = fullContent.length;
    this.pendingTail += delta;
    this.commitCompleteSentences();
    this.scheduleIdleFlush();
  }

  private commitCompleteSentences(): void {
    const parts = this.pendingTail.split(SENTENCE_BOUNDARY_RE);
    if (parts.length <= 1) return;
    const complete = parts.slice(0, -1);
    this.pendingTail = parts[parts.length - 1];
    let changed = false;
    for (const sentence of complete) {
      const trimmed = sentence.trim();
      if (trimmed) {
        this.spans.push(trimmed);
        changed = true;
      }
    }
    if (changed) this.onChange([...this.spans]);
  }

  private scheduleIdleFlush(): void {
    if (this.idleTimer) clearTimeout(this.idleTimer);
    this.idleTimer = setTimeout(() => {
      this.idleTimer = null;
      if (this.flushTail()) this.onChange([...this.spans]);
    }, IDLE_FLUSH_MS);
  }

  private flushTail(): boolean {
    const trimmed = this.pendingTail.trim();
    this.pendingTail = "";
    if (!trimmed) return false;
    this.spans.push(trimmed);
    return true;
  }

  /** Flush any remainder and, once the reply is long enough, append the
   * completion marker. Idempotent — later calls are no-ops. */
  complete(now: number = Date.now()): void {
    if (this.done) return;
    this.done = true;
    if (this.idleTimer) {
      clearTimeout(this.idleTimer);
      this.idleTimer = null;
    }
    let changed = this.flushTail();
    if (now - this.startedAt > REPLY_COMPLETE_MIN_MS) {
      this.spans.push(REPLY_COMPLETE_MARKER);
      changed = true;
    }
    if (changed) this.onChange([...this.spans]);
  }

  dispose(): void {
    if (this.idleTimer) {
      clearTimeout(this.idleTimer);
      this.idleTimer = null;
    }
  }
}
