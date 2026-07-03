/** Event dispatched to open the keyboard-shortcuts help sheet (bu-86c4c.7). */
export const OPEN_SHORTCUT_HELP_EVENT = "open-shortcut-help";

/** Dispatch the event that opens the ShortcutHints help sheet. */
export function dispatchOpenShortcutHelp() {
  window.dispatchEvent(new CustomEvent(OPEN_SHORTCUT_HELP_EVENT));
}
