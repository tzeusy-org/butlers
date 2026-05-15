// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { QaPrSummary } from "@/api/types";
import { CounterEvidence, DiffPreview, PRPanel } from "@/components/qa";

vi.mock("@/components/ui/time", () => ({
  Time: ({ value }: { value: string }) => <time dateTime={value}>{value}</time>,
}));

const pr: QaPrSummary = {
  number: 42,
  state: "open",
  title: "Fix timeout propagation",
  branch: "agent/bu-fxf19",
  ci_status: "passing",
  additions: 12,
  deletions: 3,
  opened_at: "2026-05-15T02:30:00Z",
  merged_at: "2026-05-15T04:00:00Z",
  url: "https://github.com/Tzeusy/butlers/pull/42",
};

describe("QA dossier fix-column components", () => {
  afterEach(() => {
    cleanup();
  });

  it("test_pr_panel_null_renders_escalated_message", () => {
    render(<PRPanel pr={null} whyThisFix={null} />);

    expect(screen.getByText("No PR — escalated to user.")).toBeTruthy();
    expect(screen.getByText("No PR — escalated to user.").className).toContain("italic");
    expect(screen.getByText("No PR — escalated to user.").className).toContain("font-serif");
  });

  it("test_pr_panel_renders_all_fields", () => {
    render(
      <PRPanel
        pr={pr}
        whyThisFix="The failing runtime ignored catalog timeouts."
        diffSnapshot={[
          { kind: "meta", text: "src/butlers/core/spawner.py" },
          { kind: "-", text: "runtime.invoke(prompt)" },
          { kind: "+", text: "runtime.invoke(prompt, timeout=session_timeout_s)" },
        ]}
      />,
    );

    expect(screen.getByText("open")).toBeTruthy();
    expect(screen.getByText("pr #42 · open")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Open PR" }).getAttribute("href")).toBe(pr.url);
    expect(screen.getByText("Fix timeout propagation")).toBeTruthy();
    expect(screen.getByText("agent/bu-fxf19 · ci passing · +12 / -3")).toBeTruthy();
    expect(screen.getByText("Why this fix")).toBeTruthy();
    expect(screen.getByText("The failing runtime ignored catalog timeouts.")).toBeTruthy();
    expect(screen.getByText("Diff preview")).toBeTruthy();
    expect(screen.getByText("runtime.invoke(prompt, timeout=session_timeout_s)")).toBeTruthy();
    expect(screen.getByText("opened", { exact: false })).toBeTruthy();
    expect(screen.getByText("merged", { exact: false })).toBeTruthy();
    expect(screen.getByText("2026-05-15T02:30:00Z").tagName).toBe("TIME");
    expect(screen.getByText("2026-05-15T04:00:00Z").tagName).toBe("TIME");
  });

  it("test_pr_panel_omits_empty_diff_preview", () => {
    render(<PRPanel pr={pr} whyThisFix="The failing runtime ignored catalog timeouts." diffSnapshot={[]} />);

    expect(screen.queryByText("Diff preview")).toBeNull();
  });

  it("test_diff_preview_classifies_kinds", () => {
    render(
      <DiffPreview
        lines={[
          { kind: "meta", text: "@@ core/spawner.py @@" },
          { kind: "-", text: "old call" },
          { kind: "+", text: "new call" },
          { kind: " ", text: "context call" },
        ]}
      />,
    );

    expect(screen.getByTestId("qa-diff-line-meta").className).toContain("bg-muted");
    expect(screen.getByTestId("qa-diff-line-minus").className).toContain("bg-red");
    expect(screen.getByTestId("qa-diff-line-plus").className).toContain("bg-emerald");
    expect(screen.getByTestId("qa-diff-line-context").className).toContain("bg-transparent");
    expect(screen.getByText("new call").className).toContain("whitespace-pre");
  });

  it("test_counter_evidence_empty_renders_nothing", () => {
    const { container } = render(<CounterEvidence items={[]} />);

    expect(container.innerHTML).toBe("");
  });

  it("test_diff_preview_empty_renders_nothing", () => {
    const { container } = render(<DiffPreview lines={[]} />);

    expect(container.innerHTML).toBe("");
  });
});
