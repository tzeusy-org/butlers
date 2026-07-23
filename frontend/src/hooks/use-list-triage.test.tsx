// @vitest-environment jsdom
/**
 * Tests for useListTriage -- the shared j/k roving-selection + act-key
 * pattern extracted from ApprovalsPage (bu-qvnce.11 slice 4).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";

import { useListTriage, type ListTriageVerb } from "@/hooks/use-list-triage";
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

function Harness({
  ids,
  selectedId,
  onSelect,
  verbs,
  onHints,
}: {
  ids: string[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  verbs?: ListTriageVerb[];
  onHints?: (descriptions: string[]) => void;
}) {
  const { hints } = useListTriage({ ids, selectedId, onSelect, verbs });
  onHints?.(hints.map((h) => h.description));
  return null;
}

function CommandReader({ onRead }: { onRead: (commands: PaletteCommand[]) => void }) {
  onRead(useCommandMenuActions());
  return null;
}

describe("useListTriage", () => {
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

  it("registers no bindings when the id list is empty", () => {
    let seen: string[] = [];
    act(() => {
      root.render(
        <Harness ids={[]} selectedId={null} onSelect={() => {}} onHints={(d) => (seen = d)} />,
      );
    });
    expect(seen).toEqual([]);
  });

  it("j moves the selection to the next id", () => {
    const onSelect = vi.fn();
    act(() => {
      root.render(<Harness ids={["a", "b", "c"]} selectedId="a" onSelect={onSelect} />);
    });
    act(() => press("j"));
    expect(onSelect).toHaveBeenCalledWith("b");
  });

  it("keeps j navigation active from a focused native row button", () => {
    const onSelect = vi.fn();
    act(() => {
      root.render(
        <>
          <Harness ids={["a", "b"]} selectedId="a" onSelect={onSelect} />
          <button type="button" data-testid="focused-row-button">
            Row action
          </button>
        </>,
      );
    });

    const button = container.querySelector<HTMLButtonElement>('[data-testid="focused-row-button"]');
    expect(button).not.toBeNull();
    button!.focus();
    act(() => {
      button!.dispatchEvent(
        new KeyboardEvent("keydown", { key: "j", bubbles: true, cancelable: true }),
      );
    });

    expect(onSelect).toHaveBeenCalledWith("b");
  });

  it("k moves the selection to the previous id", () => {
    const onSelect = vi.fn();
    act(() => {
      root.render(<Harness ids={["a", "b", "c"]} selectedId="b" onSelect={onSelect} />);
    });
    act(() => press("k"));
    expect(onSelect).toHaveBeenCalledWith("a");
  });

  it("clamps at the ends of the list instead of wrapping", () => {
    const onSelect = vi.fn();
    act(() => {
      root.render(<Harness ids={["a", "b"]} selectedId="b" onSelect={onSelect} />);
    });
    act(() => press("j"));
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("defaults to the first id when nothing is selected yet", () => {
    const onSelect = vi.fn();
    act(() => {
      root.render(<Harness ids={["a", "b"]} selectedId={null} onSelect={onSelect} />);
    });
    act(() => press("j"));
    expect(onSelect).toHaveBeenCalledWith("a");
  });

  it("wires a verb's key to its handler only while its row is selected", () => {
    const handler = vi.fn();
    const verbs: ListTriageVerb[] = [
      {
        key: "a",
        description: "Approve selected",
        handler,
        command: { id: "approve-selected", label: "Approve selected" },
      },
    ];
    act(() => {
      root.render(
        <Harness ids={["1", "2"]} selectedId={null} onSelect={() => {}} verbs={verbs} />,
      );
    });
    act(() => press("a"));
    expect(handler).not.toHaveBeenCalled();

    act(() => {
      root.render(<Harness ids={["1", "2"]} selectedId="1" onSelect={() => {}} verbs={verbs} />);
    });
    act(() => press("a"));
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it("hints reflect j/k plus the active verbs, in order", () => {
    let seen: string[] = [];
    const verbs: ListTriageVerb[] = [
      {
        key: "a",
        description: "Approve selected",
        handler: () => {},
        command: { id: "approve-selected", label: "Approve selected" },
      },
      {
        key: "d",
        description: "Deny selected",
        handler: () => {},
        command: { id: "deny-selected", label: "Deny selected" },
      },
    ];
    act(() => {
      root.render(
        <Harness
          ids={["1"]}
          selectedId="1"
          onSelect={() => {}}
          verbs={verbs}
          onHints={(d) => (seen = d)}
        />,
      );
    });
    expect(seen).toEqual(["Next item", "Previous item", "Approve selected", "Deny selected"]);
  });

  it("publishes its bindings to the shared '?' help sheet registry", () => {
    let entries: string[] = [];
    function HintReader({ onRead }: { onRead: (descriptions: string[]) => void }) {
      onRead(useShortcutHintEntries().map((b) => b.description));
      return null;
    }
    act(() => {
      root.render(
        <ShortcutRegistryProvider>
          <Harness ids={["1"]} selectedId="1" onSelect={() => {}} />
          <HintReader onRead={(d) => (entries = d)} />
        </ShortcutRegistryProvider>,
      );
    });
    expect(entries).toEqual(["Next item", "Previous item"]);
  });

  it("keeps pure j/k navigation out of the command palette", () => {
    let commands: PaletteCommand[] = [];
    act(() => {
      root.render(
        <CommandRegistryProvider>
          <ShortcutRegistryProvider>
            <Harness ids={["1"]} selectedId="1" onSelect={() => {}} />
            <CommandReader onRead={(next) => (commands = next)} />
          </ShortcutRegistryProvider>
        </CommandRegistryProvider>,
      );
    });

    expect(commands).toEqual([]);
  });

  it("emits a selected verb as the matching palette command with its binding", () => {
    const handler = vi.fn();
    let commands: PaletteCommand[] = [];

    act(() => {
      root.render(
        <CommandRegistryProvider>
          <ShortcutRegistryProvider>
            <Harness
              ids={["1"]}
              selectedId="1"
              onSelect={() => {}}
              verbs={[
                {
                  key: "a",
                  description: "Approve selected",
                  handler,
                  command: {
                    id: "approve-selected",
                    label: "Approve selected",
                    keywords: ["approval"],
                  },
                },
              ]}
            />
            <CommandReader onRead={(next) => (commands = next)} />
          </ShortcutRegistryProvider>
        </CommandRegistryProvider>,
      );
    });

    expect(commands).toHaveLength(1);
    expect(commands[0]).toMatchObject({
      id: "approve-selected",
      label: "Approve selected",
      keywords: ["approval"],
      binding: ["a"],
    });

    act(() => commands[0]?.perform());
    expect(handler).toHaveBeenCalledTimes(1);
  });
});
