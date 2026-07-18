// @vitest-environment jsdom

import { afterEach, describe, expect, it } from "vitest";
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
