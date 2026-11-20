// @vitest-environment jsdom
/**
 * <CustodyCasePage>: the static recovery checklist, descriptive progress only, evidence summaries
 * rendered generically, and an unavailable read that never looks clear.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router";

import CustodyCasePage from "@/pages/CustodyCasePage.tsx";

const EPISODE = "22222222-2222-4222-8222-222222222222";
const server = vi.hoisted(() => ({
  caseBody: null as unknown,
  holds: [] as unknown[],
  failCase: false,
  failHolds: false,
  posts: 0,
  device: { bound: false, last_fix: null } as unknown,
  devices: [] as string[],
  failDevice: false,
  failDevices: false,
  puts: [] as unknown[],
}));

vi.mock("@/api/client.ts", async () => {
  const actual =
    await vi.importActual<typeof import("@/api/client.ts")>("@/api/client.ts");
  return {
    ...actual,
    apiFetch: vi.fn(async (path: string, init?: { method?: string }) => {
      if (init?.method === "POST") server.posts += 1;
      if (path.includes("/device/candidates")) {
        if (server.failDevices) throw new actual.ApiError("x", "no", 503);
        return {
          data: {
            devices: server.devices.map((d) => ({ endpoint_identity: d })),
          },
        };
      }
      if (path.endsWith("/device")) {
        if (init?.method === "PUT") {
          server.puts.push(JSON.parse((init as { body: string }).body));
          return { data: { bound: true } };
        }
        if (server.failDevice) throw new actual.ApiError("x", "no", 503);
        return { data: server.device };
      }
      if (path.includes("/case")) {
        if (server.failCase) throw new actual.ApiError("x", "no", 503);
        return { data: server.caseBody };
      }
      if (server.failHolds) throw new actual.ApiError("x", "no", 503);
      return { data: { holds: server.holds } };
    }),
  };
});

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/custody/${EPISODE}`]}>
        <Routes>
          <Route path="/custody/:episodeId" element={<CustodyCasePage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const baseCase = {
  state: "open",
  outcome: null,
  opened_at: "2030-01-01T00:00:00Z",
  closed_at: null,
  truncated: false,
  evidence: [] as unknown[],
};

beforeEach(() => {
  server.caseBody = baseCase;
  server.holds = [];
  server.failCase = false;
  server.failHolds = false;
  server.posts = 0;
  server.device = { bound: false, last_fix: null };
  server.devices = [];
  server.failDevice = false;
  server.failDevices = false;
  server.puts = [];
  window.localStorage.clear();
});
afterEach(cleanup);

describe("CustodyCasePage", () => {
  it("renders the SIM and sign-out checklist with the recovery doors and the security link", async () => {
    renderPage();
    await screen.findByText(/Nothing recorded yet/);
    expect(screen.getByText(/suspend or lock the SIM/)).toBeTruthy();
    expect(
      screen.getByText(/Sign the lost device out of your Google account/),
    ).toBeTruthy();
    expect(
      screen.getByRole("link", { name: "Apple account recovery" }),
    ).toBeTruthy();
    expect(
      screen
        .getByRole("link", { name: "Security questions" })
        .getAttribute("href"),
    ).toBe("/security");
  });

  it("ticking a step is descriptive progress only: it posts nothing", async () => {
    renderPage();
    await screen.findByText(/Nothing recorded yet/);
    fireEvent.click(screen.getAllByRole("checkbox")[0]);
    expect(screen.getByText(/1 of 6 steps ticked/)).toBeTruthy();
    expect(server.posts).toBe(0);
  });

  it("renders evidence rows generically with the source and time", async () => {
    server.caseBody = {
      ...baseCase,
      evidence: [
        {
          contributor: "finance",
          kind: "held_window",
          contributed_at: "2030-01-01T01:00:00Z",
          summary: { transactions: 3, debits: 2, anomalies: { total: 1 } },
        },
        {
          contributor: "chronicler",
          kind: "device_last_fix",
          contributed_at: "2030-01-01T02:00:00Z",
          summary: {
            status: "unknown",
            reason: "stale",
            stale_fix_at: "2000-01-01T00:00:00Z",
          },
        },
      ],
    };
    renderPage();
    await screen.findByText(/Finance: Held window so far/);
    expect(
      screen.getByText(/Owner device: Last known device fix/),
    ).toBeTruthy();
    expect(screen.getByText(/gone stale and is unknown now/)).toBeTruthy();
    expect(screen.queryByText("Home")).toBeNull();
    expect(screen.queryByText("known")).toBeNull();
    expect(screen.getByText("transactions")).toBeTruthy();
  });

  it("is unavailable, not clear, when the case cannot be read", async () => {
    server.failCase = true;
    renderPage();
    await screen.findByText(/The case is unavailable/);
    expect(screen.queryByText(/Nothing recorded yet/)).toBeNull();
  });

  it("reports partial data: holds unavailable while the case loads", async () => {
    server.failHolds = true;
    renderPage();
    await screen.findByText(/Nothing recorded yet/);
    await screen.findByText(/Holds are unavailable/);
    expect(screen.queryByText(/currently held/)).toBeNull();
  });

  describe("your device", () => {
    it("offers the registered devices and puts only the chosen endpoint", async () => {
      server.devices = ["owntracks:o:phone", "owntracks:o:tablet"];
      renderPage();
      await screen.findByText(/No device is chosen for this case yet/);
      const button = await screen.findByRole("button", {
        name: "Use this device",
      });
      expect((button as HTMLButtonElement).disabled).toBe(true);
      fireEvent.change(screen.getByLabelText("Device"), {
        target: { value: "owntracks:o:tablet" },
      });
      fireEvent.click(button);
      await waitFor(() =>
        expect(server.puts).toEqual([
          { endpoint_identity: "owntracks:o:tablet" },
        ]),
      );
      await screen.findByText("Device chosen.");
    });

    it("shows the current binding with a judged fix: a place and an age, or unknown", async () => {
      server.device = {
        bound: true,
        endpoint_identity: "owntracks:o:phone",
        last_fix: { status: "known", age_seconds: 600, place: "Home" },
      };
      renderPage();
      await screen.findByText("owntracks:o:phone");
      await screen.findByText(/Last seen 10 minutes ago near Home/);
      cleanup();
      server.device = {
        bound: true,
        endpoint_identity: "owntracks:o:phone",
        last_fix: { status: "unknown", reason: "stale" },
      };
      renderPage();
      await screen.findByText(
        /Location unknown\. The last fix is too old to trust/,
      );
    });

    it("says unavailable, not no device, when the device read fails", async () => {
      server.failDevice = true;
      renderPage();
      await screen.findByText(/The device is unavailable/);
      expect(screen.queryByText(/No device is chosen/)).toBeNull();
    });

    it("says the devices could not be loaded apart from none registered", async () => {
      server.failDevices = true;
      renderPage();
      await screen.findByText(/Your devices could not be loaded/);
      expect(
        screen.queryByText(/No OwnTracks devices are registered/),
      ).toBeNull();
      cleanup();
      server.failDevices = false;
      renderPage();
      await screen.findByText(/No OwnTracks devices are registered/);
    });

    it("offers no choice on a closed case", async () => {
      server.caseBody = { ...baseCase, state: "closed" };
      renderPage();
      await screen.findByText(/No device is chosen for this case yet/);
      await screen.findByText(/Case closed/);
      expect(
        screen.queryByRole("button", { name: "Use this device" }),
      ).toBeNull();
    });
  });
});
