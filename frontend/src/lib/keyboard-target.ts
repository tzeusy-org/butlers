const EDITABLE_KEYBOARD_TAGS = new Set(["INPUT", "TEXTAREA"]);

/** Whether a keyboard event target accepts regular text input. */
export function isEditableKeyboardTarget(target: EventTarget | null): boolean {
  return (
    target instanceof HTMLElement &&
    Boolean(EDITABLE_KEYBOARD_TAGS.has(target.tagName) || target.isContentEditable)
  );
}
