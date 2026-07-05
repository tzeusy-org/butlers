/**
 * ListTriageFooterHint -- shared footer strip advertising a list's j/k/act
 * keyboard bindings inline, on the page, instead of only inside the '?'
 * help sheet (bu-qvnce.11 slice 4).
 *
 * Renders the EXACT bindings returned by `useListTriage`'s `hints` -- so a
 * page's footer can never advertise a key that isn't actually wired (the
 * same "unadvertised shortcut is structurally impossible" property the
 * shortcut registry itself guarantees for the help sheet). Renders `null`
 * when there is nothing to show (no visible rows), matching the "omitted
 * entirely, not an empty heading" convention `ShortcutHints`'s "On this
 * page" section already uses.
 *
 * Uses the same `Kbd` key-cap chip as the '?' help sheet so a binding reads
 * identically wherever it is advertised (one Dispatch visual language).
 */

import { Kbd } from "@/components/ui/shortcut-hints";
import type { ShortcutBinding } from "@/hooks/use-register-shortcut";

export function ListTriageFooterHint({ bindings }: { bindings: ShortcutBinding[] }) {
  if (bindings.length === 0) return null;

  return (
    <div
      role="note"
      aria-label="Keyboard shortcuts for this list"
      className="flex flex-wrap items-center gap-x-4 gap-y-1.5 border-t border-border px-3 py-2 text-xs text-muted-foreground"
    >
      {bindings.map((binding) => (
        <span key={binding.key} className="flex items-center gap-1.5">
          <Kbd>{binding.display[0]}</Kbd>
          {binding.description}
        </span>
      ))}
    </div>
  );
}
