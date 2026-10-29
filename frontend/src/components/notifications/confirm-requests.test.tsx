// @vitest-environment jsdom
import { act } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/api/client";
import { clearOwnerSession, rememberOwnerCsrf } from "@/api/owner-session";
import { ConfirmRequests } from "@/components/notifications/confirm-requests";

// Keep the actual generated client, owner boundary and request serialization.
// Only HTTP response data is synthetic.
const respond = vi.fn<(path: string) => Promise<unknown>>();
const fetcher = vi.fn<typeof fetch>();

const request = {
  id: "42071b76-5215-423b-b4eb-06c1dd296f56",
  text: "Was yesterday a work day? <a href='https://example.invalid'>link</a> **quoted**",
  options: [{ value: "work", label: "Work" }, { value: "not_work", label: "Not work" }],
  status: "pending",
  answer_index: null,
  application_status: "idle",
  expires_at: "2099-01-01T00:00:00Z",
  created_at: "2026-01-01T00:00:00Z",
};

function page(data: unknown[]) {
  return { data, meta: { next_cursor: null } };
}

let client: QueryClient;
beforeEach(() => {
  vi.resetAllMocks();
  clearOwnerSession();
  rememberOwnerCsrf({ csrf_token: "synthetic-confirm-test", csrf_expires_at: new Date(Date.now() + 60_000).toISOString() });
  fetcher.mockImplementation(async (input) => {
    const url = new URL(String(input), location.href);
    const path = url.pathname.replace(/^\/api(?=\/|$)/, "") + url.search;
    try {
      const value = await respond(path);
      return new Response(JSON.stringify(value), { status: 200, headers: { "Content-Type": "application/json" } });
    } catch (error) {
      if (error instanceof ApiError) {
        return new Response(JSON.stringify({ error: { code: error.code, message: error.message } }), { status: error.status });
      }
      throw error;
    }
  });
  vi.stubGlobal("fetch", fetcher);
  client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
});
afterEach(() => {
  cleanup();
  client.clear();
  clearOwnerSession();
  vi.unstubAllGlobals();
});

function mount() {
  return render(<QueryClientProvider client={client}><ConfirmRequests /></QueryClientProvider>);
}

