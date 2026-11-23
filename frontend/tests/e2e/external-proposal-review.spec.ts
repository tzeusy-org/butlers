/**
 * REQ-dashboard-approvals-904, "Review is keyboard and screen-reader accessible",
 * walked in a real browser at desktop and phone (375x667, touch) with synthetic data.
 *
 * Per viewport: the typed review is a named region that precedes the decision controls
 * in reading and tab order; every control is reached by Tab and has an accessible name; the evidence URL
 * is inert text (no link, and no request to its host); axe-core reports no violation
 * in the review; and an unresolvable entity leaves Approve disabled and described by
 * its reason while Deny stays keyboard operable.
 */

import { createRequire } from "node:module";

import { expect, test, type Page } from "@playwright/test";

import { emptyApprovalMetrics } from "./fixtures/empty-api";
import { mockJson, type ResponseOf } from "./fixtures/api-responses";

const require = createRequire(import.meta.url);
const AXE_PATH = require.resolve("axe-core/axe.min.js");

const ACTION_ID = "55555555-5555-4555-8555-555555555555";
const NOW = "2026-11-01T08:30:00Z";
const EVIDENCE_URL = "https://evidence.example.com/synthetic/page?ref=1";
const SHOWN = { tool_name: "relationship_assert_fact", args_digest: "d".repeat(64) };

type Detail = ResponseOf<"getApprovalDetail">["data"];
type Review = NonNullable<Detail["external_proposal_review"]>;

function detail(review: Partial<Review> = {}): Detail {
  return {
    id: ACTION_ID,
    title: "Relationship Assert Fact (relationship)",
    butler: "relationship",
    status: "pending",
    created_at: NOW,
    expires_at: "2026-11-15T08:30:00Z",
    why: "An external AI client proposed a 'works-at' relationship between two existing people.",
    evidence: [{ type: "text", ref: "external_mcp:client=synthetic-client", note: "Client label." }],
    proposed_action: {
      tool_name: "relationship_assert_fact",
      agent_summary: "External proposal: relationship fact 'works-at'",
    },
    call_preview: null,
    external_proposal_review: {
      proposal_id: "66666666-6666-4666-8666-666666666666",
      client_label: "synthetic-client",
      subject: { id: "77777777-7777-4777-8777-777777777777", name: "Synthetic Person A", entity_type: "person", status: "live" },
      predicate: "works-at",
      object: { id: "88888888-8888-4888-8888-888888888888", name: "Synthetic Org B", entity_type: "organization", status: "live" },
      note: "Synthetic note: their profile page lists the role.",
      evidence_url: EVIDENCE_URL,
      authority_statement:
        "An external AI client proposed this fact; neither you nor your butlers did. If you approve, it is written as a third-party report.",
      unresolvable_entity: false,
      approve_unavailable_reason: null,
      displayed_call: SHOWN,
      ...review,
    },
  };
}

async function open(page: Page, shown: Detail): Promise<string[]> {
  const evidenceRequests: string[] = [];
  page.on("request", (request) => {
    if (new URL(request.url()).hostname === "evidence.example.com") evidenceRequests.push(request.url());
  });
  const fixtures: Record<string, string> = {
    "**/api/approvals?*": mockJson("getApprovalsFlat", {
      data: [{ id: ACTION_ID, butler: "relationship", tool_name: SHOWN.tool_name, status: "pending", created_at: NOW }],
      meta: { stalled_count: 0, sources_degraded: [] },
    }),
    "**/api/approvals/history*": mockJson("getApprovalsHistory", { data: [], meta: { sources_degraded: [] } }),
    "**/api/approvals/policy": mockJson("getApprovalsPolicy", { data: { timezone: "UTC" }, meta: {} }),
    "**/api/approvals/unroutable": mockJson("getUnroutableAttention", { data: [], meta: {} }),
    "**/api/approvals/rules*": mockJson("getApprovalRules", { data: [], meta: { total: 0, offset: 0, limit: 50, has_more: false } }),
    "**/api/approvals/metrics": mockJson("getApprovalMetrics", emptyApprovalMetrics),
    [`**/api/approvals/${ACTION_ID}`]: mockJson("getApprovalDetail", { data: shown, meta: {} }),
    [`**/api/approvals/${ACTION_ID}?*`]: mockJson("getApprovalDetail", { data: shown, meta: {} }),
  };
  for (const [glob, body] of Object.entries(fixtures)) {
    await page.route(glob, (route) =>
      route.request().method() === "GET"
        ? route.fulfill({ status: 200, contentType: "application/json", body })
        : route.fallback(),
    );
  }
  await page.goto(`/approvals/${ACTION_ID}`);
  await expect(page.getByRole("region", { name: "External proposal review" })).toBeVisible();
  return evidenceRequests;
}

