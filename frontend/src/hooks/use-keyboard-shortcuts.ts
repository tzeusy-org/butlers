declare global {
  interface Window {
    __pendingGNav?: boolean;
  }
}

import { useEffect } from "react";
import { useNavigate } from "react-router";

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

      // Cmd/Ctrl+K → focus search
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        window.dispatchEvent(new CustomEvent("open-search"));
        return;
      }

      // / → focus search
      if (e.key === "/" && !e.metaKey && !e.ctrlKey) {
        e.preventDefault();
        window.dispatchEvent(new CustomEvent("open-search"));
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
          case "r":
            navigate("/traces");
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
        }
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [navigate]);
}
