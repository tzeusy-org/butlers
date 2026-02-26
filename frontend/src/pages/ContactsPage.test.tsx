// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";

import ContactsPage from "@/pages/ContactsPage";
import { useContacts, useLabels } from "@/hooks/use-contacts";
import { triggerContactsSync } from "@/api/index.ts";
import { toast } from "sonner";

vi.mock("@/hooks/use-contacts", () => ({
  useContacts: vi.fn(),
  useLabels: vi.fn(),
}));

vi.mock("@/api/index.ts", () => ({
  triggerContactsSync: vi.fn(),
}));

vi.mock("sonner", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

type UseContactsResult = ReturnType<typeof useContacts>;
type UseLabelsResult = ReturnType<typeof useLabels>;

function flush(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

function findButton(container: HTMLElement, label: string): HTMLButtonElement | undefined {
  return Array.from(container.querySelectorAll("button")).find((button) =>
    button.textContent?.includes(label),
  );
}

function setContactsState(state?: Partial<UseContactsResult>) {
  vi.mocked(useContacts).mockReturnValue({
    data: {
      contacts: [
        {
          id: "contact-1",
          full_name: "Ada Lovelace",
          first_name: "Ada",
          last_name: "Lovelace",
          nickname: null,
          email: "ada@example.com",
          phone: null,
          labels: [],
          last_interaction_at: null,
        },
      ],
      total: 1,
    },
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn().mockResolvedValue(undefined),
    ...state,
  } as UseContactsResult);
}

function setLabelsState(state?: Partial<UseLabelsResult>) {
  vi.mocked(useLabels).mockReturnValue({
    data: [],
    isLoading: false,
    isError: false,
    error: null,
    ...state,
  } as UseLabelsResult);
}

describe("ContactsPage", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.resetAllMocks();
    setContactsState();
    setLabelsState();
    vi.mocked(triggerContactsSync).mockResolvedValue({
      provider: "google",
      mode: "incremental",
      created: 1,
      updated: 2,
      skipped: 0,
      errors: 0,
      summary: {},
      message: null,
    });

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

  function renderPage() {
    act(() => {
      root.render(
        <MemoryRouter>
          <ContactsPage />
        </MemoryRouter>,
      );
    });
  }

  it('renders a "Sync From Google" button in the page header', () => {
    renderPage();
    expect(findButton(container, "Sync From Google")).toBeDefined();
  });

  it("triggers sync in incremental mode and refetches contacts on success", async () => {
    const refetch = vi.fn().mockResolvedValue(undefined);
    setContactsState({ refetch });
    renderPage();

    const button = findButton(container, "Sync From Google");
    expect(button).toBeDefined();

    await act(async () => {
      button?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    expect(triggerContactsSync).toHaveBeenCalledTimes(1);
    expect(triggerContactsSync).toHaveBeenCalledWith("incremental");
    expect(refetch).toHaveBeenCalledTimes(1);
    expect(toast.success).toHaveBeenCalledTimes(1);
  });

  it("shows in-flight button state while sync is running", async () => {
    vi.mocked(triggerContactsSync).mockReturnValue(new Promise(() => {}));
    renderPage();

    const button = findButton(container, "Sync From Google");
    expect(button).toBeDefined();

    await act(async () => {
      button?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    const syncingButton = findButton(container, "Syncing...");
    expect(syncingButton).toBeDefined();
    expect(syncingButton?.disabled).toBe(true);
  });

  it("shows error feedback when sync fails", async () => {
    vi.mocked(triggerContactsSync).mockRejectedValue(new Error("OAuth missing"));
    renderPage();

    const button = findButton(container, "Sync From Google");
    expect(button).toBeDefined();

    await act(async () => {
      button?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    expect(toast.error).toHaveBeenCalledTimes(1);
    expect(toast.error).toHaveBeenCalledWith("Google sync failed: OAuth missing");
  });
});

