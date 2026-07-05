// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";

import {
  ShortcutRegistryProvider,
  isShortcutTargetSuspended,
  useRegisterShortcut,
  useShortcutHintEntries,
  type ShortcutBinding,
} from "@/hooks/use-register-shortcut";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

function Registrar({ bindings }: { bindings: ShortcutBinding[] }) {
  useRegisterShortcut(bindings);
  return null;
}

function HintReader({ onRead }: { onRead: (bindings: ShortcutBinding[]) => void }) {
  onRead(useShortcutHintEntries());
  return null;
}

function press(key: string, init: KeyboardEventInit = {}) {
  window.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true, ...init }));
}

describe("useRegisterShortcut", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    window.__pendingGNav = false;
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
    vi.restoreAllMocks();
    document.querySelectorAll('[role="dialog"]').forEach((el) => el.remove());
  });

  it("fires the handler when its key matches", () => {
    const handler = vi.fn();
    act(() => {
      root.render(<Registrar bindings={[{ key: "a", display: ["a"], description: "Approve", handler }]} />);
    });

    act(() => press("a"));

    expect(handler).toHaveBeenCalledTimes(1);
  });

  it("does not fire for a non-matching key", () => {
    const handler = vi.fn();
    act(() => {
      root.render(<Registrar bindings={[{ key: "a", display: ["a"], description: "Approve", handler }]} />);
    });

    act(() => press("b"));

    expect(handler).not.toHaveBeenCalled();
  });

  it("requires an exact modifier match — a plain binding does not fire under Ctrl", () => {
    const handler = vi.fn();
    act(() => {
      root.render(<Registrar bindings={[{ key: "a", display: ["a"], description: "Approve", handler }]} />);
    });

    act(() => press("a", { ctrlKey: true }));

    expect(handler).not.toHaveBeenCalled();
  });

  it("matches a modifier chord exactly (Ctrl+Shift+ArrowUp)", () => {
    const handler = vi.fn();
    act(() => {
      root.render(
        <Registrar
          bindings={[
            {
              key: "ArrowUp",
              ctrlKey: true,
              shiftKey: true,
              display: ["Ctrl", "Shift", "↑"],
              description: "Previous",
              handler,
            },
          ]}
        />,
      );
    });

    // Missing shiftKey — should not fire.
    act(() => press("ArrowUp", { ctrlKey: true }));
    expect(handler).not.toHaveBeenCalled();

    act(() => press("ArrowUp", { ctrlKey: true, shiftKey: true }));
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it("suspends a plain-key binding while focus is in an input", () => {
    const handler = vi.fn();
    act(() => {
      root.render(<Registrar bindings={[{ key: "a", display: ["a"], description: "Approve", handler }]} />);
    });

    const input = document.createElement("input");
    document.body.appendChild(input);
    act(() => {
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "a", bubbles: true }));
    });

    expect(handler).not.toHaveBeenCalled();
    input.remove();
  });

  it("suspends in a <select> (bu-5o22a's SELECT gap)", () => {
    const handler = vi.fn();
    act(() => {
      root.render(<Registrar bindings={[{ key: "a", display: ["a"], description: "Approve", handler }]} />);
    });

    const select = document.createElement("select");
    document.body.appendChild(select);
    act(() => select.dispatchEvent(new KeyboardEvent("keydown", { key: "a", bubbles: true })));
    expect(handler).not.toHaveBeenCalled();
    select.remove();
  });

  // Note: contentEditable suspension (also part of bu-5o22a's guard) is
  // exercised by isShortcutTargetSuspended's own unit test below via a
  // `getter`-mocked element — jsdom does not implement the real
  // isContentEditable algorithm (it always reports false for a plain
  // `contentEditable = "true"` div), so a DOM-event-level test here would be
  // a false negative, not real coverage.

  it("suspends while any [role=dialog] overlay is open", () => {
    const handler = vi.fn();
    act(() => {
      root.render(<Registrar bindings={[{ key: "a", display: ["a"], description: "Approve", handler }]} />);
    });

    const dialog = document.createElement("div");
    dialog.setAttribute("role", "dialog");
    document.body.appendChild(dialog);

    act(() => press("a"));
    expect(handler).not.toHaveBeenCalled();

    dialog.remove();
    act(() => press("a"));
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it("fires even in an editable field / open overlay when allowWhenSuspended is set", () => {
    const handler = vi.fn();
    act(() => {
      root.render(
        <Registrar
          bindings={[
            {
              key: "ArrowUp",
              ctrlKey: true,
              shiftKey: true,
              display: ["Ctrl", "Shift", "↑"],
              description: "Previous",
              handler,
              allowWhenSuspended: true,
            },
          ]}
        />,
      );
    });

    const textarea = document.createElement("textarea");
    document.body.appendChild(textarea);
    act(() => {
      textarea.dispatchEvent(
        new KeyboardEvent("keydown", { key: "ArrowUp", ctrlKey: true, shiftKey: true, bubbles: true }),
      );
    });

    expect(handler).toHaveBeenCalledTimes(1);
    textarea.remove();
  });

  it("defers to a pending g-chord — does not fire while window.__pendingGNav is true", () => {
    const handler = vi.fn();
    act(() => {
      root.render(<Registrar bindings={[{ key: "a", display: ["a"], description: "Approve", handler }]} />);
    });

    window.__pendingGNav = true;
    act(() => press("a"));
    expect(handler).not.toHaveBeenCalled();

    window.__pendingGNav = false;
    act(() => press("a"));
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it("stops firing after the registering component unmounts", () => {
    const handler = vi.fn();
    act(() => {
      root.render(<Registrar bindings={[{ key: "a", display: ["a"], description: "Approve", handler }]} />);
    });
    act(() => {
      root.render(<div />);
    });

    act(() => press("a"));
    expect(handler).not.toHaveBeenCalled();
  });

  it("always dispatches through the latest bindings across re-renders (no stale closures)", () => {
    const calls: string[] = [];
    function Harness({ label }: { label: string }) {
      useRegisterShortcut([
        { key: "a", display: ["a"], description: "Approve", handler: () => calls.push(label) },
      ]);
      return null;
    }

    act(() => root.render(<Harness label="first" />));
    act(() => press("a"));
    act(() => root.render(<Harness label="second" />));
    act(() => press("a"));

    expect(calls).toEqual(["first", "second"]);
  });
});

describe("ShortcutRegistryProvider / useShortcutHintEntries", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  it("publishes a registered scope's bindings for the help sheet to read", () => {
    let seen: ShortcutBinding[] = [];
    act(() => {
      root.render(
        <ShortcutRegistryProvider>
          <Registrar
            bindings={[{ key: "j", display: ["j"], description: "Next", handler: () => {} }]}
          />
          <HintReader onRead={(b) => (seen = b)} />
        </ShortcutRegistryProvider>,
      );
    });

    expect(seen.map((b) => b.description)).toEqual(["Next"]);
  });

  it("aggregates bindings from multiple independently-mounted scopes", () => {
    let seen: ShortcutBinding[] = [];
    act(() => {
      root.render(
        <ShortcutRegistryProvider>
          <Registrar bindings={[{ key: "j", display: ["j"], description: "Next", handler: () => {} }]} />
          <Registrar bindings={[{ key: "k", display: ["k"], description: "Prev", handler: () => {} }]} />
          <HintReader onRead={(b) => (seen = b)} />
        </ShortcutRegistryProvider>,
      );
    });

    expect(seen.map((b) => b.description).sort()).toEqual(["Next", "Prev"]);
  });

  it("removes a scope's bindings when it unmounts", () => {
    let seen: ShortcutBinding[] = [];
    act(() => {
      root.render(
        <ShortcutRegistryProvider>
          <Registrar bindings={[{ key: "j", display: ["j"], description: "Next", handler: () => {} }]} />
          <HintReader onRead={(b) => (seen = b)} />
        </ShortcutRegistryProvider>,
      );
    });
    expect(seen).toHaveLength(1);

    act(() => {
      root.render(
        <ShortcutRegistryProvider>
          <HintReader onRead={(b) => (seen = b)} />
        </ShortcutRegistryProvider>,
      );
    });
    expect(seen).toHaveLength(0);
  });

  it("useShortcutHintEntries returns [] outside a provider (no crash)", () => {
    let seen: ShortcutBinding[] | null = null;
    act(() => {
      root.render(<HintReader onRead={(b) => (seen = b)} />);
    });
    expect(seen).toEqual([]);
  });

  it("still installs real key handling without a provider mounted", () => {
    const handler = vi.fn();
    act(() => {
      root.render(<Registrar bindings={[{ key: "a", display: ["a"], description: "Approve", handler }]} />);
    });
    act(() => press("a"));
    expect(handler).toHaveBeenCalledTimes(1);
  });
});

describe("isShortcutTargetSuspended", () => {
  afterEach(() => {
    document.querySelectorAll('[role="dialog"]').forEach((el) => el.remove());
  });

  it("returns false for a plain body target with no overlay open", () => {
    expect(isShortcutTargetSuspended(document.body)).toBe(false);
  });

  it("returns true for INPUT/TEXTAREA/SELECT targets", () => {
    expect(isShortcutTargetSuspended(document.createElement("input"))).toBe(true);
    expect(isShortcutTargetSuspended(document.createElement("textarea"))).toBe(true);
    expect(isShortcutTargetSuspended(document.createElement("select"))).toBe(true);
  });

  it("returns true for a contentEditable target (bu-5o22a)", () => {
    // jsdom doesn't implement the real isContentEditable algorithm (a plain
    // `el.contentEditable = "true"` still reports `isContentEditable ===
    // false`), so this stubs the getter directly to exercise the guard's own
    // branch rather than relying on jsdom's incomplete behavior.
    const editable = document.createElement("div");
    Object.defineProperty(editable, "isContentEditable", { value: true });
    expect(isShortcutTargetSuspended(editable)).toBe(true);
  });

  it("returns true when a [role=dialog] element exists anywhere in the document", () => {
    const dialog = document.createElement("div");
    dialog.setAttribute("role", "dialog");
    document.body.appendChild(dialog);
    expect(isShortcutTargetSuspended(document.body)).toBe(true);
  });
});