describe("dashboard confirmation requests", () => {
  it("loads older questions by server cursor and keeps their answer state through page refreshes", async () => {
    const olderId = "72071b76-5215-423b-b4eb-06c1dd296f56";
    const olderText = "An older question still needs your answer.";
    const older = { ...request, id: olderId, text: olderText };
    let answered = false;
    let finishRefresh!: (result: unknown) => void;
    respond.mockImplementation(async (path) => {
      if (path.endsWith("/answer")) {
        answered = true;
        return { data: { status: "answered", application_status: "pending" } };
      }
      if (path === `/confirms?before=${request.id}`) {
        if (answered) return new Promise((resolve) => { finishRefresh = resolve; });
        return page([older]);
      }
      return { data: [{ ...request, status: "expired" }], meta: { next_cursor: request.id } };
    });
    mount();
    await screen.findByText(request.text);
    expect(screen.queryByText(olderText)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Load more" }));
    expect(await screen.findByText(olderText)).toBeTruthy();
    expect(fetcher).toHaveBeenCalledWith(`/api/confirms?before=${request.id}`, expect.objectContaining({ signal: expect.any(AbortSignal) }));
    expect(screen.getByText(request.text)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Load more" })).toBeNull();
    const choices = screen.getAllByRole("button", { name: "Not work" });
    fireEvent.click(choices[1]);
    expect(await screen.findByText("Your answer: Not work")).toBeTruthy();
    expect(await screen.findByText("Answer recorded. Waiting for it to be applied.")).toBeTruthy();
    expect((screen.getAllByRole("button", { name: "Not work" })[1] as HTMLButtonElement).disabled).toBe(true);
    expect(fetcher).toHaveBeenCalledWith(`/api/confirms/${olderId}/answer`, expect.objectContaining({
      method: "POST", body: JSON.stringify({ option_index: 1 }),
    }));
    await waitFor(() => expect(finishRefresh).toBeTypeOf("function"));
    await act(async () => { finishRefresh(page([{ ...older, status: "answered", answer_index: 1, application_status: "pending" }])); });
    expect(screen.getByText("Your answer: Not work")).toBeTruthy();
    expect(screen.getByText(request.text)).toBeTruthy();
  });

  it("renders persisted text inertly and records the selected index before showing application progress", async () => {
    let applicationStatus = "pending";
    let answered = false;
    let finishAnswer!: (result: unknown) => void;
    respond.mockImplementation(async (path) => {
      if (path.endsWith("/answer")) {
        return new Promise((resolve) => { finishAnswer = resolve; });
      }
      return page([{ ...request, ...(answered ? { status: "answered", answer_index: 1, application_status: applicationStatus } : {}) }]);
    });
    const { container } = mount();
    expect(await screen.findByText(request.text)).toBeTruthy();
    expect(container.querySelector("a")).toBeNull();
    expect(container.querySelector("strong")).toBeNull();
    const button = screen.getByRole("button", { name: "Not work" }) as HTMLButtonElement;
    fireEvent.click(button);
    expect(button.disabled).toBe(true);
    fireEvent.click(button);
    expect(screen.queryByText(/Answer recorded/)).toBeNull();
    await waitFor(() => expect(fetcher).toHaveBeenCalledWith(`/api/confirms/${request.id}/answer`, expect.objectContaining({
      method: "POST", body: JSON.stringify({ option_index: 1 }),
    })));
    expect(respond.mock.calls.filter(([path]) => path.endsWith("/answer"))).toHaveLength(1);
    answered = true;
    await act(async () => { finishAnswer({ data: { status: "answered", application_status: "pending" } }); });
    expect(await screen.findByText("Answer recorded. Waiting for it to be applied.")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Work" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText("Your answer: Not work")).toBeTruthy();
    applicationStatus = "applied";
    await act(async () => { await client.invalidateQueries({ queryKey: ["confirms"] }); });
    expect(await screen.findByText("Answer applied.")).toBeTruthy();
  });

  it.each(["unavailable", "refused"])("keeps a %s submission honest and permits a deliberate retry", async (failure) => {
    respond.mockImplementation(async (path) => {
      if (path.endsWith("/answer")) {
        if (failure === "refused") return { data: { status: "refused" } };
        throw new ApiError("TEMPORARY_FAILURE", "Try again later", 503);
      }
      return page([request]);
    });
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "Not work" }));
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", expect.stringContaining("Could not confirm whether your answer was recorded."));
    expect(screen.queryByText(/Answer recorded/)).toBeNull();
    await waitFor(() => expect((screen.getByRole("button", { name: "Not work" }) as HTMLButtonElement).disabled).toBe(false));
    expect(respond.mock.calls.filter(([path]) => path.endsWith("/answer"))).toHaveLength(1);
  });

  it("shows the authoritative answer when a different choice was already recorded elsewhere", async () => {
    let answered = false;
    respond.mockImplementation(async (path) => {
      if (path.endsWith("/answer")) {
        answered = true;
        return { data: { status: "already_handled", state: "answered", application_status: "applied" } };
      }
      return page([{ ...request, ...(answered ? { status: "answered", answer_index: 0, application_status: "applied" } : {}) }]);
    });
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "Not work" }));
    expect(await screen.findByText("Your answer: Work")).toBeTruthy();
    expect(screen.queryByText("Your answer: Not work")).toBeNull();
    expect((screen.getByRole("button", { name: "Not work" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("recovers a committed answer after its POST response was lost", async () => {
    let answered = false;
    respond.mockImplementation(async (path) => {
      if (path.endsWith("/answer")) {
        answered = true;
        throw new TypeError("Network response lost");
      }
      return page([{ ...request, ...(answered ? { status: "answered", answer_index: 1, application_status: "pending" } : {}) }]);
    });
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "Not work" }));
    expect(await screen.findByText("Your answer: Not work")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
    expect((screen.getByRole("button", { name: "Not work" }) as HTMLButtonElement).disabled).toBe(true);
    expect(respond.mock.calls.filter(([path]) => path.endsWith("/answer"))).toHaveLength(1);
  });

  it.each([
    { status: "expired", expires_at: request.expires_at },
    { status: "pending", expires_at: "2020-01-01T00:00:00Z" },
  ])("disables expired choices for $status server state", async (expiry) => {
    respond.mockResolvedValue(page([{ ...request, ...expiry }]));
    mount();
    expect(await screen.findByText("This question has expired.")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Not work" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("distinguishes a recorded answer whose application needs attention", async () => {
    respond.mockResolvedValue(page([{ ...request, status: "answered", answer_index: 1, application_status: "error" }]));
    mount();
    expect(await screen.findByText("Answer recorded. Applying it failed; it will be retried.")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Not work" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("omits the dark runtime and replaces cached controls if a refresh becomes unavailable", async () => {
    respond.mockResolvedValue(page([request]));
    const { container } = mount();
    await screen.findByRole("button", { name: "Not work" });
    respond.mockRejectedValue(new ApiError("UNKNOWN_ERROR", "confirm_runtime_unavailable", 503));
    await act(async () => { await client.invalidateQueries({ queryKey: ["confirms"] }); });
    await waitFor(() => expect(container.textContent).toBe(""));
  });

  it("shows a real loading failure with a working read retry", async () => {
    respond.mockRejectedValue(new ApiError("UNKNOWN_ERROR", "Database unavailable", 503));
    mount();
    expect(await screen.findByText("Questions could not be loaded.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Not work" })).toBeNull();
    respond.mockResolvedValue(page([request]));
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("button", { name: "Not work" })).toBeTruthy();
  });
});
