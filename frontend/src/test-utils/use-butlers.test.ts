import { describe, expect, it, vi, beforeEach } from "vitest";

import { useButlers } from "@/hooks/use-butlers";
import {
  getLastUseButlersState,
  resetUseButlersMock,
  setUseButlersState,
} from "@/test-utils/use-butlers";

vi.mock("@/hooks/use-butlers", () => ({
  useButlers: vi.fn(),
}));

describe("useButlers test helper", () => {
  beforeEach(() => {
    resetUseButlersMock();
  });

  it("defaults missing sessions_24h to zero without overwriting explicit fixtures", () => {
    setUseButlersState({
      data: {
        data: [
          { name: "general", status: "ok", port: 40101, type: "butler" },
          { name: "health", status: "ok", port: 40102, type: "butler", sessions_24h: 7 },
        ],
      },
    });

    const result = useButlers();

    expect(result?.data?.meta).toEqual({});
    expect(result?.data?.data.map((butler) => butler.sessions_24h)).toEqual([0, 7]);
    expect(getLastUseButlersState()?.data?.data[0]).not.toHaveProperty("sessions_24h");
  });
});
