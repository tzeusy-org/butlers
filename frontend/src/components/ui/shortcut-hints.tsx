import { useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { ALL_ROUTES } from "@/lib/route-registry";
import { OPEN_SHORTCUT_HELP_EVENT } from "@/lib/shortcut-help";
import { useShortcutHintEntries } from "@/hooks/use-register-shortcut";

interface ShortcutRow {
  keys: readonly string[];
  description: string;
}

const STATIC_SHORTCUTS: ShortcutRow[] = [
  { keys: ["Ctrl", "K"], description: "Open command menu" },
  { keys: ["/"], description: "Open command menu" },
  { keys: ["?"], description: "Open this help sheet" },
];

/**
 * g-chord rows, generated from the same route registry that builds the
 * sidebar and the command menu's Pages group (bu-86c4c.7) — this list used
 * to be a hand-maintained duplicate here that had already drifted (g-h
 * pointed at a stale route). It can't drift anymore: a chord shown here is
 * read from the exact route it navigates to.
 */
function useChordShortcuts(): ShortcutRow[] {
  return useMemo(
    () =>
      ALL_ROUTES.filter((r) => r.chord).map((r) => ({
        keys: ["g", r.chord as string],
        description: `Go to ${r.label}`,
      })),
    [],
  );
}

function Kbd({ children }: { children: React.ReactNode }) {
  return (
    <kbd className="pointer-events-none inline-flex h-5 select-none items-center gap-1 rounded border bg-muted px-1.5 font-mono text-[10px] font-medium text-muted-foreground">
      {children}
    </kbd>
  );
}

/**
 * Rows contributed by whichever page is currently mounted, via
 * `useRegisterShortcut` (bu-qvnce.11 — "On this page"). This is the ONLY
 * place page-scoped shortcuts (approvals triage, chat conversation
 * switching, ...) become discoverable at all — before this they had zero
 * hints anywhere in the product.
 */
function usePageShortcuts(): ShortcutRow[] {
  const bindings = useShortcutHintEntries();
  return useMemo(
    () => bindings.map((b) => ({ keys: b.display, description: b.description })),
    [bindings],
  );
}

export function ShortcutHints() {
  const [open, setOpen] = useState(false);
  const chordShortcuts = useChordShortcuts();
  const pageShortcuts = usePageShortcuts();
  const shortcuts = useMemo(
    () => [...STATIC_SHORTCUTS, ...chordShortcuts],
    [chordShortcuts],
  );

  // '?' is bound globally (use-keyboard-shortcuts.ts) in addition to this
  // trigger button, so the help sheet has a real keyboard binding rather
  // than only being reachable by clicking the floating button.
  useEffect(() => {
    function handleOpen() {
      setOpen(true);
    }
    window.addEventListener(OPEN_SHORTCUT_HELP_EVENT, handleOpen);
    return () => window.removeEventListener(OPEN_SHORTCUT_HELP_EVENT, handleOpen);
  }, []);

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          className="fixed bottom-4 right-4 z-50 h-8 w-8 rounded-full opacity-60 hover:opacity-100"
          aria-label="Keyboard shortcuts"
          title="?"
        >
          <span className="text-xs font-bold">?</span>
        </Button>
      </DialogTrigger>
      <DialogContent className="max-h-[80vh] overflow-y-auto sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>Keyboard Shortcuts</DialogTitle>
        </DialogHeader>
        <ShortcutRowList rows={shortcuts} />

        {/* "On this page" — page-scoped shortcuts published by whichever
            page is currently mounted, via useRegisterShortcut (bu-qvnce.11).
            Omitted entirely (not an empty heading) when the current page
            registers none. */}
        {pageShortcuts.length > 0 && (
          <section aria-labelledby="shortcut-hints-page-heading" className="pt-4">
            <h3
              id="shortcut-hints-page-heading"
              className="mb-2 text-[10px] font-mono uppercase tracking-widest text-muted-foreground"
            >
              On this page
            </h3>
            <ShortcutRowList rows={pageShortcuts} />
          </section>
        )}
      </DialogContent>
    </Dialog>
  );
}

/** Shared row renderer for both the global and "On this page" sections. */
function ShortcutRowList({ rows }: { rows: ShortcutRow[] }) {
  return (
    <div className="space-y-3 pt-2" role="list">
      {rows.map((shortcut, idx) => (
        <div key={idx} role="listitem" className="flex items-center justify-between">
          <span className="text-sm text-muted-foreground">{shortcut.description}</span>
          <div className="flex items-center gap-1">
            {shortcut.keys.map((key, kidx) => (
              <span key={kidx} className="flex items-center gap-1">
                {kidx > 0 && <span className="text-xs text-muted-foreground">+</span>}
                <Kbd>{key}</Kbd>
              </span>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
