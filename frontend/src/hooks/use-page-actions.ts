/**
 * usePageActions -- single-declaration primary-action manifest for a page's
 * own (non-list) verbs (bu-ep4ks.12 -- "keyboard chassis completion").
 *
 * useListTriage already solves this pairing problem for row-triage verbs
 * (one verb feeds both a ShortcutBinding and a PaletteCommand, so a key and
 * its palette entry can never point at different rows). This hook lifts the
 * same one-declaration-feeds-both shape for a page's standalone actions --
 * "Refresh timeline", "Jump to latest", "Save view" -- that have no
 * surrounding row list. Before this, a page wired useRegisterShortcut and
 * useRegisterCommands separately for the same verb, which is exactly how
 * command-registry.tsx's own docstring drifted: an illustrative example
 * verb with no shortcut actually paired to it. Declaring the action once
 * here makes that drift structurally impossible -- the shortcut's `display`
 * IS the command's `binding`.
 *
 * A thin composition of useRegisterShortcut + useRegisterCommands -- it does
 * not replace either, and does not fit list-triage rows (use useListTriage
 * for those).
 *
 * Usage:
 *
 *   usePageActions([
 *     {
 *       id: "timeline-refresh",
 *       label: "Refresh timeline",
 *       key: "r",
 *       display: ["r"],
 *       description: "Refresh timeline",
 *       handler: () => void refetch(),
 *     },
 *   ]);
 *
 * Pass a freshly-built array each render (memoize with `useMemo` when the
 * actions are cheap to keep stable across renders -- not required for
 * correctness, only to avoid unnecessary registry churn).
 */

import { useMemo } from "react";
import { useRegisterCommands, type PaletteCommand } from "@/lib/command-registry";
import { useRegisterShortcut, type ShortcutBinding } from "@/hooks/use-register-shortcut";

export interface PageAction {
  /** Stable id, unique within the caller's own action set (palette command id). */
  id: string;
  /** Primary label shown (and matched against) in the command menu. */
  label: string;
  /** Extra terms matched against the palette query but not displayed as the label. */
  keywords?: string[];
  /** `KeyboardEvent.key` to match, case-sensitive (e.g. "r", "n", "?"). */
  key: string;
  /** Require Ctrl held. Default false. */
  ctrlKey?: boolean;
  /** Require Meta/Cmd held. Default false. */
  metaKey?: boolean;
  /** Require Shift held. Default false. */
  shiftKey?: boolean;
  /** Require Alt held. Default false. */
  altKey?: boolean;
  /** Human-readable key combo for display, e.g. `["r"]` or `["Ctrl", "Shift", "↑"]`. */
  display: string[];
  /** One-line description shown in the '?' help sheet's "On this page" section. */
  description: string;
  /** Invoked when the chord matches (shortcut) or the command is selected (palette). */
  handler: () => void;
  /** See `ShortcutBinding.allowWhenSuspended`. Default false. */
  allowWhenSuspended?: boolean;
}

/**
 * Register a page's standalone primary actions as BOTH page-scoped keyboard
 * shortcuts and command-palette Actions, from one declaration per action.
 */
export function usePageActions(actions: PageAction[]): void {
  const bindings = useMemo<ShortcutBinding[]>(
    () =>
      actions.map((a) => ({
        key: a.key,
        ctrlKey: a.ctrlKey,
        metaKey: a.metaKey,
        shiftKey: a.shiftKey,
        altKey: a.altKey,
        display: a.display,
        description: a.description,
        handler: a.handler,
        allowWhenSuspended: a.allowWhenSuspended,
      })),
    [actions],
  );
  useRegisterShortcut(bindings);

  const commands = useMemo<PaletteCommand[]>(
    () =>
      actions.map((a) => ({
        id: a.id,
        label: a.label,
        keywords: a.keywords,
        perform: a.handler,
        binding: a.display,
      })),
    [actions],
  );
  useRegisterCommands(commands);
}
