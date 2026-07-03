declare global {
  interface Window {
    __pendingGNav?: boolean;
  }
}

import { useEffect } from "react";
import { useNavigate } from "react-router";
import { dispatchOpenEntityFinder } from "@/lib/entity-finder";
import { dispatchOpenShortcutHelp } from "@/lib/shortcut-help";
import { G_CHORD_ROUTES } from "@/lib/route-registry";

export function useKeyboardShortcuts() {
  const navigate = useNavigate();

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      const target = e.target as HTMLElement;
      const inEditableField =
        target.tagName === "INPUT" ||
        target.tagName === "TEXTAREA" ||
        target.isContentEditable;

      // Cmd/Ctrl+K → the command menu. This is a modifier chord, so it can't
      // collide with normal typing — unlike '/' and 'g', it must work even
      // while focus is inside an input (bu-86c4c.7: "every shortcut dies
      // while focus is in an input" was the audit finding here).
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        dispatchOpenEntityFinder();
        return;
      }

      // Everything below is a bare, unmodified key ('/', '?', 'g', letters)
      // that would otherwise collide with normal typing — skip while an
      // input/textarea/contenteditable has focus.
      if (inEditableField) return;

      // / → the command menu (same surface as Cmd+K; bu-86c4c.7 unification
      // — this used to open a second, different palette).
      if (e.key === "/" && !e.metaKey && !e.ctrlKey) {
        e.preventDefault();
        dispatchOpenEntityFinder();
        return;
      }

      // ? → keyboard-shortcuts help sheet (previously click-only).
      if (e.key === "?" && !e.metaKey && !e.ctrlKey) {
        e.preventDefault();
        dispatchOpenShortcutHelp();
        return;
      }

      // g+key navigation (press g, then a key). Destinations come from the
      // single route registry (src/lib/route-registry.ts) so this can never
      // drift from the sidebar/command menu again.
      if (e.key === "g") {
        window.__pendingGNav = true;
        setTimeout(() => {
          window.__pendingGNav = false;
        }, 1000);
        return;
      }

      if (window.__pendingGNav) {
        window.__pendingGNav = false;
        const path = G_CHORD_ROUTES[e.key];
        if (path) navigate(path);
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [navigate]);
}
