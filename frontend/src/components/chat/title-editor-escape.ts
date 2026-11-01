/** Marks the conversation rename input (ConversationActions.tsx). */
export const TITLE_EDITOR_ATTRIBUTE = "data-conversation-title-editor";

/**
 * True when an Escape keydown comes from a rename editor. Radix layers
 * (Sheet, Dialog) listen for Escape in the document capture phase, before
 * the editor's own handler runs, so a containing layer passes its
 * `onEscapeKeyDown` event here and calls `preventDefault()` to stay open.
 */
export function isTitleEditorEscape(event: KeyboardEvent): boolean {
  return event.target instanceof Element && event.target.hasAttribute(TITLE_EDITOR_ATTRIBUTE);
}
