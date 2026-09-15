// @vitest-environment jsdom
/**
 * ContextChip — the removable pre-send page-context chip (bu-0ynlk.4).
 *
 * Covers:
 *  - renders the label; clicking the toggle expands the exact JSON payload
 *  - Backspace, Delete, and the × button all detach (call onToggleIncluded)
 *  - policy "none" renders a static notice with no remove affordance
 *  - a detached chip renders a re-attach affordance that calls onToggleIncluded
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { ContextChip } from "./ContextChip.tsx";
import type { PageContext } from "@/api/types.ts";

afterEach(() => cleanup());

const PAYLOAD: PageContext = {
  route: "/sessions/sess-1",
  visible_resource: { kind: "session", id: "sess-1" },
};

describe("ContextChip — attached (snapshot policy)", () => {
  it("renders the label and reveals the exact payload once expanded", () => {
    render(
      <ContextChip
        id="chip-1"
        label="Session sess-1"
        policy="snapshot"
        payload={PAYLOAD}
        included
        onToggleIncluded={vi.fn()}
      />,
    );

    expect(screen.getByText("Session sess-1")).toBeDefined();
    expect(screen.queryByTestId("context-chip-payload")).toBeNull();

    fireEvent.click(screen.getByTestId("context-chip-toggle"));

    const payloadEl = screen.getByTestId("context-chip-payload");
    expect(JSON.parse(payloadEl.textContent ?? "null")).toEqual(PAYLOAD);
  });

  it("labels a ref-only chip distinctly", () => {
    render(
      <ContextChip
        id="chip-ref"
        label="Models"
        policy="ref-only"
        payload={{ route: "/settings/models" }}
        included
        onToggleIncluded={vi.fn()}
      />,
    );
    expect(screen.getByText("Models (reference only)")).toBeDefined();
  });

  it("detaches on Backspace", () => {
    const onToggle = vi.fn();
    render(
      <ContextChip
        id="chip-1"
        label="Session sess-1"
        policy="snapshot"
        payload={PAYLOAD}
        included
        onToggleIncluded={onToggle}
      />,
    );
    fireEvent.keyDown(screen.getByTestId("context-chip-toggle"), { key: "Backspace" });
    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it("detaches on Delete", () => {
    const onToggle = vi.fn();
    render(
      <ContextChip
        id="chip-1"
        label="Session sess-1"
        policy="snapshot"
        payload={PAYLOAD}
        included
        onToggleIncluded={onToggle}
      />,
    );
    fireEvent.keyDown(screen.getByTestId("context-chip-toggle"), { key: "Delete" });
    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it("detaches on clicking the × remove button", () => {
    const onToggle = vi.fn();
    render(
      <ContextChip
        id="chip-1"
        label="Session sess-1"
        policy="snapshot"
        payload={PAYLOAD}
        included
        onToggleIncluded={onToggle}
      />,
    );
    fireEvent.click(screen.getByTestId("context-chip-remove"));
    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it("does not detach on unrelated keys", () => {
    const onToggle = vi.fn();
    render(
      <ContextChip
        id="chip-1"
        label="Session sess-1"
        policy="snapshot"
        payload={PAYLOAD}
        included
        onToggleIncluded={onToggle}
      />,
    );
    fireEvent.keyDown(screen.getByTestId("context-chip-toggle"), { key: "Enter" });
    expect(onToggle).not.toHaveBeenCalled();
  });
});

describe("ContextChip — detached", () => {
  it("renders a re-attach affordance that calls onToggleIncluded", () => {
    const onToggle = vi.fn();
    render(
      <ContextChip
        id="chip-1"
        label="Session sess-1"
        policy="snapshot"
        payload={PAYLOAD}
        included={false}
        onToggleIncluded={onToggle}
      />,
    );
    const chip = screen.getByTestId("context-chip");
    expect(chip.getAttribute("data-included")).toBe("false");
    expect(screen.queryByTestId("context-chip-payload")).toBeNull();
    fireEvent.click(chip);
    expect(onToggle).toHaveBeenCalledTimes(1);
  });
});

describe("ContextChip — policy 'none'", () => {
  it("renders a static notice with no removal affordance", () => {
    render(
      <ContextChip
        id="chip-1"
        label="Secrets"
        policy="none"
        payload={null}
        included={false}
        onToggleIncluded={vi.fn()}
      />,
    );
    const chip = screen.getByTestId("context-chip");
    expect(chip.getAttribute("data-policy")).toBe("none");
    expect(chip.textContent).toContain("Context not attached on this page.");
    expect(screen.queryByTestId("context-chip-remove")).toBeNull();
    expect(screen.queryByTestId("context-chip-toggle")).toBeNull();
  });
});
