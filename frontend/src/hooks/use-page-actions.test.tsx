// @vitest-environment jsdom
/**
 * Tests for usePageActions -- the single-declaration primary-action manifest
 * (bu-ep4ks.12). Mirrors use-list-triage.test.tsx's harness pattern.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";

import { usePageActions, type PageAction } from "@/hooks/use-page-actions";
import {
  CommandRegistryProvider,
  useCommandMenuActions,
  type PaletteCommand,
} from "@/lib/command-registry";
import { ShortcutRegistryProvider, useShortcutHintEntries } from "@/hooks/use-register-shortcut";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

function press(key: string) {
  window.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true }));
}

function Harness({ actions }: { actions: PageAction[] }) {
  usePageActions(actions);
  return null;
}

function CommandReader({ onRead }: { onRead: (commands: PaletteCommand[]) => void }) {
  onRead(useCommandMenuActions());
  return null;
}

function HintReader({ onRead }: { onRead: (descriptions: string[]) => void }) {
  onRead(useShortcutHintEntries().map((b) => b.description));
  return null;
}

describe("usePageActions", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    window.__pendingGNav = false;
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.restoreAllMocks();
  });

  it("invokes the handler when its key fires", () => {
    const handler = vi.fn();
    const actions: PageAction[] = [
      { id: "refresh", label: "Refresh", key: "r", display: ["r"], description: "Refresh", handler },
    ];
    act(() => {
      root.render(<Harness actions={actions} />);
    });
    act(() => press("r"));
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it("never fires a different action's key", () => {
    const refresh = vi.fn();
    const jump = vi.fn();
    const actions: PageAction[] = [
      { id: "refresh", label: "Refresh", key: "r", display: ["r"], description: "Refresh", handler: refresh },
      { id: "jump", label: "Jump to latest", key: "n", display: ["n"], description: "Jump to latest", handler: jump },
    ];
    act(() => {
      root.render(<Harness actions={actions} />);
    });
    act(() => press("n"));
    expect(jump).toHaveBeenCalledTimes(1);
    expect(refresh).not.toHaveBeenCalled();
  });

  it("publishes the exact same binding to the '?' help sheet as the registered shortcut", () => {
    let entries: string[] = [];
    const actions: PageAction[] = [
      {
        id: "refresh",
        label: "Refresh timeline",
        key: "r",
        display: ["r"],
        description: "Refresh timeline",
        handler: vi.fn(),
      },
    ];
    act(() => {
      root.render(
        <ShortcutRegistryProvider>
          <Harness actions={actions} />
          <HintReader onRead={(d) => (entries = d)} />
        </ShortcutRegistryProvider>,
      );
    });
    expect(entries).toEqual(["Refresh timeline"]);
  });

  it("registers a palette command whose binding matches the shortcut's display -- the pairing the docstring drift was missing", () => {
    const handler = vi.fn();
    let commands: PaletteCommand[] = [];
    const actions: PageAction[] = [
      {
        id: "timeline-refresh",
        label: "Refresh timeline",
        keywords: ["reload"],
        key: "r",
        display: ["r"],
        description: "Refresh timeline",
        handler,
      },
    ];
    act(() => {
      root.render(
        <CommandRegistryProvider>
          <ShortcutRegistryProvider>
            <Harness actions={actions} />
            <CommandReader onRead={(next) => (commands = next)} />
          </ShortcutRegistryProvider>
        </CommandRegistryProvider>,
      );
    });

    expect(commands).toHaveLength(1);
    expect(commands[0]).toMatchObject({
      id: "timeline-refresh",
      label: "Refresh timeline",
      keywords: ["reload"],
      binding: ["r"],
    });

    act(() => commands[0]?.perform());
    expect(handler).toHaveBeenCalledTimes(1);

    // Pressing the actual key fires the SAME handler the palette command
    // performs -- one declaration, both surfaces, no possible drift.
    act(() => press("r"));
    expect(handler).toHaveBeenCalledTimes(2);
  });

  it("registers no commands or bindings for an empty action list", () => {
    let commands: PaletteCommand[] = [];
    let entries: string[] = [];
    act(() => {
      root.render(
        <CommandRegistryProvider>
          <ShortcutRegistryProvider>
            <Harness actions={[]} />
            <CommandReader onRead={(next) => (commands = next)} />
            <HintReader onRead={(d) => (entries = d)} />
          </ShortcutRegistryProvider>
        </CommandRegistryProvider>,
      );
    });
    expect(commands).toEqual([]);
    expect(entries).toEqual([]);
  });
});
