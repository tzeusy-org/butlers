// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";

vi.mock("@/hooks/use-education", () => ({
  useUpdateMindMapStatus: vi.fn(),
}));

import { TONE_COLORS } from "@/components/ui/StateDot";
import { useUpdateMindMapStatus } from "@/hooks/use-education";
import CurriculumActions from "./CurriculumActions";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

describe("CurriculumActions status tone", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    vi.mocked(useUpdateMindMapStatus).mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useUpdateMindMapStatus>);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.clearAllMocks();
  });

  it.each([
    ["active", "green"],
    ["completed", "green"],
    ["abandoned", "neutral"],
    ["unknown", "green"],
  ] as const)("uses the %s curriculum status tone", (status, tone) => {
    act(() => {
      root.render(<CurriculumActions mindMapId="mind-map-1" status={status} />);
    });

    const badge = container.querySelector("[data-slot='badge']") as HTMLElement | null;
    expect(badge).not.toBeNull();
    expect(badge!.getAttribute("data-variant")).toBe("outline");
    expect(badge!.textContent).toBe(status);
    expect(badge!.style.borderColor).toBe(TONE_COLORS[tone]);
    expect(badge!.style.color).toBe(TONE_COLORS[tone]);
  });
});
