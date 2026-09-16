// @vitest-environment jsdom

import { createRef, useEffect } from "react";
import { act, cleanup, render, screen } from "@testing-library/react";
import {
  MemoryRouter,
  Route,
  Routes,
  useLocation,
  useNavigate,
  type NavigateFunction,
} from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ShellScrollContext,
  type ShellScrollContextValue,
} from "@/components/layout/ShellScrollContext";
import {
  SHELL_SCROLL_OWNER_ATTRIBUTE,
  SHELL_SCROLL_OWNER_VALUE,
  useShellScrollMemory,
} from "@/hooks/use-shell-scroll-memory";

interface HarnessControls {
  navigate: NavigateFunction;
  pathname: string;
}

function RouteProbe({ onReady }: { onReady: (controls: HarnessControls) => void }) {
  useShellScrollMemory();
  const navigate = useNavigate();
  const location = useLocation();

  useEffect(() => {
    onReady({ navigate, pathname: location.pathname });
  }, [location.pathname, navigate, onReady]);

  const ownsInnerScroll = location.pathname === "/owned";
  return (
    <div
      {...(ownsInnerScroll
        ? { [SHELL_SCROLL_OWNER_ATTRIBUTE]: SHELL_SCROLL_OWNER_VALUE }
        : {})}
      data-testid="route-content"
    >
      {location.pathname}
    </div>
  );
}

function renderHarness(initialPath = "/list") {
  let controls: HarnessControls | null = null;
  const onReady = (next: HarnessControls) => {
    controls = next;
  };
  const mainScrollContainerRef = createRef<HTMLElement>();

  function Harness() {
    return (
      <ShellScrollContext.Provider
        value={{ mainScrollContainerRef } satisfies ShellScrollContextValue}
      >
        <main ref={mainScrollContainerRef}>
          <Routes>
            <Route path="*" element={<RouteProbe onReady={onReady} />} />
          </Routes>
        </main>
      </ShellScrollContext.Provider>
    );
  }

  render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Harness />
    </MemoryRouter>,
  );

  return {
    main: () => screen.getByRole("main"),
    controls: () => {
      if (!controls) throw new Error("route controls are not ready");
      return controls;
    },
  };
}

describe("useShellScrollMemory", () => {
  const frames: FrameRequestCallback[] = [];
  let frameId = 0;

  beforeEach(() => {
    frames.length = 0;
    frameId = 0;
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
      frames.push(callback);
      frameId += 1;
      return frameId;
    });
    vi.stubGlobal("cancelAnimationFrame", vi.fn());
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("retries a POP restore when the first frame cannot reach the saved offset", async () => {
    const { main, controls } = renderHarness();
    let contentHeight = 1_200;
    Object.defineProperty(main(), "scrollHeight", {
      configurable: true,
      get: () => contentHeight,
    });
    Object.defineProperty(main(), "clientHeight", {
      configurable: true,
      get: () => 500,
    });

    main().scrollTop = 640;
    main().dispatchEvent(new Event("scroll"));

    await act(async () => {
      controls().navigate("/detail");
    });
    expect(main().scrollTop).toBe(0);

    contentHeight = 1_000;
    await act(async () => {
      controls().navigate(-1);
    });

    expect(frames).toHaveLength(1);
    expect(main().scrollTop).toBe(0);

    contentHeight = 1_200;
    await act(async () => {
      frames.shift()!(0);
    });

    expect(main().scrollTop).toBe(640);
    expect(frames).toHaveLength(0);
  });

  it("does not write the shell offset for a route that owns an inner scroller", async () => {
    const { main, controls } = renderHarness();
    main().scrollTop = 172;
    main().dispatchEvent(new Event("scroll"));

    await act(async () => {
      controls().navigate("/owned");
    });

    expect(main().scrollTop).toBe(172);
  });

  it("starts a fresh mount at the top", () => {
    const { main } = renderHarness();
    expect(main().scrollTop).toBe(0);
  });
});
