/**
 * useClientLink -- tracks THIS BROWSER's own network connectivity, distinct
 * from fleet/backend health (see EventBusHealth in use-event-stream.ts).
 *
 * Before this hook (bu-8cdl1.13), the shell's Live indicator and RootLayout's
 * sr-only announcer derived their only "connection" signal from the fleet
 * event-stream socket. A dropped LTE link closes that socket exactly like a
 * real backend outage does, so a phone owner who walks into an elevator saw
 * "Fleet event stream offline" -- a claim about the fleet's health that is
 * actually a fact about their own device. useClientLink gives callers the
 * missing signal so they can render client loss as client loss.
 */
import { useEffect, useRef, useState } from "react";

export type ClientLinkStatus = "online" | "offline" | "reconnecting";

/** Grace window after the browser's `online` event fires before the client
 *  link is reported fully restored, so a flapping connection does not read
 *  as instantly healthy. */
export const CLIENT_LINK_RECONNECT_GRACE_MS = 2_000;

export interface UseClientLinkResult {
  status: ClientLinkStatus;
}

function browserIsOnline(): boolean {
  return typeof navigator === "undefined" ? true : navigator.onLine;
}

export function useClientLink(): UseClientLinkResult {
  const [status, setStatus] = useState<ClientLinkStatus>(() =>
    browserIsOnline() ? "online" : "offline",
  );
  const graceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    function clearGraceTimer() {
      if (graceTimerRef.current !== null) {
        clearTimeout(graceTimerRef.current);
        graceTimerRef.current = null;
      }
    }

    function handleOffline() {
      clearGraceTimer();
      setStatus("offline");
    }

    function handleOnline() {
      clearGraceTimer();
      setStatus("reconnecting");
      graceTimerRef.current = setTimeout(() => {
        graceTimerRef.current = null;
        setStatus("online");
      }, CLIENT_LINK_RECONNECT_GRACE_MS);
    }

    window.addEventListener("offline", handleOffline);
    window.addEventListener("online", handleOnline);
    return () => {
      clearGraceTimer();
      window.removeEventListener("offline", handleOffline);
      window.removeEventListener("online", handleOnline);
    };
  }, []);

  return { status };
}
