// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";

import type { AutonomySuggestion } from "@/api/types";
import { AutonomySuggestionsBanner } from "@/components/approvals/autonomy-suggestions-banner.tsx";

afterEach(cleanup);

const V2_PROMOTION: AutonomySuggestion = {
  id: "suggestion-1",
  suggestion_type: "promotion",
  pattern_fingerprint: "fingerprint",
  fingerprint_version: 2,
  action_id: "approval-42",
  tool_name: "send_telegram",
  representative_args: { chat_id: "mom_123" },
  status: "pending",
  approval_count_at_creation: 5,
  scope_description:
    "Auto-approve send_telegram when chat_id = 'mom_123'; the shown arguments are exactly pinned while omitted arguments may vary",
  created_at: "2026-07-17T12:00:00Z",
};

describe("AutonomySuggestionsBanner", () => {
  it("explains that a v2 promotion constrains only its safety-critical basis", () => {
    render(
      <MemoryRouter>
        <AutonomySuggestionsBanner
          suggestions={[V2_PROMOTION]}
          onConfirm={() => {}}
          onDismiss={() => {}}
        />
      </MemoryRouter>,
    );

    expect(
      screen.getByText(
        "This scope pins only the shown arguments; omitted arguments may vary.",
      ),
    ).toBeTruthy();
  });

  it("links an evidence-backed suggestion to its originating approval", () => {
    render(
      <MemoryRouter>
        <AutonomySuggestionsBanner
          suggestions={[V2_PROMOTION]}
          onConfirm={() => {}}
          onDismiss={() => {}}
        />
      </MemoryRouter>,
    );

    expect(screen.getByRole("link", { name: "Review approval" }).getAttribute("href")).toBe(
      "/approvals/approval-42",
    );
  });
});

it("demotion Section is flat and retains keyboard recovery controls", async () => {
  const confirm = vi.fn(), dismiss = vi.fn();
  render(<MemoryRouter><AutonomySuggestionsBanner suggestions={[{...V2_PROMOTION, suggestion_type: "demotion"}]} onConfirm={confirm} onDismiss={dismiss} /></MemoryRouter>);
  const section = screen.getByText("Review Standing Rule").closest("section")!;
  expect(section.className).not.toMatch(/(?:bg|border)-\[var\(--(?:amber|red)/);
  expect(screen.getByText("Execution failed")).toBeTruthy();
  expect(screen.getByText(V2_PROMOTION.scope_description)).toBeTruthy();
  const user = userEvent.setup();
  await user.tab();
  expect(document.activeElement).toBe(screen.getByRole("button", {name: "Revoke rule"}));
  await user.keyboard("{Enter}");
  expect(confirm).toHaveBeenCalledWith(V2_PROMOTION.id);
  await user.tab();
  await user.keyboard("{Enter}");
  expect(dismiss).toHaveBeenCalledWith(V2_PROMOTION.id);
});
