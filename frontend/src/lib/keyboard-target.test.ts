// @vitest-environment jsdom

import { describe, expect, it } from "vitest";

import { isEditableKeyboardTarget } from "@/lib/keyboard-target";

describe("isEditableKeyboardTarget", () => {
  it.each(["input", "textarea"])("returns true for a %s", (tagName) => {
    expect(isEditableKeyboardTarget(document.createElement(tagName))).toBe(true);
  });

  it("returns true for a contentEditable target", () => {
    // jsdom does not implement the browser's isContentEditable algorithm, so
    // exercise the guard directly rather than relying on a false-negative DOM
    // event assertion.
    const editable = document.createElement("div");
    Object.defineProperty(editable, "isContentEditable", { value: true });

    expect(isEditableKeyboardTarget(editable)).toBe(true);
  });

  it("returns false for a non-editable target", () => {
    expect(isEditableKeyboardTarget(document.body)).toBe(false);
  });

  it("returns false for non-HTMLElement event targets", () => {
    expect(isEditableKeyboardTarget(document)).toBe(false);
    expect(
      isEditableKeyboardTarget(document.createElementNS("http://www.w3.org/2000/svg", "svg")),
    ).toBe(false);
  });
});
