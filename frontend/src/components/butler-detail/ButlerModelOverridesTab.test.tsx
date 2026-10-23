// @vitest-environment jsdom

import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import ButlerModelOverridesTab from "./ButlerModelOverridesTab";

// Real component, hooks, HTTP client and pricing cache; only the HTTP edge is controlled.
const model = { id: "model-a", alias: "Alpha", model_id: "provider/alpha", runtime_type: "codex",
  complexity_tier: "workhorse", enabled: true, priority: 0, extra_args: [], session_timeout_s: 1800 };
const secondModel = { ...model, id: "model-b", alias: "Beta", model_id: "provider/beta" };
const override = { id: "override-a", catalog_entry_id: model.id, alias: model.alias,
  butler_name: "qa", enabled: true, priority: null, complexity_tier: null };
let pricing: "healthy" | "failed" | "missing";
let writes: { method: string; path: string; body: unknown }[];
let client: QueryClient;
const json = (data: unknown, status = 200) => new Response(JSON.stringify({ data }), {
  status, headers: { "Content-Type": "application/json" },
});

beforeEach(() => {
  pricing = "healthy";
  writes = [];
  client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
    const path = new URL(String(input), "https://butlers.test").pathname;
    const method = options?.method ?? "GET";
    if (method !== "GET") {
      writes.push({ method, path, body: JSON.parse(String(options?.body)) });
      return json(method === "DELETE" ? { deleted: true, id: override.id } : [override]);
    }
    if (path === "/api/settings/models") return json([model, secondModel]);
    if (path === "/api/settings/pricing") {
      if (pricing === "failed") return json(null, 503);
      return json(pricing === "missing" ? {} : { [model.model_id]: { input_per_million: 2.5, output_per_million: 10 }, [secondModel.model_id]: { input_per_million: 0.0001, output_per_million: 0 } });
    }
    if (path === "/api/butlers/qa/model-overrides") return json([override]);
    if (path === "/api/butlers/qa/resolve-model") return json({ resolved: false });
    throw new Error(`Unexpected HTTP path: ${method} ${path}`);
  }));
});

afterEach(() => { cleanup(); client.clear(); vi.unstubAllGlobals(); });

function show() {
  render(<QueryClientProvider client={client}><ButlerModelOverridesTab butlerName="qa" /></QueryClientProvider>);
}

it("requires reasons on edit and removal and carries actual rate preview status through both HTTP mutations", async () => {
  show();
  await userEvent.click(await screen.findByRole("button", { name: "Edit" }));
  const dialog = screen.getByRole("dialog");
  const save = within(dialog).getByRole("button", { name: "Update" });
  expect((save as HTMLButtonElement).disabled).toBe(true);
  const reason = within(dialog).getByLabelText("Reason");
  fireEvent.change(reason, { target: { value: "   " } });
  expect((save as HTMLButtonElement).disabled).toBe(true);
  expect(await within(dialog).findByText(/USD per 1M tokens/)).not.toBeNull();
  expect(within(dialog).getAllByText(/\$2.50 input.*\$10.00 output/)).toHaveLength(2);
  fireEvent.keyDown(within(dialog).getByRole("combobox", { name: "Model" }), { key: "Enter" });
  fireEvent.keyDown(await screen.findByRole("option", { name: /Beta/ }), { key: "Enter" });
  expect(within(dialog).getByText(/\$0.0001 input.*\$0.00 output/)).not.toBeNull();
  expect(within(dialog).getByText(/existing entry stays unchanged/)).not.toBeNull();
  fireEvent.change(reason, { target: { value: " approved evaluation " } });
  await userEvent.click(save);
  await waitFor(() => expect(writes).toHaveLength(1));
  expect(writes[0]).toMatchObject({ method: "PUT", body: { reason: "approved evaluation",
    price_preview_unavailable: false, overrides: [{ catalog_entry_id: "model-b" }] } });
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  await userEvent.click(screen.getByRole("button", { name: "Remove" }));
  const removal = screen.getByRole("dialog");
  const confirm = within(removal).getByRole("button", { name: "Remove" });
  expect((confirm as HTMLButtonElement).disabled).toBe(true);
  fireEvent.change(within(removal).getByLabelText("Reason"), { target: { value: "restore catalog defaults" } });
  await userEvent.click(confirm);
  await waitFor(() => expect(writes).toHaveLength(2));
  expect(writes[1]).toEqual({ method: "DELETE", path: "/api/butlers/qa/model-overrides/override-a",
    body: { reason: "restore catalog defaults", price_preview_unavailable: false } });
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  await userEvent.click(screen.getByRole("button", { name: "Add override" }));
  const addition = screen.getByRole("dialog");
  fireEvent.keyDown(within(addition).getByRole("combobox", { name: "Model" }), { key: "Enter" });
  fireEvent.keyDown(await screen.findByRole("option", { name: /Alpha/ }), { key: "Enter" });
  const add = within(addition).getByRole("button", { name: "Add override" });
  expect((add as HTMLButtonElement).disabled).toBe(true);
  fireEvent.change(within(addition).getByLabelText("Reason"), { target: { value: "new override evaluation" } });
  await userEvent.click(add);
  await waitFor(() => expect(writes).toHaveLength(3));
  expect(writes[2]).toMatchObject({ method: "PUT", body: { reason: "new override evaluation",
    overrides: [{ catalog_entry_id: "model-a" }] } });
});

it.each(["failed", "missing"] as const)("keeps pricing %s honest and nonblocking after a reason", async (state) => {
  pricing = state;
  show();
  await userEvent.click(await screen.findByRole("button", { name: "Edit" }));
  const dialog = screen.getByRole("dialog");
  expect(await within(dialog).findByText(/Cost preview unavailable/)).not.toBeNull();
  expect(within(dialog).queryByText(/\$[0-9]/)).toBeNull();
  fireEvent.change(within(dialog).getByLabelText("Reason"), { target: { value: "owner accepted unavailable preview" } });
  await userEvent.click(within(dialog).getByRole("button", { name: "Update" }));
  await waitFor(() => expect(writes).toHaveLength(1));
  expect(writes[0]).toMatchObject({ body: { price_preview_unavailable: true,
    reason: "owner accepted unavailable preview" } });
});
