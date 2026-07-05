// @vitest-environment jsdom
/**
 * Tests for ListTriageFooterHint -- the shared footer strip advertising a
 * list's j/k/act bindings inline on the page (bu-qvnce.11 slice 4).
 */

import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";

import { ListTriageFooterHint } from "@/components/ui/list-triage-footer";
import type { ShortcutBinding } from "@/hooks/use-register-shortcut";

function binding(overrides: Partial<ShortcutBinding> = {}): ShortcutBinding {
  return {
    key: "j",
    display: ["j"],
    description: "Next item",
    handler: () => {},
    ...overrides,
  };
}

describe("ListTriageFooterHint", () => {
  it("renders nothing when there are no bindings", () => {
    const html = renderToStaticMarkup(<ListTriageFooterHint bindings={[]} />);
    expect(html).toBe("");
  });

  it("renders one entry per binding with its display key and description", () => {
    const html = renderToStaticMarkup(
      <ListTriageFooterHint
        bindings={[
          binding(),
          binding({ key: "k", display: ["k"], description: "Previous item" }),
          binding({ key: "a", display: ["a"], description: "Approve selected" }),
        ]}
      />,
    );
    expect(html).toContain("Next item");
    expect(html).toContain("Previous item");
    expect(html).toContain("Approve selected");
    expect(html).toContain('role="note"');
  });

  it("shows only the first display segment as the key cap (single-key list-triage bindings)", () => {
    const html = renderToStaticMarkup(
      <ListTriageFooterHint bindings={[binding({ key: "a", display: ["a"], description: "Approve selected" })]} />,
    );
    const kbdCount = (html.match(/<kbd/g) ?? []).length;
    expect(kbdCount).toBe(1);
  });
});
