/**
 * "Xm elapsed" / "Xh elapsed" / "Xd elapsed" -- the elapsed-time counterpart
 * to ApprovalsVerdictOpener's countdownText (that one counts down to a future
 * expiry; this one counts up from a past start).
 *
 * Shared by SessionsVerdictOpener (one-shot verdict render, defaults `now` to
 * Date.now()) and the Sessions pinned strip's live-ticking running-session
 * timer (bu-ptaub, passes a ticking `now` from useTickingNow so the text
 * advances on its own). Lives in its own module (not a component file) so
 * react-refresh/only-export-components stays happy.
 */
export function elapsedText(startedAt: string, now: number = Date.now()): string | null {
  const startDate = new Date(startedAt);
  if (Number.isNaN(startDate.getTime())) return null;
  const msElapsed = now - startDate.getTime();
  if (msElapsed < 0) return null;
  const mins = Math.round(msElapsed / 60_000);
  if (mins < 1) return "just started";
  if (mins < 60) return `${mins}m elapsed`;
  const hours = Math.round(msElapsed / 3_600_000);
  if (hours < 24) return `${hours}h elapsed`;
  const days = Math.round(msElapsed / (24 * 3_600_000));
  return `${days}d elapsed`;
}
