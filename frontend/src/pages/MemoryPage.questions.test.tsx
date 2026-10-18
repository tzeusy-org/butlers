// @vitest-environment jsdom
/** The "Open questions" palette verb on /memory (bu-q7vx1q.73). */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, useLocation } from "react-router";

import MemoryPage from "@/pages/MemoryPage";
import {
  CommandRegistryProvider,
  useCommandMenuActions,
  type PaletteCommand,
} from "@/lib/command-registry";

vi.mock("@/components/memory/MemoryOverture", () => ({ default: () => null }));
vi.mock("@/components/memory/MemoryBrowser", () => ({ default: () => null }));
vi.mock("@/components/memory/AttentionRail", () => ({ default: () => null }));
vi.mock("@/components/memory/HousekeepingBand", () => ({ default: () => null }));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

let commands: PaletteCommand[] = [];
let search = "";
function Probe({ onRead }: { onRead: (commands: PaletteCommand[], search: string) => void }) {
  onRead(useCommandMenuActions(), useLocation().search);
  return null;
}
const read = (nextCommands: PaletteCommand[], nextSearch: string) => {
  commands = nextCommands;
  search = nextSearch;
};

describe("MemoryPage palette verb", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    commands = [];
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  it("registers Open questions, and selecting it opens the register and clears a search", () => {
    act(() => {
      root.render(
        <MemoryRouter initialEntries={["/memory?q=coffee&offset=50"]}>
          <CommandRegistryProvider>
            <Probe onRead={read} />
            <MemoryPage />
          </CommandRegistryProvider>
        </MemoryRouter>,
      );
    });

    const verb = commands.find((c) => c.id === "memory-open-questions");
    expect(verb?.label).toBe("Open questions");
    expect(verb?.binding).toBeUndefined();

    act(() => verb!.perform());

    const params = new URLSearchParams(search);
    expect(params.get("register")).toBe("questions");
    expect(params.has("q")).toBe(false);
    expect(params.has("offset")).toBe(false);
  });

  it("unregisters the verb when the page unmounts", () => {
    act(() => {
      root.render(
        <MemoryRouter>
          <CommandRegistryProvider>
            <Probe onRead={read} />
            <MemoryPage />
          </CommandRegistryProvider>
        </MemoryRouter>,
      );
    });
    expect(commands.some((c) => c.id === "memory-open-questions")).toBe(true);

    act(() => {
      root.render(
        <MemoryRouter>
          <CommandRegistryProvider>
            <Probe onRead={read} />
          </CommandRegistryProvider>
        </MemoryRouter>,
      );
    });

    expect(commands.some((c) => c.id === "memory-open-questions")).toBe(false);
  });
});
