// @vitest-environment jsdom
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import type { AllowanceAccount } from "@/api/types.ts";
import { ModelAllowanceStrip } from "@/components/settings/ModelAllowanceStrip";

const NOW = Date.parse("2026-10-18T12:00:00Z");
const now = () => NOW;

function account(over: Partial<AllowanceAccount>): AllowanceAccount {
  return {
    account_key: "acct-a",
    state: "exhausted",
    reset_at: "2026-10-18T14:05:00Z",
    reset_source: "parsed",
    first_seen_at: "2026-10-18T11:00:00Z",
    updated_at: "2026-10-18T11:00:00Z",
    entries: [],
    ...over,
  };
}

afterEach(cleanup);

describe("ModelAllowanceStrip", () => {
  it("shows a labelled countdown and collapses available accounts to a count", () => {
    render(
      <ModelAllowanceStrip
        isError={false}
        now={now}
        data={{
          available: true,
          accounts: [
            account({}),
            account({ account_key: "b", state: "available", reset_at: null }),
            account({ account_key: "c", state: "available", reset_at: null }),
          ],
        }}
      />,
    );
    expect(screen.getByTestId("models-allowance-acct-a").textContent).toContain(
      "resets in 2h 05m (provider said)",
    );
    expect(screen.getByTestId("models-allowance-available").textContent).toBe("2 available");
    expect(screen.queryByTestId("models-allowance-b")).toBeNull();
  });

  it("labels an estimated reset", () => {
    render(
      <ModelAllowanceStrip
        isError={false}
        now={now}
        data={{ available: true, accounts: [account({ reset_source: "default_window" })] }}
      />,
    );
    expect(screen.getByTestId("models-allowance-acct-a").textContent).toContain("(estimated)");
  });

  it("says should be back rather than a negative countdown", () => {
    render(
      <ModelAllowanceStrip
        isError={false}
        now={now}
        data={{ available: true, accounts: [account({ reset_at: "2026-10-18T11:00:00Z" })] }}
      />,
    );
    const text = screen.getByTestId("models-allowance-acct-a").textContent;
    expect(text).toContain("should be back");
    expect(text).not.toContain("resets in");
  });

  it("says unknown for unknown-state rows", () => {
    render(
      <ModelAllowanceStrip
        isError={false}
        now={now}
        data={{
          available: true,
          accounts: [account({ state: "unknown", reset_at: null, reset_source: "unknown" })],
        }}
      />,
    );
    expect(screen.getByTestId("models-allowance-acct-a").textContent).toContain("unknown");
  });

  it("reads allowance unknown when the table is unreadable", () => {
    render(<ModelAllowanceStrip isError={false} data={{ available: false, accounts: [] }} />);
    expect(screen.getByTestId("models-allowance-unknown")).toBeTruthy();
  });

  it("reads allowance unknown on a fetch error, but renders nothing for a non-owner", () => {
    const { unmount } = render(<ModelAllowanceStrip isError data={undefined} />);
    expect(screen.getByTestId("models-allowance-unknown")).toBeTruthy();
    unmount();
    const { container } = render(<ModelAllowanceStrip isError isForbidden data={undefined} />);
    expect(container.innerHTML).toBe("");
  });
});
