// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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

  it("says so honestly when the patch is gone", () => {
    hooks.useQaCaseProposal.mockReturnValue({ data: undefined, isLoading: false, isError: true });

    render(<ProposalExportPanel caseId="case-1" />);

    expect(screen.getByTestId("qa-proposal-export-unavailable")).toBeTruthy();
  });
});
