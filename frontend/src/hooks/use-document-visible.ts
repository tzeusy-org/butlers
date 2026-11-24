import { useSyncExternalStore } from "react";

function subscribe(onChange: () => void) {
  document.addEventListener("visibilitychange", onChange);
  return () => document.removeEventListener("visibilitychange", onChange);
}

function isVisible() {
  return document.visibilityState !== "hidden";
}

/** False only while the browser reports this tab as hidden. */
export function useDocumentVisible(): boolean {
  return useSyncExternalStore(subscribe, isVisible);
}
