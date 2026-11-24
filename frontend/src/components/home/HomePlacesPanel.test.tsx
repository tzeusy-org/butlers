// @vitest-environment jsdom
/**
 * <HomePlacesPanel> wired through the real hooks and API module to an in-memory registry
 * that follows the server contract (whole-row CAS, 409 on a stale version). One workflow
 * covers list, add, edit, pause, remove, loading, load failure and the stale-edit 409.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import type { OwnerPlace } from "@/api/home-places.ts";
import { HomePlacesPanel } from "@/components/home/HomePlacesPanel.tsx";

const server = vi.hoisted(() => ({
  places: [] as Array<Record<string, unknown>>,
  failList: false,
  requests: [] as Array<{ method: string; path: string; body: unknown }>,
}));

vi.mock("@/api/client.ts", async () => {
  const actual = await vi.importActual<typeof import("@/api/client.ts")>("@/api/client.ts");
  const conflict = (code: string, message: string) => new actual.ApiError(code, message, 409);
  return {
    ...actual,
    apiFetch: vi.fn(async (path: string, options?: RequestInit) => {
      const method = options?.method ?? "GET";
      const body = options?.body ? JSON.parse(String(options.body)) : undefined;
      server.requests.push({ method, path, body });
      const [route, query] = path.split("?");
      const id = route.split("/")[3];
      if (method === "GET") {
        if (server.failList) throw new actual.ApiError("PLACES_UNAVAILABLE", "down", 503);
        return { data: structuredClone(server.places.filter((place) => !place.deleted)) };
      }
      if (method === "POST") {
        const place = { ...body, id: `place-${server.places.length + 1}`, version: 1, updated_at: "" };
        server.places.push(place);
        return { data: structuredClone(place) };
      }
      const place = server.places.find((candidate) => candidate.id === id && !candidate.deleted);
      if (!place) throw new actual.ApiError("PLACE_NOT_FOUND", "gone", 404);
      if (method === "PUT") {
        const { expected_version: expected, ...input } = body;
        if (expected !== place.version) throw conflict("VERSION_CONFLICT", "stale");
        Object.assign(place, input, { version: Number(place.version) + 1 });
        return { data: structuredClone(place) };
      }
      if (`expected_version=${place.version}` !== query) throw conflict("VERSION_CONFLICT", "stale");
      place.deleted = true;
      return undefined;
    }),
  };
});

const OFFICE: OwnerPlace = {
  id: "place-office",
  label: "Office",
  kind: "work",
  lat: 51.5,
  lon: -0.12,
  radius_m: 200,
  aliases: [],
  ssids: ["Office WiFi"],
  ha_zone: null,
  paused: false,
  version: 3,
  updated_at: "",
};

function renderPanel() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <HomePlacesPanel />
    </QueryClientProvider>,
  );
}

function fill(label: string, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

beforeEach(() => {
  server.places = [{ ...OFFICE, ssids: [...OFFICE.ssids] }];
  server.failList = false;
  server.requests = [];
});

afterEach(() => cleanup());

describe("HomePlacesPanel", () => {
  it("lists, adds, edits, pauses and removes places through the registry API", async () => {
    renderPanel();
    expect(screen.getByText("Loading places")).toBeTruthy();
    const list = await screen.findByTestId("home-places-list");
    expect(within(list).getByText("Office")).toBeTruthy();
    expect(within(list).getByText(/1 Wi-Fi network$/)).toBeTruthy();

    // Local validation refuses an over-precise coordinate and sends nothing.
    fill("Name", "Home");
    fireEvent.change(screen.getByLabelText("Kind"), { target: { value: "home" } });
    fill("Centre latitude", "12.345678");
    fill("Centre longitude", "76.5");
    fireEvent.click(screen.getByRole("button", { name: "Add place" }));
    expect(screen.getByRole("alert").textContent).toContain("five decimals");
    expect(server.requests.filter((request) => request.method !== "GET")).toEqual([]);

    fill("Centre latitude", "12.34567");
    fill("Wi-Fi networks, one per line (exact, case-sensitive)", "Home Net \n\n");
    fireEvent.click(screen.getByRole("button", { name: "Add place" }));
    await within(list).findByText("Home");
    expect(server.requests.find((request) => request.method === "POST")?.body).toEqual({
      label: "Home",
      kind: "home",
      lat: 12.34567,
      lon: 76.5,
      radius_m: 150,
      aliases: [],
      ssids: ["Home Net "],
      ha_zone: null,
      paused: false,
    });

    fireEvent.click(screen.getByRole("button", { name: "Edit Office" }));
    fill("Radius (m)", "400");
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await within(list).findByText(/400 m/);
    const put = server.requests.find((request) => request.method === "PUT");
    expect(put?.body).toMatchObject({ radius_m: 400, expected_version: 3, ssids: ["Office WiFi"] });

    fireEvent.click(screen.getByRole("button", { name: "Edit Office" }));
    fireEvent.click(screen.getByRole("button", { name: "Pause Office" }));
    await within(list).findByText("Paused");
    // Pausing the place open in the form leaves edit mode instead of keeping a stale version.
    expect(screen.getByRole("button", { name: "Add place" })).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Remove Home" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm removing Home" }));
    await waitFor(() => expect(within(list).queryByText("Home")).toBeNull());
  });

  it("asks before removing: the first click sends no DELETE, and Keep cancels", async () => {
    renderPanel();
    fireEvent.click(await screen.findByRole("button", { name: "Remove Office" }));
    expect(server.requests.some((request) => request.method === "DELETE")).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: "Keep" }));
    expect(screen.queryByRole("button", { name: "Confirm removing Office" })).toBeNull();
    expect(server.requests.some((request) => request.method === "DELETE")).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: "Edit Office" }));
    fireEvent.click(screen.getByRole("button", { name: "Remove Office" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm removing Office" }));
    await waitFor(() => expect(screen.queryByTestId("home-places-list")).toBeNull());
    expect(server.requests.filter((request) => request.method === "DELETE")).toHaveLength(1);
    // The removed place no longer holds the form in edit mode.
    expect(screen.getByRole("button", { name: "Add place" })).toBeTruthy();
  });

  it("shows a stale edit as a conflict and leaves edit mode instead of resubmitting", async () => {
    renderPanel();
    fireEvent.click(await screen.findByRole("button", { name: "Edit Office" }));
    server.places[0].version = 4; // another tab saved first
    fill("Radius (m)", "900");
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    expect((await screen.findByRole("alert")).textContent).toContain("changed since it was loaded");
    expect(screen.getByRole("button", { name: "Add place" })).toBeTruthy();
    expect(server.places[0].radius_m).toBe(200);
  });

  it("says when the list failed to load instead of showing it empty", async () => {
    server.failList = true;
    renderPanel();

    expect((await screen.findByTestId("home-places-error")).textContent).toContain("places");
    expect(screen.queryByText("No places yet.")).toBeNull();
    server.failList = false;
    fireEvent.click(screen.getByRole("button", { name: "Reload places" }));
    expect(await screen.findByTestId("home-places-list")).toBeTruthy();
  });
});
