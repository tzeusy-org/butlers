// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/api/client", () => ({ submitHomePersonMappings: vi.fn() }));

import { submitHomePersonMappings } from "@/api/client";
import type { ApiResponse, HomePersonMappingReceipt } from "@/api/types";

import { HomePersonMappingPanel } from "./HomePersonMappingPanel";

const privateHa = "person.private_ui_sentinel";
const privateEntity = "00000000-0000-4000-8000-000000000002";

const receipt: ApiResponse<HomePersonMappingReceipt> = {
  data: {
    receipt: "00000000-0000-4000-8000-000000000001",
    complete: true,
    received_count: 1,
    created_count: 1,
    unchanged_count: 0,
    conflict_count: 0,
    invalid_reference_count: 0,
  },
  meta: {},
};

let queryClient: QueryClient;
let consoleCalls: ReturnType<typeof vi.spyOn>[];

beforeEach(() => {
  queryClient = new QueryClient();
  consoleCalls = (["log", "info", "warn", "error", "debug"] as const).map((method) =>
    vi.spyOn(console, method).mockImplementation(() => {}),
  );
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.clearAllMocks();
  localStorage.clear();
  sessionStorage.clear();
});

function renderPanel() {
  return render(
    <QueryClientProvider client={queryClient}>
      <HomePersonMappingPanel />
    </QueryClientProvider>,
  );
}

function input(label: string): HTMLInputElement {
  return screen.getByLabelText(label) as HTMLInputElement;
}

function fillPrivateValues() {
  fireEvent.change(input("Home Assistant person ID 1"), { target: { value: privateHa } });
  fireEvent.change(input("Person entity UUID 1"), { target: { value: privateEntity } });
}

/** Every browser-side copy path the contract excludes, rendered to text. */
function browserCopies(): string {
  const storage = (store: Storage) =>
    Object.keys(store).map((key) => `${key}=${store.getItem(key)}`);
  return JSON.stringify({
    dom: document.body.innerHTML,
    url: window.location.href,
    history: window.history.state,
    local: storage(localStorage),
    session: storage(sessionStorage),
    console: consoleCalls.map((spy) => spy.mock.calls),
    queries: queryClient.getQueryCache().getAll().map((query) => [query.queryKey, query.state]),
    mutations: queryClient.getMutationCache().getAll().map((mutation) => mutation.state),
  });
}

function expectNoPrivateCopies() {
  const copies = browserCopies();
  expect(copies).not.toContain(privateHa);
  expect(copies).not.toContain(privateEntity);
}

describe("HomePersonMappingPanel", () => {
  it.each([
    ["success", () => Promise.resolve(receipt), "Complete. Created 1; unchanged 0."],
    [
      "failure",
      () => Promise.reject(new Error(`rejected ${privateHa} ${privateEntity}`)),
      "Mapping request was not applied. Check the values and try again.",
    ],
  ])("keeps private values ephemeral through %s settlement", async (_name, outcome, message) => {
    let settle!: () => void;
    vi.mocked(submitHomePersonMappings).mockImplementation(
      () =>
        new Promise((resolve, reject) => {
          settle = () => outcome().then(resolve, reject);
        }),
    );
    renderPanel();
    fillPrivateValues();
    fireEvent.click(screen.getByRole("button", { name: "Submit mappings" }));

    // In flight: the form is locked, and the values reached only the client call.
    expect((screen.getByRole("button", { name: "Submitting..." }) as HTMLButtonElement).disabled).toBe(
      true,
    );
    expect(input("Home Assistant person ID 1").disabled).toBe(true);
    expect(submitHomePersonMappings).toHaveBeenCalledWith(
      [{ ha_person_id: privateHa, entity_id: privateEntity }],
      expect.stringMatching(/^[A-Za-z0-9_-]{43}$/),
    );

    await act(async () => settle());
    await screen.findByText(message);
    expect(input("Home Assistant person ID 1").value).toBe("");
    expect(input("Person entity UUID 1").value).toBe("");
    expect(input("Home Assistant person ID 1").disabled).toBe(false);
    await waitFor(expectNoPrivateCopies);
  });

  it("clears private values without submitting", () => {
    renderPanel();
    fillPrivateValues();
    fireEvent.click(screen.getByRole("button", { name: "Clear" }));

    expect(input("Home Assistant person ID 1").value).toBe("");
    expect(input("Person entity UUID 1").value).toBe("");
    expect(submitHomePersonMappings).not.toHaveBeenCalled();
    expectNoPrivateCopies();
  });

  it("leaves no private copy when dismissed before settlement", async () => {
    let settle!: (value: ApiResponse<HomePersonMappingReceipt>) => void;
    vi.mocked(submitHomePersonMappings).mockImplementation(
      () => new Promise((resolve) => (settle = resolve)),
    );
    const view = renderPanel();
    fillPrivateValues();
    fireEvent.click(screen.getByRole("button", { name: "Submit mappings" }));

    view.unmount();
    await act(async () => settle(receipt));
    expectNoPrivateCopies();
  });
});
