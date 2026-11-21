// @vitest-environment jsdom
/**
 * Tests for <HomeCriticalEntitiesPanel>: the owner marks explicit entities critical, the panel
 * runs nothing, states what the gate cannot cover, and a failed read is never shown as empty.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { HomeCriticalEntitiesPanel } from "@/components/home/HomeCriticalEntitiesPanel.tsx";

const saveEntity = vi.fn();
const removeEntity = vi.fn();
const refetch = vi.fn();
const state = vi.hoisted(() => ({ registry: {} as Record<string, unknown> }));

const idle = (mutate: unknown) => ({ mutate, isPending: false, isError: false });

vi.mock("@/hooks/use-home-critical-entities.ts", () => ({
  useHomeCriticalEntities: () => ({ refetch, ...state.registry }),
  useSaveCriticalEntity: () => idle(saveEntity),
  useDeleteCriticalEntity: () => idle(removeEntity),
}));

const ENTRY = {
  entity_id: "switch.freezer",
  owner_label: "Freezer plug",
  added_by: "owner",
  added_at: "2026-11-21T08:00:00Z",
};

beforeEach(() => {
  state.registry = { data: [ENTRY] };
});

afterEach(() => {
  cleanup();
  saveEntity.mockReset();
  removeEntity.mockReset();
  refetch.mockReset();
});

describe("HomeCriticalEntitiesPanel", () => {
  it("lists a critical entity with its own name and an unmark control", () => {
    render(<HomeCriticalEntitiesPanel />);

    const row = screen.getByTestId("home-critical-entities-list").textContent ?? "";
    expect(row).toContain("Freezer plug");
    expect(row).toContain("switch.freezer");
    expect(screen.getByRole("button", { name: "Unmark Freezer plug as critical" })).toBeTruthy();
  });

  it("states the limits of the gate and that unmarking never releases a waiting call", () => {
    render(<HomeCriticalEntitiesPanel />);

    const limits = screen.getByTestId("home-critical-entities-limits").textContent ?? "";
    expect(limits).toContain("only calls Butlers sends");
    expect(limits).toContain("on their own triggers");
    expect(limits).toContain("mark the helper itself");
    expect(screen.getByText(/already waiting for approval keeps waiting/)).toBeTruthy();
  });

  it("saves the trimmed entity and name and offers no run or approve control", () => {
    render(<HomeCriticalEntitiesPanel />);
    fireEvent.change(screen.getByLabelText("Entity ID"), { target: { value: " light.hall " } });
    fireEvent.change(screen.getByLabelText("Your name for it"), { target: { value: " Hall " } });
    fireEvent.click(screen.getByRole("button", { name: "Mark critical" }));

    expect(saveEntity.mock.calls[0][0]).toEqual({ entity_id: "light.hall", owner_label: "Hall" });
    expect(screen.queryByRole("button", { name: /run|approve|secure all/i })).toBeNull();
  });

  it.each(["all", "switch.a, switch.b", "Switch.Freezer", "freezer", ""])(
    "refuses %j as an entity without calling the API",
    (value) => {
      render(<HomeCriticalEntitiesPanel />);
      fireEvent.change(screen.getByLabelText("Entity ID"), { target: { value } });
      fireEvent.change(screen.getByLabelText("Your name for it"), { target: { value: "Name" } });
      fireEvent.click(screen.getByRole("button", { name: "Mark critical" }));

      expect(screen.getByRole("alert").textContent).toContain("one entity ID");
      expect(saveEntity).not.toHaveBeenCalled();
    },
  );

  it("refuses a blank name without calling the API", () => {
    render(<HomeCriticalEntitiesPanel />);
    fireEvent.change(screen.getByLabelText("Entity ID"), { target: { value: "light.hall" } });
    fireEvent.change(screen.getByLabelText("Your name for it"), { target: { value: "   " } });
    fireEvent.click(screen.getByRole("button", { name: "Mark critical" }));

    expect(screen.getByRole("alert").textContent).toContain("1 to 80");
    expect(saveEntity).not.toHaveBeenCalled();
  });

  it("unmarks by entity id", () => {
    render(<HomeCriticalEntitiesPanel />);
    fireEvent.click(screen.getByRole("button", { name: "Unmark Freezer plug as critical" }));

    expect(removeEntity.mock.calls[0][0]).toBe("switch.freezer");
  });

  it("says nothing is critical only after a successful empty read", () => {
    state.registry = { data: [] };
    render(<HomeCriticalEntitiesPanel />);

    expect(screen.getByText(/No entity is marked critical/)).toBeTruthy();
  });

  it("names a failed read as degraded, never as an empty registry, and offers a retry", () => {
    state.registry = { isError: true };
    render(<HomeCriticalEntitiesPanel />);

    const note = screen.getByTestId("home-critical-entities-error");
    expect(note.textContent).toContain("couldn't load");
    expect(note.textContent).toContain("every call as critical");
    expect(screen.queryByText(/No entity is marked critical/)).toBeNull();
    expect(screen.queryByTestId("home-critical-entities-list")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(refetch).toHaveBeenCalledTimes(1);
  });
});
