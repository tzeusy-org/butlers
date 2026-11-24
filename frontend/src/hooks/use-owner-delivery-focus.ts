import { useEffect } from "react";
import { ownerFetch } from "@/api/owner-session";

/** Server-timed activity, never acknowledgement. A disabled server stops this loop. */
export function useOwnerDeliveryFocus() {
  useEffect(() => {
    let stopped = false;
    let pending = false;
    async function report() {
      if (stopped || pending || document.visibilityState === "hidden" || !document.hasFocus()) return;
      pending = true;
      try {
        const result = await ownerFetch("/api/owner-delivery/activity", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ focused: true }),
        });
        if (result.status === 404) stopped = true;
      } catch { /* The finite server freshness expires; never invent activity on failure. */ }
      finally { pending = false; }
    }
    void report();
    const timer = window.setInterval(() => void report(), 30_000);
    const wake = () => void report();
    window.addEventListener("focus", wake);
    document.addEventListener("visibilitychange", wake);
    return () => {
      stopped = true;
      window.clearInterval(timer);
      window.removeEventListener("focus", wake);
      document.removeEventListener("visibilitychange", wake);
    };
  }, []);
}