/** Tab from the top of the page until focus reaches the button named *name*. */
async function tabTo(page: Page, name: string): Promise<void> {
  for (let i = 0; i < 120; i++) {
    await page.keyboard.press("Tab");
    const label = await page.evaluate(() => {
      const el = document.activeElement;
      return el?.tagName === "BUTTON" ? (el.textContent ?? "").trim() : "";
    });
    if (label === name) return;
  }
  throw new Error(`keyboard never reached ${name}`);
}

/** The review precedes the decision controls in reading (and tab) order. */
async function reviewPrecedesFocus(page: Page): Promise<boolean> {
  return page.evaluate(() => {
    const region = document.querySelector('[data-testid="external-proposal-review"]');
    const focused = document.activeElement;
    return !!region && !!focused &&
      !!(region.compareDocumentPosition(focused) & Node.DOCUMENT_POSITION_FOLLOWING);
  });
}

async function axeViolations(page: Page): Promise<string[]> {
  await page.addScriptTag({ path: AXE_PATH });
  return page.evaluate(async () => {
    const runner = (window as unknown as {
      axe: { run: (context: Element) => Promise<{ violations: { id: string }[] }> };
    }).axe;
    const region = document.querySelector('[data-testid="external-proposal-review"]');
    if (!region) return ["review-missing"];
    const result = await runner.run(region);
    return result.violations.map((violation) => violation.id);
  });
}

const VIEWPORTS = [
  { name: "desktop", use: { viewport: { width: 1920, height: 1080 } } },
  { name: "phone", use: { viewport: { width: 375, height: 667 }, isMobile: true, hasTouch: true } },
] as const;

for (const viewport of VIEWPORTS) {
  test.describe(`external proposal review (${viewport.name})`, () => {
    test.use(viewport.use);

    test("is keyboard reachable, named, inert and axe-clean", async ({ page }) => {
      const evidenceRequests = await open(page, detail());
      const review = page.getByRole("region", { name: "External proposal review" });
      await expect(review).toContainText("Synthetic Person A");
      await expect(review).toContainText("synthetic-client");
      await expect(review.getByText(EVIDENCE_URL, { exact: true })).toBeVisible();
      await expect(review.locator("a, [href], img, iframe")).toHaveCount(0);
      await expect(page.getByRole("link", { name: /evidence\.example\.com/ })).toHaveCount(0);

      await tabTo(page, "Approve");
      await expect(page.getByRole("button", { name: "Approve" })).toBeFocused();
      expect(await reviewPrecedesFocus(page)).toBe(true);
      await page.keyboard.press("Tab");
      await expect(page.getByRole("button", { name: "Deny" })).toBeFocused();
      await expect(page.getByRole("button", { name: "Defer" })).toBeVisible();
      await expect(page.getByRole("textbox", { name: /deny reason/i })).toBeVisible();
      await expect(page.getByText(/always allow|standing rule/i)).toHaveCount(0);

      expect(await axeViolations(page)).toEqual([]);
      expect(evidenceRequests).toEqual([]);
    });

    test("an unresolvable entity disables Approve with its reason and keeps Deny", async ({ page }) => {
      const reason =
        "The subject entity 77777777-7777-4777-8777-777777777777 was merged into another entity (merged). This proposal cannot be applied; reject it instead.";
      await open(page, detail({
        unresolvable_entity: true,
        displayed_call: null,
        approve_unavailable_reason: reason,
        subject: { id: "77777777-7777-4777-8777-777777777777", name: "Synthetic Person A", entity_type: "person", status: "merged" },
      }));
      const approve = page.getByRole("button", { name: "Approve" });
      await expect(approve).toBeDisabled();
      await expect(approve).toHaveAccessibleDescription(/Approve unavailable:.*merged/);

      const denies: unknown[] = [];
      await page.route(`**/api/approvals/${ACTION_ID}/deny`, (route) => {
        denies.push(route.request().postDataJSON());
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: mockJson("denyApproval", {
            data: { id: ACTION_ID, butler: "relationship", tool_name: SHOWN.tool_name, tool_args: {}, status: "rejected", requested_at: NOW },
            meta: {},
          }),
        });
      });
      // A disabled Approve is not a tab stop; Deny is reachable and Enter decides.
      await tabTo(page, "Deny");
      await expect(page.getByRole("button", { name: "Deny" })).toBeFocused();
      expect(await reviewPrecedesFocus(page)).toBe(true);
      await page.keyboard.press("Enter");
      await expect.poll(() => denies.length).toBe(1);
      expect(await axeViolations(page)).toEqual([]);
    });
  });
}
