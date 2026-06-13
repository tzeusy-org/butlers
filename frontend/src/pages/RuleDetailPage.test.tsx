/**
 * Component tests for RuleDetailPage — the rule's editorial detail page
 * (bu-2ix8d.7).
 *
 * Acceptance (pr/overview/memory-redesign/prompts/06-detail-pages.md "Rule"):
 *   - Shared skeleton: eyebrow (RULE · <short id>), heading = directive text,
 *     state line, KV band — exactly one <h1>, no "Details" chrome.
 *   - Outcome record two-line format (applied/helpful/harmful/effectiveness).
 *   - The `harmful` fragment is --red ONLY when > 0 (zero harm → zero red).
 *   - Provenance `derived from episode` renders when source_episode_id set,
 *     and the section is omitted otherwise.
 *   - No commit footer (mutations live only on the fact page).
 */

import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";

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
  id: "abcd1234-0000-0000-0000-000000000000",
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
  source_episode_id: "ep-7abcdef0",
  source_butler: "general",
  created_at: "2025-02-01T08:00:00Z",
  last_applied_at: "2025-04-01T14:00:00Z",
  last_evaluated_at: "2025-04-15T10:00:00Z",
  tags: ["safety", "ux"],
  metadata: {},
};

function setRule(rule: MemoryRule | null, opts: Partial<UseRuleResult> = {}) {
  vi.mocked(useRule).mockReturnValue({
    data: rule ? { data: rule } : undefined,
    isLoading: false,
    error: null,
    ...opts,
  } as UseRuleResult);
}

function html(): string {
  return renderToStaticMarkup(
    <MemoryRouter>
      <RuleDetailPage />
    </MemoryRouter>,
  );
}

describe("RuleDetailPage", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("renders the editorial skeleton with a single H1 = directive text", () => {
    setRule(BASE_RULE);
    const out = html();
    expect((out.match(/<h1[^>]*>/g) ?? []).length).toBe(1);
    expect(out).toContain("Always confirm before deleting records");
    expect(out).toContain("RULE · ABCD1234");
    // State line in the API's words
    expect(out).toContain("established");
    expect(out).toContain("permanent permanence");
  });

  it("renders the outcome record (applied/helpful/harmful/effectiveness)", () => {
    setRule(BASE_RULE);
    const out = html();
    expect(out).toContain("applied 12");
    expect(out).toContain("helpful 10");
    expect(out).toContain("harmful 0");
    expect(out).toContain("effectiveness 0.75");
    expect(out).toContain("last applied 2025-04-01");
    expect(out).toContain("last evaluated 2025-04-15");
  });

  it("does NOT color the harmful fragment when harmful_count is 0", () => {
    setRule({ ...BASE_RULE, harmful_count: 0 });
    const out = html();
    // The harmful span must not carry the --red token when harm is zero.
    expect(out).not.toMatch(/var\(--red\)[^<]*harmful 0/);
    expect(out).not.toMatch(/harmful 0[^<]*var\(--red\)/);
  });

  it("colors the harmful fragment --red when harmful_count > 0", () => {
    setRule({ ...BASE_RULE, harmful_count: 4 });
    const out = html();
    expect(out).toContain("harmful 4");
    expect(out).toContain("var(--red)");
  });

  it("renders provenance derived-from-episode when source_episode_id set", () => {
    setRule(BASE_RULE);
    const out = html();
    expect(out).toContain("PROVENANCE");
    expect(out).toContain("derived from episode");
  });

  it("omits the PROVENANCE section when no source episode", () => {
    setRule({ ...BASE_RULE, source_episode_id: null });
    const out = html();
    expect(out).not.toContain("PROVENANCE");
  });

  it("renders no commit footer (no Confirm/Retract buttons)", () => {
    setRule(BASE_RULE);
    const out = html();
    expect(out).not.toContain("Confirm");
    expect(out).not.toContain("Retract");
  });

  it("renders a not-found voice line when the rule is absent", () => {
    setRule(null);
    const out = html();
    expect(out).toContain("not on the books");
  });
});
