// @vitest-environment jsdom
//
// RegisterCrossfade (bu-9qekw) is the opacity-only cross-fade wrapper that the
// register pills use to swap browse registers. MEMORY_LANGUAGE.md §8 specifies a
// 200ms `cubic-bezier(0.22, 1, 0.36, 1)` cross-fade on pill switch:
// opacity-only (no scale, no slide), and numerals must not re-animate.
//
// These assertions are on the MECHANISM, not pixel-level timing:
//   - the registered content carries the opacity transition (200ms + easing);
//   - switching registers fades the outgoing content out while fading the
//     incoming content in (both layers present mid-transition);
//   - the same register key does not re-mount its child (no numeral re-animation);
//   - prefers-reduced-motion drops the transition entirely (instant swap).

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";

import RegisterCrossfade, {
  REGISTER_CROSSFADE_DURATION_MS,
  REGISTER_CROSSFADE_EASING,
} from "@/components/memory/RegisterCrossfade";

// prefers-reduced-motion hook is mocked per-test so we can exercise both paths.
vi.mock("@/hooks/use-prefers-reduced-motion", () => ({
  usePrefersReducedMotion: vi.fn(() => false),
}));
import { usePrefersReducedMotion } from "@/hooks/use-prefers-reduced-motion";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

function layers(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>("[data-crossfade-layer]"));
}

describe("RegisterCrossfade", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.mocked(usePrefersReducedMotion).mockReturnValue(false);
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
    vi.restoreAllMocks();
  });

  it("exports the §8 timing contract: 200ms + the specified easing", () => {
    expect(REGISTER_CROSSFADE_DURATION_MS).toBe(200);
    expect(REGISTER_CROSSFADE_EASING).toBe("cubic-bezier(0.22, 1, 0.36, 1)");
  });

  it("applies an opacity-only transition with the §8 duration and easing", () => {
    act(() => {
      root.render(
        <RegisterCrossfade activeKey="facts">
          <div>Facts content</div>
        </RegisterCrossfade>,
      );
    });

    const [layer] = layers(container);
    expect(layer).toBeDefined();
    // Opacity-only: the transition is on opacity, with the exact duration/easing.
    expect(layer.style.transitionProperty).toBe("opacity");
    expect(layer.style.transitionDuration).toBe(`${REGISTER_CROSSFADE_DURATION_MS}ms`);
    expect(layer.style.transitionTimingFunction).toBe(REGISTER_CROSSFADE_EASING);
    // No scale / no slide: the layer must not declare a transform.
    expect(layer.style.transform).toBe("");
    expect(container.textContent).toContain("Facts content");
  });

  it("cross-fades on register switch: outgoing fades out, incoming fades in", () => {
    // Make requestAnimationFrame flush synchronously inside act() so the incoming
    // layer's fade-in (0 → 1) is observable deterministically.
    const rafSpy = vi
      .spyOn(globalThis, "requestAnimationFrame")
      .mockImplementation((cb: FrameRequestCallback) => {
        cb(0);
        return 0;
      });
    try {
      act(() => {
        root.render(
          <RegisterCrossfade activeKey="facts">
            <div>Facts content</div>
          </RegisterCrossfade>,
        );
      });

      // Switch register: both the old and new layer co-exist during the fade.
      act(() => {
        root.render(
          <RegisterCrossfade activeKey="rules">
            <div>Rules content</div>
          </RegisterCrossfade>,
        );
      });

      const present = layers(container);
      expect(present.length).toBe(2);
      expect(container.textContent).toContain("Facts content");
      expect(container.textContent).toContain("Rules content");

      const outgoing = present.find((l) => l.textContent?.includes("Facts content"));
      const incoming = present.find((l) => l.textContent?.includes("Rules content"));
      expect(outgoing?.style.opacity).toBe("0"); // fading out
      expect(incoming?.style.opacity).toBe("1"); // faded in
      // The incoming layer animates via the opacity transition (not an instant set).
      expect(incoming?.style.transitionProperty).toBe("opacity");
    } finally {
      rafSpy.mockRestore();
    }
  });

  it("does not re-mount the child when the active key is unchanged (numerals don't re-animate)", () => {
    const mountEffect = vi.fn();

    function Counter() {
      useEffect(() => {
        // Fires once per real mount; a same-key re-render must NOT re-trigger it.
        mountEffect();
      }, []);
      return <span>42</span>;
    }

    act(() => {
      root.render(
        <RegisterCrossfade activeKey="facts">
          <Counter />
        </RegisterCrossfade>,
      );
    });
    // Re-render with the same active key (e.g. a parent state change).
    act(() => {
      root.render(
        <RegisterCrossfade activeKey="facts">
          <Counter />
        </RegisterCrossfade>,
      );
    });

    expect(mountEffect).toHaveBeenCalledTimes(1);
  });

  it("swaps instantly with no transition under prefers-reduced-motion", () => {
    vi.mocked(usePrefersReducedMotion).mockReturnValue(true);

    act(() => {
      root.render(
        <RegisterCrossfade activeKey="facts">
          <div>Facts content</div>
        </RegisterCrossfade>,
      );
    });

    const [layer] = layers(container);
    expect(layer.style.transitionProperty).toBe("");
    expect(layer.style.opacity).toBe("1");
  });
});
