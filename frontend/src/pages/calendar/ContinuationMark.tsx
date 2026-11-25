/**
 * The marker for an event that began before, or runs past, the day it is drawn on: a visible arrow
 * for sighted users and a spoken phrase for screen readers (bu-5ii4gj.4 D2).
 */

interface ContinuationMarkProps {
  direction: "from" | "to";
}

export function ContinuationMark({ direction }: ContinuationMarkProps) {
  return direction === "from" ? (
    <>
      <span aria-hidden="true">← </span>
      <span className="sr-only">(continued from previous day) </span>
    </>
  ) : (
    <>
      <span aria-hidden="true"> →</span>
      <span className="sr-only"> (continues next day)</span>
    </>
  );
}
