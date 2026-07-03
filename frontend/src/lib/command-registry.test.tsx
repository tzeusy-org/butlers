// @vitest-environment jsdom
/**
 * The per-page command registration API (bu-86c4c.7).
 *
 * Any mounted component can contribute Actions to the command menu for as
 * long as it stays mounted; unmounting removes its commands. Re-registering
 * with a new command set replaces (not appends to) that component's own
 * contribution, while other scopes' commands are unaffected.
 */
import { describe, expect, it } from "vitest";
import { render, cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

import {
  CommandRegistryProvider,
  useCommandMenuActions,
  useRegisterCommands,
  type PaletteCommand,
} from "@/lib/command-registry";

afterEach(cleanup);

function Registrar({ commands }: { commands: PaletteCommand[] }) {
  useRegisterCommands(commands);
  return null;
}

function Reader({ onRead }: { onRead: (commands: PaletteCommand[]) => void }) {
  onRead(useCommandMenuActions());
  return null;
}

describe("command-registry", () => {
  it("surfaces a registered command to readers", () => {
    let seen: PaletteCommand[] = [];
    const noop = () => {};

    render(
      <CommandRegistryProvider>
        <Registrar commands={[{ id: "a", label: "Approve next", perform: noop }]} />
        <Reader onRead={(c) => (seen = c)} />
      </CommandRegistryProvider>,
    );

    expect(seen.map((c) => c.id)).toEqual(["a"]);
  });

  it("aggregates commands from multiple independently-mounted scopes", () => {
    let seen: PaletteCommand[] = [];
    const noop = () => {};

    render(
      <CommandRegistryProvider>
        <Registrar commands={[{ id: "approve-next", label: "Approve next", perform: noop }]} />
        <Registrar commands={[{ id: "ack-issue", label: "Acknowledge issue", perform: noop }]} />
        <Reader onRead={(c) => (seen = c)} />
      </CommandRegistryProvider>,
    );

    expect(seen.map((c) => c.id).sort()).toEqual(["ack-issue", "approve-next"]);
  });

  it("removes a scope's commands when it unmounts, leaving other scopes intact", () => {
    let seen: PaletteCommand[] = [];
    const noop = () => {};

    const { rerender } = render(
      <CommandRegistryProvider>
        <Registrar commands={[{ id: "a", label: "A", perform: noop }]} />
        <Registrar commands={[{ id: "b", label: "B", perform: noop }]} />
        <Reader onRead={(c) => (seen = c)} />
      </CommandRegistryProvider>,
    );

    expect(seen.map((c) => c.id).sort()).toEqual(["a", "b"]);

    // Unmount only the first Registrar.
    rerender(
      <CommandRegistryProvider>
        <Registrar commands={[{ id: "b", label: "B", perform: noop }]} />
        <Reader onRead={(c) => (seen = c)} />
      </CommandRegistryProvider>,
    );

    expect(seen.map((c) => c.id)).toEqual(["b"]);
  });

  it("perform() runs the exact callback the command was registered with", () => {
    let ran = false;
    let seen: PaletteCommand[] = [];

    render(
      <CommandRegistryProvider>
        <Registrar
          commands={[
            {
              id: "approve-next",
              label: "Approve next",
              perform: () => {
                ran = true;
              },
            },
          ]}
        />
        <Reader onRead={(c) => (seen = c)} />
      </CommandRegistryProvider>,
    );

    seen[0]?.perform();
    expect(ran).toBe(true);
  });

  it("useCommandMenuActions returns [] outside a provider (no crash for unmounted consumers)", () => {
    let seen: PaletteCommand[] | null = null;
    render(<Reader onRead={(c) => (seen = c)} />);
    expect(seen).toEqual([]);
  });
});
