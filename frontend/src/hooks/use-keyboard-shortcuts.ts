declare global {
  interface Window {
    __pendingGNav?: boolean;
  }
}

import { useEffect } from "react";
import { useNavigate } from "react-router";
import { dispatchOpenCommandPalette } from "@/lib/command-palette";
import { dispatchOpenEntityFinder } from "@/lib/entity-finder";

export function useKeyboardShortcuts() {
  const navigate = useNavigate();

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      // Don't trigger in inputs or textareas
      const target = e.target as HTMLElement;
      if (
        target.tagName === "INPUT" ||
        target.tagName === "TEXTAREA" ||
        target.isContentEditable
      ) {
        return;
      }

      // Cmd/Ctrl+K → entity-first finder (EntityFinder, bu-xfjwk)
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        dispatchOpenEntityFinder();
        return;
      }

      // / → legacy command palette (global nav search)
      if (e.key === "/" && !e.metaKey && !e.ctrlKey) {
        e.preventDefault();
        dispatchOpenCommandPalette();
        return;
      }

      // g+key navigation (press g, then a key)
      if (e.key === "g") {
        window.__pendingGNav = true;
        setTimeout(() => {
          window.__pendingGNav = false;
        }, 1000);
        return;
      }

      if (window.__pendingGNav) {
        window.__pendingGNav = false;
        switch (e.key) {
          case "o":
            navigate("/");
            break;
          case "b":
            navigate("/butlers");
            break;
          case "s":
            navigate("/sessions");
            break;
          case "t":
            navigate("/timeline");
            break;
          case "n":
            navigate("/notifications");
            break;
          case "i":
            navigate("/issues");
            break;
          case "a":
            navigate("/audit-log");
            break;
          case "m":
            navigate("/memory");
            break;
          case "c":
            navigate("/contacts");
            break;
          case "h":
            navigate("/health/measurements");
            break;
          case "e":
            navigate("/ingestion");
            break;
        }
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [navigate]);
}
