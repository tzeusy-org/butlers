// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { onlineManager, QueryClient, QueryClientProvider } from "@tanstack/react-query";

import type { TelegramSessionStatusResponse } from "@/api/types";

vi.mock("@/api/index", () => ({
  getTelegramSessionStatus: vi.fn(),
  telegramSendCode: vi.fn(),
  telegramVerifyCode: vi.fn(),
}));

import { getTelegramSessionStatus } from "@/api/index";
import { TelegramSessionSetup } from "./TelegramSessionSetup";

const UNREADY_STATUS: TelegramSessionStatusResponse = {
  has_api_id: true,
  has_api_hash: true,
  has_session: false,
  has_scope_consent: false,
  ready: false,
};

const mockGetTelegramSessionStatus = vi.mocked(getTelegramSessionStatus);

function renderSetup({ startImmediately = false }: { startImmediately?: boolean } = {}) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });

  return render(
    <QueryClientProvider client={queryClient}>
      <TelegramSessionSetup entityId="owner-entity" entries={[]} startImmediately={startImmediately} />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  onlineManager.setOnline(true);
  vi.clearAllMocks();
});

describe("TelegramSessionSetup status reader", () => {
  it("renders the status skeleton while the status probe is loading", async () => {
    let resolveStatus: (status: TelegramSessionStatusResponse) => void;
    const statusPromise = new Promise<TelegramSessionStatusResponse>((resolve) => {
      resolveStatus = resolve;
    });
    mockGetTelegramSessionStatus.mockReturnValue(statusPromise);

    const { container } = renderSetup();

    expect(screen.getByText("Telegram user session")).toBeTruthy();
    expect(container.querySelector('[data-slot="skeleton"]')).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();

    await act(async () => {
      resolveStatus!(UNREADY_STATUS);
    });
  });

  it.each([false, true])(
    "keeps setup fail-closed while an offline initial status query is pending (startImmediately=%s)",
    (startImmediately) => {
      onlineManager.setOnline(false);

      const { container } = renderSetup({ startImmediately });

      expect(screen.getByText("Telegram user session")).toBeTruthy();
      expect(container.querySelector('[data-slot="skeleton"]')).toBeTruthy();
      expect(screen.queryByRole("button")).toBeNull();
      expect(screen.queryByLabelText("Telegram API ID")).toBeNull();
      expect(screen.queryByLabelText("Telegram API hash")).toBeNull();
      expect(screen.queryByLabelText("Telegram phone number")).toBeNull();
    },
  );

  it("renders unavailable and retries without inferring missing credentials or setup", async () => {
    mockGetTelegramSessionStatus
      .mockRejectedValueOnce(new Error("status endpoint unavailable"))
      .mockResolvedValueOnce(UNREADY_STATUS);

    renderSetup();

    expect(await screen.findByText("Session status unavailable.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Retry status" })).toBeTruthy();
    expect(screen.queryByText("Telegram API ID")).toBeNull();
    expect(screen.queryByRole("button", { name: "Send code" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Set up Telegram session" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Retry status" }));

    expect(await screen.findByRole("button", { name: "Set up Telegram session" })).toBeTruthy();
    await waitFor(() => {
      expect(mockGetTelegramSessionStatus).toHaveBeenCalledTimes(2);
    });
  });

  it("keeps a successful but unready status on the existing setup path", async () => {
    mockGetTelegramSessionStatus.mockResolvedValue(UNREADY_STATUS);

    renderSetup();

    expect(await screen.findByRole("button", { name: "Set up Telegram session" })).toBeTruthy();
    expect(screen.getByLabelText("Telegram credential status")).toBeTruthy();
    expect(screen.getByText("+ API ID")).toBeTruthy();
    expect(screen.getByText("+ API hash")).toBeTruthy();
    expect(screen.getByText("− session")).toBeTruthy();
    expect(screen.queryByText("Session status unavailable.")).toBeNull();
    expect(screen.queryByRole("button", { name: "Retry status" })).toBeNull();
  });
});
