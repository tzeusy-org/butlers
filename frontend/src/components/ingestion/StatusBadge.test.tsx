// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";

import { StatusBadge } from "./StatusBadge";
import type { IngestionEventStatus } from "@/api/index.ts";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

describe("StatusBadge", () => {
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

  function render(status: IngestionEventStatus, filterReason?: string | null) {
    act(() => {
      root.render(<StatusBadge status={status} filterReason={filterReason} />);
    });
  }

  it("renders 'ingested' with green badge", () => {
    render("ingested");
    expect(container.textContent).toContain("ingested");
    const badge = container.querySelector("[data-slot='badge']");
    expect(badge).not.toBeNull();
    expect(badge!.className).toContain("emerald");
  });

  it("renders 'filtered' with secondary badge", () => {
    render("filtered");
    expect(container.textContent).toContain("filtered");
    const badge = container.querySelector("[data-slot='badge']");
    expect(badge).not.toBeNull();
    expect(badge!.getAttribute("data-variant")).toBe("secondary");
  });

  it("renders 'error' with destructive badge", () => {
    render("error");
    expect(container.textContent).toContain("error");
    const badge = container.querySelector("[data-slot='badge']");
    expect(badge).not.toBeNull();
    expect(badge!.getAttribute("data-variant")).toBe("destructive");
  });

  it("renders 'replay_pending' with blue badge", () => {
    render("replay_pending");
    expect(container.textContent).toContain("replay pending");
    const badge = container.querySelector("[data-slot='badge']");
    expect(badge).not.toBeNull();
    expect(badge!.className).toContain("blue");
  });

  it("renders 'replay_complete' with green outline badge", () => {
    render("replay_complete");
    expect(container.textContent).toContain("replayed");
    const badge = container.querySelector("[data-slot='badge']");
    expect(badge).not.toBeNull();
    expect(badge!.className).toContain("emerald");
    expect(badge!.getAttribute("data-variant")).toBe("outline");
  });

  it("renders 'replay_failed' with red outline badge", () => {
    render("replay_failed");
    expect(container.textContent).toContain("replay failed");
    const badge = container.querySelector("[data-slot='badge']");
    expect(badge).not.toBeNull();
    expect(badge!.className).toContain("destructive");
    expect(badge!.getAttribute("data-variant")).toBe("outline");
  });

  it("wraps filtered badge in a tooltip trigger when filterReason is provided", () => {
    render("filtered", "Matched rule: no-spam");
    // Tooltip trigger wraps the badge in a span with cursor-help
    const trigger = container.querySelector("[data-slot='tooltip-trigger']");
    expect(trigger).not.toBeNull();
  });

  it("wraps error badge in a tooltip trigger when filterReason is provided", () => {
    render("error", "Processing exception: timeout");
    const trigger = container.querySelector("[data-slot='tooltip-trigger']");
    expect(trigger).not.toBeNull();
  });

  it("does NOT add tooltip trigger for filtered badge without filterReason", () => {
    render("filtered", null);
    const trigger = container.querySelector("[data-slot='tooltip-trigger']");
    expect(trigger).toBeNull();
  });

  it("does NOT add tooltip trigger for ingested badge", () => {
    render("ingested", "some reason");
    const trigger = container.querySelector("[data-slot='tooltip-trigger']");
    expect(trigger).toBeNull();
  });
});
