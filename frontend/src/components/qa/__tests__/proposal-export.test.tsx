// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/api/client";
import { ProposalExportPanel } from "@/components/qa";

const hooks = vi.hoisted(() => ({ useQaCaseProposal: vi.fn() }));
vi.mock("@/hooks/use-qa", () => ({ useQaCaseProposal: hooks.useQaCaseProposal }));

const proposal = {
  case_id: "case-1",
  branch_name: "qa/finance/abc123",
  base_sha: "b".repeat(40),
  head_sha: "c".repeat(40),
  commits: ["c".repeat(40)],
  digest: "d".repeat(64),
  paths: ["src/a.py", "docs/b.md"],
  diff: [],
  diff_truncated: false,
  bundle_bytes: 2048,
  sealed_before_isolation: false,
};

describe("ProposalExportPanel", () => {
  beforeEach(() => {
    hooks.useQaCaseProposal.mockReset();
  });
  afterEach(cleanup);

  it("shows exactly what would be pushed", () => {
    hooks.useQaCaseProposal.mockReturnValue({
      data: { data: proposal },
      isLoading: false,
      isError: false,
    });

    render(<ProposalExportPanel caseId="case-1" />);

    expect(screen.getByText("qa/finance/abc123")).toBeTruthy();
    expect(screen.getByTestId("qa-proposal-export-digest").textContent).toBe("d".repeat(64));
    expect(screen.getByText("src/a.py")).toBeTruthy();
    expect(screen.getByText("2.0 KiB")).toBeTruthy();
    expect(screen.queryByTestId("qa-proposal-export-pre-isolation")).toBeNull();
  });

  it("warns when the export was sealed before isolation", () => {
    hooks.useQaCaseProposal.mockReturnValue({
      data: { data: { ...proposal, sealed_before_isolation: true } },
      isLoading: false,
      isError: false,
    });

    render(<ProposalExportPanel caseId="case-1" />);

    expect(screen.getByTestId("qa-proposal-export-pre-isolation")).toBeTruthy();
  });

  it.each([
    [410, "pruned", false],
    [409, "Do not adopt it", true],
    [503, "Try again", false],
    [404, "No retained patch", false],
  ])("maps a %s answer to an honest message", (status, text, alert) => {
    hooks.useQaCaseProposal.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new ApiError("X", "x", status),
    });

    render(<ProposalExportPanel caseId="case-1" />);

    const node = screen.getByTestId("qa-proposal-export-unavailable");
    expect(node.textContent).toContain(text);
    expect(node.getAttribute("role")).toBe(alert ? "alert" : null);
  });

  it("labels the changed paths", () => {
    hooks.useQaCaseProposal.mockReturnValue({
      data: { data: proposal },
      isLoading: false,
      isError: false,
    });

    render(<ProposalExportPanel caseId="case-1" />);

    expect(screen.getByText("Changed paths")).toBeTruthy();
  });
});
