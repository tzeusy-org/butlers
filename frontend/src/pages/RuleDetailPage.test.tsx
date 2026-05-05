import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import RuleDetailPage from "@/pages/RuleDetailPage";
import { useRule } from "@/hooks/use-memory";
import type { MemoryRule } from "@/api/types";

vi.mock("react-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router")>();
  return { ...actual, useParams: vi.fn(() => ({ ruleId: "rule-001" })) };
});

vi.mock("@/hooks/use-memory", () => ({
  useRule: vi.fn(),
}));

type UseRuleResult = ReturnType<typeof useRule>;

const BASE_RULE: MemoryRule = {
  id: "rule-001",
  content: "Always confirm before deleting records",
  scope: "global",
  maturity: "established",
  confidence: 0.9,
  decay_rate: 0.005,
  permanence: "permanent",
  effectiveness_score: 0.75,
  applied_count: 12,
  success_count: 10,
  harmful_count: 0,
  source_episode_id: "ep-7",
  source_butler: "general",
  created_at: "2025-02-01T08:00:00Z",
  last_applied_at: "2025-04-01T14:00:00Z",
  last_evaluated_at: "2025-04-15T10:00:00Z",
  tags: ["safety", "ux"],
  metadata: {},
};

function setRuleState(rule: MemoryRule | null, opts: Partial<UseRuleResult> = {}) {
  vi.mocked(useRule).mockReturnValue({
    data: rule ? { data: rule } : undefined,
    isLoading: false,
    error: null,
    ...opts,
  } as UseRuleResult);
}

function renderPage(): string {
  const queryClient = new QueryClient();
  return renderToStaticMarkup(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <RuleDetailPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("RuleDetailPage — layout", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("renders a single H1 (no double-H1 regression)", () => {
    setRuleState(BASE_RULE);
    const html = renderPage();
    const h1Matches = html.match(/<h1[^>]*>/g) ?? [];
    expect(h1Matches.length).toBe(1);
  });

  it("renders the rule content as the page title", () => {
    setRuleState(BASE_RULE);
    const html = renderPage();
    expect(html).toContain("Always confirm before deleting records");
  });

  it("renders the type pill with 'rule'", () => {
    setRuleState(BASE_RULE);
    const html = renderPage();
    expect(html).toContain("rule");
  });

  it("renders breadcrumbs back to memory and rules", () => {
    setRuleState(BASE_RULE);
    const html = renderPage();
    expect(html).toContain("/memory");
    expect(html).toContain("Rules");
  });

  it("truncates rule title to 80 chars with ellipsis", () => {
    const longContent = "a".repeat(100); // 100 chars, longer than 80
    setRuleState({ ...BASE_RULE, content: longContent });
    const html = renderPage();
    // Should contain the truncated title in the H1 (79 chars + ellipsis)
    expect(html).toContain("<h1" + " class=\"text-3xl font-bold tracking-tight\">" + "a".repeat(79) + "…</h1>");
  });

  it("does not truncate rule title under 80 chars", () => {
    const shortContent = "Keep this content as-is";
    setRuleState({ ...BASE_RULE, content: shortContent });
    const html = renderPage();
    // Should contain the untruncated content in the H1
    expect(html).toContain("<h1" + " class=\"text-3xl font-bold tracking-tight\">" + "Keep this content as-is</h1>");
  });
});

describe("RuleDetailPage — body content", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("renders maturity badge", () => {
    setRuleState(BASE_RULE);
    const html = renderPage();
    expect(html).toContain("established");
  });

  it("renders permanence badge", () => {
    setRuleState(BASE_RULE);
    const html = renderPage();
    expect(html).toContain("permanent");
    expect(html).toContain("Permanence");
  });

  it("renders effectiveness progress bar", () => {
    setRuleState(BASE_RULE);
    const html = renderPage();
    expect(html).toContain("Effectiveness");
    expect(html).toContain("75%");
  });

  it("renders confidence progress bar", () => {
    setRuleState(BASE_RULE);
    const html = renderPage();
    expect(html).toContain("Confidence");
    expect(html).toContain("90%");
  });

  it("renders applied/success/harmful counts", () => {
    setRuleState(BASE_RULE);
    const html = renderPage();
    expect(html).toContain("Applied");
    expect(html).toContain("12");
    expect(html).toContain("Successes");
    expect(html).toContain("10");
    expect(html).toContain("Harmful");
  });

  it("renders provenance when source_butler and source_episode_id are set", () => {
    setRuleState(BASE_RULE);
    const html = renderPage();
    expect(html).toContain("general");
    expect(html).toContain("ep-7");
  });

  it("renders tags", () => {
    setRuleState(BASE_RULE);
    const html = renderPage();
    expect(html).toContain("safety");
    expect(html).toContain("ux");
  });

  it("renders 'No provenance data' when no provenance fields set", () => {
    setRuleState({
      ...BASE_RULE,
      source_butler: null,
      source_episode_id: null,
    });
    const html = renderPage();
    expect(html).toContain("No provenance data");
  });
});

describe("RuleDetailPage — async states", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("renders loading state without rule content", () => {
    setRuleState(null, { isLoading: true } as Partial<UseRuleResult>);
    const html = renderPage();
    expect(html).not.toContain("Always confirm");
  });

  it("renders nothing when rule data is absent and not loading", () => {
    setRuleState(null);
    const html = renderPage();
    expect(html).not.toContain("Effectiveness");
  });
});
