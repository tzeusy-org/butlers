// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import userEvent from "@testing-library/user-event";

import {
  ShortcutRegistryProvider,
  isShortcutTargetSuspended,
  useRegisterShortcut,
  useShortcutHintEntries,
  type ShortcutBinding,
} from "@/hooks/use-register-shortcut";
import { DisclosureRow } from "@/components/ui/DisclosureRow";

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

  it("yields to an event a focused control has already handled", () => {
    const handler = vi.fn();
    act(() => {
      root.render(
        <>
          <Registrar bindings={[{ key: "Enter", display: ["Enter"], description: "Open", handler }]} />
          <DisclosureRow expanded={false} onToggle={() => {}} data-testid="handled-control">
            Handled row
          </DisclosureRow>
        </>,
      );
    });

    const control = container.querySelector<HTMLElement>('[data-testid="handled-control"]');
    act(() => {
      control?.dispatchEvent(
        new KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true }),
      );
    });

    expect(handler).not.toHaveBeenCalled();
  });

  it("keeps nonactivation shortcuts active after focus moves to a native link", () => {
    const handler = vi.fn();
    act(() => {
      root.render(
        <>
          <Registrar
            bindings={[
              { key: "j", display: ["j"], description: "Next", handler },
              { key: "PageDown", display: ["PgDn"], description: "Later", handler },
            ]}
          />
          <a href="/butlers" data-testid="shortcut-link">
            Butler
          </a>
        </>,
      );
    });

    const link = container.querySelector<HTMLAnchorElement>('[data-testid="shortcut-link"]');
    expect(link).not.toBeNull();
    link!.focus();
    act(() => {
      link!.dispatchEvent(new KeyboardEvent("keydown", { key: "j", bubbles: true, cancelable: true }));
      link!.dispatchEvent(
        new KeyboardEvent("keydown", { key: "PageDown", bubbles: true, cancelable: true }),
      );
    });

    expect(handler).toHaveBeenCalledTimes(2);
  });

  it("keeps a declared shortcut active after focus moves to a native button", () => {
    const handler = vi.fn();
    act(() => {
      root.render(
        <>
          <Registrar
            bindings={[
              { key: "ArrowRight", display: ["→"], description: "Next", handler },
            ]}
          />
          <button type="button" data-testid="shortcut-button">
            Next
          </button>
        </>,
      );
    });

    const button = container.querySelector<HTMLButtonElement>('[data-testid="shortcut-button"]');
    expect(button).not.toBeNull();
    button!.focus();
    act(() => {
      button!.dispatchEvent(
        new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true, cancelable: true }),
      );
    });

    expect(handler).toHaveBeenCalledTimes(1);
  });

  it("leaves focused native button activation to Enter and Space exactly once", async () => {
    const shortcutHandler = vi.fn();
    const nativeActivation = vi.fn();
    act(() => {
      root.render(
        <>
          <Registrar
            bindings={[
              { key: "Enter", display: ["Enter"], description: "Open", handler: shortcutHandler },
              { key: " ", display: ["Space"], description: "Open", handler: shortcutHandler },
            ]}
          />
          <button type="button" data-testid="native-action" onClick={nativeActivation}>
            Open
          </button>
        </>,
      );
    });

    const button = container.querySelector<HTMLButtonElement>('[data-testid="native-action"]');
    expect(button).not.toBeNull();
    button!.focus();
    const user = userEvent.setup();
    await user.keyboard("{Enter}");
    await user.keyboard(" ");

    expect(nativeActivation).toHaveBeenCalledTimes(2);
    expect(shortcutHandler).not.toHaveBeenCalled();
  });

  // Note: contentEditable suspension (also part of bu-5o22a's guard) is
  // exercised by isShortcutTargetSuspended's own unit test below via a
  // `getter`-mocked element — jsdom does not implement the real
  // isContentEditable algorithm (it always reports false for a plain
  // `contentEditable = "true"` div), so a DOM-event-level test here would be
  // a false negative, not real coverage.

  it("suspends app-wide while a MODAL [role=dialog][aria-modal=true] overlay is open", () => {
    const handler = vi.fn();
    act(() => {
      root.render(<Registrar bindings={[{ key: "a", display: ["a"], description: "Approve", handler }]} />);
    });

    const modal = document.createElement("div");
    modal.setAttribute("role", "dialog");
    modal.setAttribute("aria-modal", "true");
    document.body.appendChild(modal);

    // Focus on the page (event target is window) — a true modal still suspends.
    act(() => press("a"));
    expect(handler).not.toHaveBeenCalled();

    modal.remove();
    act(() => press("a"));
    expect(handler).toHaveBeenCalledTimes(1);
  });

  // bu-hmdqz.11: the persistent floating chat widget is a NON-modal role=dialog
  // (no aria-modal). Before this fix, its mere presence killed every
  // page-scoped shortcut app-wide (approvals j/k/a/d/x, chronicles brackets,
  // sessions) while the '?' sheet still advertised them.
  it("chat open (non-modal dialog) + focus on the page → the page verb still fires", () => {
    const handler = vi.fn();
    act(() => {
      root.render(<Registrar bindings={[{ key: "a", display: ["a"], description: "Approve", handler }]} />);
    });

    const chat = document.createElement("div");
    chat.setAttribute("role", "dialog"); // NON-modal: no aria-modal
    document.body.appendChild(chat);

    // Focus is on the page (event target is window), not inside the chat.
    act(() => press("a"));
    expect(handler).toHaveBeenCalledTimes(1);

    chat.remove();
  });

  it("focus INSIDE a dialog (modal or not) never leaks to a page-scoped shortcut", () => {
    const handler = vi.fn();
    act(() => {
      root.render(<Registrar bindings={[{ key: "a", display: ["a"], description: "Approve", handler }]} />);
    });

    const chat = document.createElement("div");
    chat.setAttribute("role", "dialog"); // NON-modal floating chat widget
    const inner = document.createElement("button");
    chat.appendChild(inner);
    document.body.appendChild(chat);

    // Keystroke fired from an element inside the chat — its keystroke, not the page's.
    act(() => inner.dispatchEvent(new KeyboardEvent("keydown", { key: "a", bubbles: true })));
    expect(handler).not.toHaveBeenCalled();

    chat.remove();
  });

  it("a true modal (aria-modal=true) suspends everything regardless of focus", () => {
    const handler = vi.fn();
    act(() => {
      root.render(<Registrar bindings={[{ key: "a", display: ["a"], description: "Approve", handler }]} />);
    });

    const modal = document.createElement("div");
    modal.setAttribute("role", "dialog");
    modal.setAttribute("aria-modal", "true");
    const inside = document.createElement("button");
    modal.appendChild(inside);
    document.body.appendChild(modal);

    // Focus on the page ...
    act(() => press("a"));
    expect(handler).not.toHaveBeenCalled();
    // ... and focus inside the modal — both suspended.
    act(() => inside.dispatchEvent(new KeyboardEvent("keydown", { key: "a", bubbles: true })));
    expect(handler).not.toHaveBeenCalled();

    modal.remove();
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

  it("does not broadly suspend native action controls", () => {
    expect(isShortcutTargetSuspended(document.createElement("button"))).toBe(false);
    const link = document.createElement("a");
    link.href = "/butlers";
    expect(isShortcutTargetSuspended(link)).toBe(false);
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

  it("returns true when a MODAL [role=dialog][aria-modal=true] exists (page focus)", () => {
    const modal = document.createElement("div");
    modal.setAttribute("role", "dialog");
    modal.setAttribute("aria-modal", "true");
    document.body.appendChild(modal);
    expect(isShortcutTargetSuspended(document.body)).toBe(true);
  });

  it("returns FALSE for a page target while only a NON-modal [role=dialog] is open", () => {
    // The persistent floating chat widget is a non-modal dialog: it must not
    // suspend page-scoped shortcuts merely by being mounted (bu-hmdqz.11).
    const chat = document.createElement("div");
    chat.setAttribute("role", "dialog"); // no aria-modal
    document.body.appendChild(chat);
    expect(isShortcutTargetSuspended(document.body)).toBe(false);
  });

  it("returns true for a target INSIDE any dialog, modal or not (containment)", () => {
    const chat = document.createElement("div");
    chat.setAttribute("role", "dialog"); // non-modal
    const inner = document.createElement("button");
    chat.appendChild(inner);
    document.body.appendChild(chat);
    expect(isShortcutTargetSuspended(inner)).toBe(true);
  });
});
