// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  getHomeAtmosphereCurrent: vi.fn(),
  updateHomeAtmosphereLocation: vi.fn(),
}));

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return {
    ...actual,
    getHomeAtmosphereCurrent: apiMocks.getHomeAtmosphereCurrent,
    updateHomeAtmosphereLocation: apiMocks.updateHomeAtmosphereLocation,
  };
});

import * as homeHooks from "./use-home.ts";

afterEach(() => {
  vi.clearAllMocks();
});

function renderHookHarness() {
  const useHomeAtmosphereCurrent = (
    homeHooks as unknown as {
      useHomeAtmosphereCurrent?: () => {
        data?: { configured: boolean };
        isLoading: boolean;
      };
    }
  ).useHomeAtmosphereCurrent;
  const useUpdateHomeAtmosphereLocation = (
    homeHooks as unknown as {
      useUpdateHomeAtmosphereLocation?: () => {
        mutate: (coordinates: { latitude: number; longitude: number }) => void;
      };
    }
  ).useUpdateHomeAtmosphereLocation;

  expect(useHomeAtmosphereCurrent).toBeTypeOf("function");
  expect(useUpdateHomeAtmosphereLocation).toBeTypeOf("function");
  if (!useHomeAtmosphereCurrent || !useUpdateHomeAtmosphereLocation) return;

  function Harness() {
    const current = useHomeAtmosphereCurrent();
    const update = useUpdateHomeAtmosphereLocation();
    return (
      <>
        <output>{current.isLoading ? "loading" : String(current.data?.configured)}</output>
        <button
          type="button"
          onClick={() => update.mutate({ latitude: 1.3521, longitude: 103.8198 })}
        >
          Save
        </button>
      </>
    );
  }

  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <Harness />
    </QueryClientProvider>,
  );
}

describe("home atmosphere hooks", () => {
  it("fetches current configuration and refetches it after a successful location update", async () => {
    apiMocks.getHomeAtmosphereCurrent.mockResolvedValue({
      configured: false,
      latitude: null,
      longitude: null,
      stale: false,
      source_error: false,
      last_error: null,
    });
    apiMocks.updateHomeAtmosphereLocation.mockResolvedValue({
      latitude: 1.3521,
      longitude: 103.8198,
    });

    renderHookHarness();

    await screen.findByText("false");
    await act(async () => {
      screen.getByRole("button", { name: "Save" }).click();
    });

    expect(apiMocks.updateHomeAtmosphereLocation).toHaveBeenCalledWith({
      latitude: 1.3521,
      longitude: 103.8198,
    });
    await waitFor(() => {
      expect(apiMocks.getHomeAtmosphereCurrent).toHaveBeenCalledTimes(2);
    });
  });
});
