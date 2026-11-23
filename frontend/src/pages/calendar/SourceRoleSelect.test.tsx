// @vitest-environment jsdom

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { CalendarWorkspaceSourceFreshness } from "@/api/types.ts";
import { SourceRoleSelect } from "@/pages/calendar/SourceRoleSelect";

const mutate = vi.fn();
vi.mock("@/hooks/use-calendar-workspace", () => ({
  useSetCalendarSourceRole: () => ({ mutate, isPending: false }),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function source(over: Partial<CalendarWorkspaceSourceFreshness> = {}) {
  return {
    source_id: "s1",
    source_key: "provider:google:partner@example.test",
    source_kind: "provider_event",
    lane: "user",
    calendar_id: "partner@example.test",
    display_name: "Partner",
    time_role: "unclassified",
    role_basis: "provider_inferred",
    ...over,
  } as CalendarWorkspaceSourceFreshness;
}

let root: ReturnType<typeof createRoot> | null = null;

function render(s: CalendarWorkspaceSourceFreshness) {
  const host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  act(() => root!.render(<SourceRoleSelect source={s} />));
  return host;
}

afterEach(() => {
  act(() => root?.unmount());
  root = null;
  document.body.innerHTML = "";
  mutate.mockReset();
});

describe("SourceRoleSelect", () => {
  it("says an unclassified calendar counts only when the owner is invited", () => {
    const host = render(source());
    expect(host.textContent).toContain("counts only if invited");
    expect(host.querySelector("select")!.value).toBe("");
  });

  it("shows a confirmed role as selected", () => {
    const host = render(source({ time_role: "household_member", role_basis: "owner_confirmed" }));
    expect(host.querySelector("select")!.value).toBe("household_member");
  });

  it("records the chosen role as the owner's word", () => {
    const host = render(source());
    const select = host.querySelector("select")!;
    act(() => {
      select.value = "household_member";
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    expect(mutate).toHaveBeenCalledWith(
      { source_key: "provider:google:partner@example.test", role: "household_member" },
      expect.anything(),
    );
  });

  it("clears the confirmation when Not set is chosen", () => {
    const host = render(source({ time_role: "team", role_basis: "owner_confirmed" }));
    const select = host.querySelector("select")!;
    act(() => {
      select.value = "";
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    expect(mutate).toHaveBeenCalledWith(
      { source_key: "provider:google:partner@example.test", role: null },
      expect.anything(),
    );
  });

  it("is labelled for assistive tech with the calendar's name", () => {
    const host = render(source());
    expect(host.textContent).toContain("Whose calendar is Partner");
  });
});
