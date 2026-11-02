// @vitest-environment jsdom
/**
 * <HomePresenceOwnerPanel> wired through the real hooks and API module to an in-memory
 * setting that follows the server contract (identical list is a no-op, stale changed list is
 * 409, unobserved ids are one count-only 422). One workflow covers loading, unconfigured,
 * choose, local validation, unknown reference, stale conflict, clear and load failure.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { HomePresenceOwnerPanel } from "@/components/home/HomePresenceOwnerPanel.tsx";

const server = vi.hoisted(() => ({
  stored: null as string[] | null,
  version: 0,
  observed: new Set<string>(),
  failGet: false,
  holdGet: null as Promise<void> | null,
  requests: [] as Array<{ method: string; body: unknown }>,
}));

vi.mock("@/api/client.ts", async () => {
  const actual = await vi.importActual<typeof import("@/api/client.ts")>("@/api/client.ts");
  const snapshot = () => {
    const ids = [...(server.stored ?? [])].sort();
    return { configured: ids.length > 0, owner_entities: ids, version: server.version };
  };
  return {
    ...actual,
    apiFetch: vi.fn(async (path: string, options?: RequestInit) => {
      expect(path).toBe("/home/settings/presence/owner-entities");
      const method = options?.method ?? "GET";
      const body = options?.body ? JSON.parse(String(options.body)) : undefined;
      server.requests.push({ method, body });
      if (method === "GET") {
        if (server.holdGet) await server.holdGet;
        if (server.failGet) {
          throw new actual.ApiError("PRESENCE_CONFIG_UNAVAILABLE", "down", 503);
        }
        return { data: snapshot() };
      }
      const submitted = [...body.owner_entities].sort();
      const missing = submitted.filter((id: string) => !server.observed.has(id)).length;
      if (missing > 0) {
        throw new actual.ApiError("INVALID_REFERENCE", "unseen", 422, {
          invalid_reference_count: missing,
        });
      }
      if (JSON.stringify(submitted) === JSON.stringify(snapshot().owner_entities)) {
        return { data: snapshot() };
      }
      if (body.expected_version !== server.version) {
        throw new actual.ApiError("VERSION_CONFLICT", "stale", 409);
      }
      server.stored = submitted;
      server.version += 1;
      return { data: snapshot() };
    }),
  };
});

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <HomePresenceOwnerPanel />
    </QueryClientProvider>,
  );
}

const textarea = () => screen.getByLabelText(/Entity IDs, one per line/) as HTMLTextAreaElement;
const type = (value: string) => fireEvent.change(textarea(), { target: { value } });
const submit = () => fireEvent.click(screen.getByRole("button", { name: "Save entities" }));

describe("HomePresenceOwnerPanel", () => {
  beforeEach(() => {
    server.stored = null;
    server.version = 0;
    server.observed = new Set(["person.owner", "sensor.owner_room"]);
    server.failGet = false;
    server.holdGet = null;
    server.requests = [];
  });

  afterEach(cleanup);

  it("chooses, refuses, resolves a conflict and clears the owner entities", async () => {
    let release: () => void = () => {};
    server.holdGet = new Promise<void>((resolve) => {
      release = resolve;
    });
    renderPanel();
    expect(screen.getByText("Loading presence settings")).toBeTruthy();
    release();
    server.holdGet = null;
    await screen.findByTestId("home-presence-unconfigured");

    // Choose: local validation refuses before any request; ids are never trimmed or folded.
    fireEvent.click(screen.getByRole("button", { name: "Choose entities" }));
    type("Person.Owner\n sensor.owner_room");
    submit();
    expect(screen.getByRole("alert").textContent).toMatch(/exact Home Assistant entity IDs/);
    type("person.owner\nperson.owner");
    submit();
    expect(screen.getByRole("alert").textContent).toMatch(/may appear once/);
    expect(server.requests.filter((request) => request.method === "PUT")).toEqual([]);

    // An unobserved id is reported by count only and changes nothing.
    type("person.owner\nperson.never_seen");
    submit();
    const unseen = await screen.findByText(/1 of these entities have not been seen/);
    expect(unseen.textContent).not.toContain("never_seen");
    expect(server.version).toBe(0);

    type("sensor.owner_room\nperson.owner");
    submit();
    const saved = await screen.findByTestId("home-presence-entities");
    expect(saved.textContent).toBe("person.ownersensor.owner_room");
    expect(server.requests.at(-2)).toEqual({
      method: "PUT",
      body: { owner_entities: ["sensor.owner_room", "person.owner"], expected_version: 0 },
    });

    // Someone else changes the setting; a stale changed save is a 409 that leaves edit mode.
    fireEvent.click(screen.getByRole("button", { name: "Edit entities" }));
    server.stored = ["person.owner"];
    server.version = 5;
    type("sensor.owner_room");
    submit();
    expect(await screen.findByText(/changed since they were loaded/)).toBeTruthy();
    await waitFor(() =>
      expect(screen.getByTestId("home-presence-entities").textContent).toBe("person.owner"),
    );

    // Clearing restores the unconfigured state at the next version.
    fireEvent.click(screen.getByRole("button", { name: "Edit entities" }));
    expect(textarea().value).toBe("person.owner");
    type("");
    submit();
    await screen.findByTestId("home-presence-unconfigured");
    expect(server.stored).toEqual([]);
    expect(server.version).toBe(6);
  });

  it("shows a load failure without claiming the setting is empty", async () => {
    server.failGet = true;
    renderPanel();
    expect(await screen.findByTestId("home-presence-error")).toBeTruthy();
    expect(screen.queryByTestId("home-presence-unconfigured")).toBeNull();
    server.failGet = false;
    fireEvent.click(screen.getByRole("button", { name: "Reload" }));
    await screen.findByTestId("home-presence-unconfigured");
  });
});
