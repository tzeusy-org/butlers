/** Event dispatched to open the keyboard-shortcuts help sheet (bu-86c4c.7). */
export const OPEN_SHORTCUT_HELP_EVENT = "open-shortcut-help";

/** Dispatch the event that opens the ShortcutHints help sheet. */
export function dispatchOpenShortcutHelp() {
  window.dispatchEvent(new CustomEvent(OPEN_SHORTCUT_HELP_EVENT));
}

/**
 * Event dispatched by the global 'c' shortcut to open (and focus) the
 * floating chat widget's composer (bu-0ynlk.13). Listened to by both
 * `FloatingChatWidget` (opens the panel if closed) and `WidgetPanel` (focuses
 * the composer either way — the mount-time focus choreography already
 * handles the "just opened" case, so this covers "already open").
 */
export const OPEN_CHAT_WIDGET_EVENT = "open-chat-widget";

/** Dispatch the event that opens/focuses the floating chat widget. */
export function dispatchOpenChatWidget() {
  window.dispatchEvent(new CustomEvent(OPEN_CHAT_WIDGET_EVENT));
}
