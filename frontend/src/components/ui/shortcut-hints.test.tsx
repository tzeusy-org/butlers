// @vitest-environment jsdom

import { afterEach, describe, expect, it } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { axe, toHaveNoViolations } from "jest-axe";

import { ShortcutHints } from "@/components/ui/shortcut-hints";
import { ShortcutRegistryProvider, useRegisterShortcut } from "@/hooks/use-register-shortcut";
import { OPEN_SHORTCUT_HELP_EVENT } from "@/lib/shortcut-help";

expect.extend(toHaveNoViolations);

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

function PageShortcuts() {
  useRegisterShortcut([
    { key: "a", display: ["a"], description: "Approve selected", handler: () => {} },
    { key: "j", display: ["j"], description: "Next approval", handler: () => {} },
  ]);
  return null;
}

function openHelpSheet() {
  window.dispatchEvent(new CustomEvent(OPEN_SHORTCUT_HELP_EVENT));
}

describe("ShortcutHints — On this page section (bu-qvnce.11)", () => {
  let container: HTMLDivElement;
  let root: Root;

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  it("renders no 'On this page' heading when the current page registers no shortcuts", () => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);

    act(() => {
      root.render(
        <ShortcutRegistryProvider>
          <ShortcutHints />
        </ShortcutRegistryProvider>,
      );
    });
    act(() => openHelpSheet());

    expect(document.body.textContent).not.toContain("On this page");
  });

  it("renders an 'On this page' section listing the current page's registered shortcuts", () => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);

    act(() => {
      root.render(
        <ShortcutRegistryProvider>
          <PageShortcuts />
          <ShortcutHints />
        </ShortcutRegistryProvider>,
      );
    });
    act(() => openHelpSheet());

    expect(document.body.textContent).toContain("On this page");
    expect(document.body.textContent).toContain("Approve selected");
    expect(document.body.textContent).toContain("Next approval");
  });

  it("drops the section's rows when the registering page unmounts (e.g. navigating away)", () => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);

    act(() => {
      root.render(
        <ShortcutRegistryProvider>
          <PageShortcuts />
          <ShortcutHints />
        </ShortcutRegistryProvider>,
      );
    });
    act(() => openHelpSheet());
    expect(document.body.textContent).toContain("Approve selected");

    act(() => {
      root.render(
        <ShortcutRegistryProvider>
          <ShortcutHints />
        </ShortcutRegistryProvider>,
      );
    });

    expect(document.body.textContent).not.toContain("Approve selected");
    expect(document.body.textContent).not.toContain("On this page");
  });

  it("has no axe violations with the 'On this page' section open", async () => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);

    act(() => {
      root.render(
        <ShortcutRegistryProvider>
          <PageShortcuts />
          <ShortcutHints />
        </ShortcutRegistryProvider>,
      );
    });
    act(() => openHelpSheet());

    const dialog = document.querySelector('[role="dialog"]') as HTMLElement;
    expect(dialog).not.toBeNull();
    const results = await axe(dialog, {
      rules: { "color-contrast": { enabled: false } },
    });
    expect(results).toHaveNoViolations();
  });
});
