/**
 * useListTriage -- shared j/k roving-selection + act-key pattern for row
 * lists (bu-qvnce.11 slice 4, extracted from ApprovalsPage's hand-rolled
 * j/k/a/d/x/u implementation).
 *
 * Before this hook, ApprovalsPage was the only page with keyboard triage
 * over a row list, and its moveSelection/shortcutBindings machinery lived
 * entirely inline -- not reusable by any other row list in the product
 * (Dashboard's Needs-attention list, Issues, Notifications). This hook lifts
 * the reusable half (j/k moves a selection cursor across a visible id list;
 * an optional set of "verbs" bind to whatever is currently selected) while
 * leaving page-specific decision machinery (undo windows, toasts, mutation
 * wiring) where it already lived -- those differ enough per page (approve/
 * deny/defer vs acknowledge/restore vs mark-read) that folding them into the
 * shared hook would just relocate page-specific logic, not generalize it.
 *
 * It registers through `useRegisterShortcut`, so:
 *   - j/k (and any verb keys) are suspended while focus is in an editable
 *     field, a `<select>`, or any open `[role=dialog]` overlay (the shared
 *     guard useRegisterShortcut already applies) -- callers do not need to
 *     re-implement that guard.
 *   - every binding is published to the '?' help sheet's "On this page"
 *     section automatically.
 *
 * The returned `hints` array IS the exact set of bindings registered --
 * pass it straight to `<ListTriageFooterHint bindings={hints} />` so a
 * page's footer strip can never drift from what's actually bound.
 *
 * Usage:
 *
 *   const ids = useMemo(() => rows.map((r) => r.id), [rows]);
 *   const verbs = useMemo<ListTriageVerb[]>(() => {
 *     if (!selectedId) return [];
 *     return [{
 *       key: "a",
 *       description: "Approve selected",
 *       handler: () => approve(selectedId),
 *       command: { id: "approve-selected", label: "Approve selected" },
 *     }];
 *   }, [selectedId, approve]);
 *   const { moveSelection, hints } = useListTriage({ ids, selectedId, onSelect: setSelectedId, verbs });
 */

import { useCallback, useMemo } from "react";
import { useRegisterCommands, type PaletteCommand } from "@/lib/command-registry";
import { useRegisterShortcut, type ShortcutBinding } from "@/hooks/use-register-shortcut";

export type ListTriageCommand = Pick<PaletteCommand, "id" | "label" | "keywords">;

export interface ListTriageVerb {
  /** `KeyboardEvent.key` to match, e.g. "a" or "Enter". List-triage verbs
   *  are always plain, unmodified keystrokes. */
  key: string;
  /** One-line description shown in the '?' help sheet and the footer hint strip. */
  description: string;
  /** Invoked when the key fires (subject to the shared suspend guard). */
  handler: () => void;
  /**
   * Required command-palette representation of this selected-row action.
   * `useListTriage` supplies the matching handler and binding from this same
   * declaration, so the palette cannot target a different row than the key.
   * Lists with only j/k navigation omit `verbs` entirely.
   */
  command: ListTriageCommand;
}

export interface UseListTriageOptions<TId extends string = string> {
  /** Ordered ids of the currently-visible rows (memoize this in the caller
   *  so identity is stable across renders that don't actually change the
   *  visible set -- otherwise the shortcut registry churns every render). */
  ids: TId[];
  /** Currently-selected id, or null/undefined if nothing is selected yet. */
  selectedId: TId | null | undefined;
  /** Called with the next id when j/k moves the selection. */
  onSelect: (id: TId) => void;
  /**
   * Act-verb bindings for the CURRENTLY SELECTED row (e.g. approve/deny/
   * defer, or a single acknowledge/mark-read). Memoize in the caller keyed
   * on whatever the verbs actually close over -- recomputing this is what
   * lets verbs change (or disappear) when the selection changes. Omit or
   * return `[]` for a list that is j/k-navigable but has no keyboard act.
   */
  verbs?: ListTriageVerb[];
}

export interface UseListTriageResult {
  /**
   * Move the selection by delta (1 = next, -1 = previous), clamped to the
   * visible id list. No-ops when there are no visible rows or the target
   * id is already selected.
   */
  moveSelection: (delta: 1 | -1) => void;
  /**
   * The exact ShortcutBinding[] registered with useRegisterShortcut this
   * render -- pass to <ListTriageFooterHint bindings={hints} />.
   */
  hints: ShortcutBinding[];
}

export function useListTriage<TId extends string = string>({
  ids,
  selectedId,
  onSelect,
  verbs = [],
}: UseListTriageOptions<TId>): UseListTriageResult {
  const moveSelection = useCallback(
    (delta: 1 | -1) => {
      if (ids.length === 0) return;
      const idx = selectedId ? ids.indexOf(selectedId) : -1;
      const nextIdx = idx === -1 ? 0 : Math.min(Math.max(idx + delta, 0), ids.length - 1);
      const next = ids[nextIdx];
      if (next !== undefined && next !== selectedId) onSelect(next);
    },
    [ids, selectedId, onSelect],
  );

  const bindings = useMemo<ShortcutBinding[]>(() => {
    if (ids.length === 0) return [];
    const result: ShortcutBinding[] = [
      { key: "j", display: ["j"], description: "Next item", handler: () => moveSelection(1) },
      { key: "k", display: ["k"], description: "Previous item", handler: () => moveSelection(-1) },
    ];
    if (selectedId) {
      for (const verb of verbs) {
        result.push({
          key: verb.key,
          display: [verb.key],
          description: verb.description,
          handler: verb.handler,
        });
      }
    }
    return result;
  }, [ids, selectedId, moveSelection, verbs]);

  useRegisterShortcut(bindings);

  const commands = useMemo<PaletteCommand[]>(() => {
    if (!selectedId) return [];
    return verbs.map((verb) => ({ ...verb.command, binding: [verb.key], perform: verb.handler }));
  }, [selectedId, verbs]);

  useRegisterCommands(commands);

  return { moveSelection, hints: bindings };
}
