/**
 * Playwright smoke tests — entity-redesign routes (bu-81rkz / tasks.md §4.5)
 *
 * Gap F-07 from entity-redesign-reconciliation-frontend.md: No Playwright tests
 * existed for entity-redesign routes. This file covers the required scenarios.
 *
 * NOTE ON "FIVE TABS":
 * The original spec (dashboard-relationship §"Entity detail page") described
 * a five-tab layout (Notes, Interactions, Gifts, Loans, Timeline). The actual
 * implementation replaced that with a unified ActivityTimeline section and
 * six filter pills: All | Interactions | Notes | Gifts | Loans | Life events.
 * This file tests the implemented layout — see gap D-01 in the reconciliation
 * report for the spec-to-code delta.
 *
 * DESIGN:
 * - All API calls are intercepted via page.route() — no real backend required.
 * - Tests skip gracefully when the preview/dev server is not reachable.
 * - HTTP 4xx/5xx responses are NOT skipped — they signal a broken build.
 *
 * Prerequisites:
 *   npm run preview    (or dev, set PLAYWRIGHT_BASE_URL)
 *   npm run test:e2e:install  (once per machine for browser binaries)
 *
 * Run:
 *   cd frontend && npm run test:e2e -- entity-redesign.spec.ts
 */

import { test, expect, type Page } from "@playwright/test";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const ENTITY_ID = "ent-fixture-0001";
const CONTACT_ID = "cnt-fixture-0001";
const TIMEOUT_MS = 10_000;

// ---------------------------------------------------------------------------
// Fixture data — minimal valid shapes derived from frontend/src/api/types.ts
// ---------------------------------------------------------------------------

/** Minimal valid EntityDetail wrapped in an ApiResponse envelope. */
const MOCK_ENTITY_DETAIL = {
  data: {
    id: ENTITY_ID,
    canonical_name: "Alice Fixture",
    entity_type: "person",
    aliases: [],
    roles: [],
    fact_count: 0,
    linked_contact_id: null,
    linked_contact_name: null,
    unidentified: false,
    source_butler: null,
    source_scope: null,
    archived: false,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    dunbar_tier: null,
    dunbar_score: null,
    metadata: {},
    recent_facts: [],
    recent_facts_total: 0,
    recent_facts_offset: 0,
    recent_facts_limit: 200,
    recent_facts_has_more: false,
    entity_info: [],
  },
  meta: {},
};

// Note: The /contacts/:contactId route now uses ContactEntityRedirect (PR #2000)
// which calls GET /api/relationship/contacts/:id/entity (the entity-resolver
// sub-endpoint), not the full contact detail endpoint.  The old ContactDetail
// mock objects have been replaced with entity-resolver response stubs in the
// contact detail tests below.

/** A dunbar_tier_override timeline event. */
const MOCK_DUNBAR_TIER_OVERRIDE_ITEM = {
  kind: "dunbar_tier_override",
  id: "tl-001",
  content: "Tier set to 2",
  valid_at: "2026-03-01T12:00:00Z",
  predicate: "dunbar_tier_override",
  metadata: { tier: 2 },
};

/** A generic interaction timeline item. */
const MOCK_INTERACTION_ITEM = {
  kind: "interaction",
  id: "tl-002",
  content: "Caught up over coffee",
  valid_at: "2026-04-10T09:00:00Z",
  predicate: "interaction_in_person",
  metadata: {},
};

// ---------------------------------------------------------------------------
// Helper: attempt navigation; return false if server unreachable
// ---------------------------------------------------------------------------

async function tryNavigate(page: Page, url: string): Promise<boolean> {
  try {
    await page.goto(url, { timeout: TIMEOUT_MS });
    return true;
  } catch {
    return false;
  }
}

// ---------------------------------------------------------------------------
// Helper: install base entity API stubs (empty state)
// ---------------------------------------------------------------------------

/**
 * Stubs all relationship-entity sub-endpoints for ENTITY_ID to return empty
 * arrays, and stubs the memory entity endpoint to return MOCK_ENTITY_DETAIL.
 *
 * Call this BEFORE page.goto() so stubs are installed before any fetch fires.
 */
async function installEntityStubs(page: Page, overrides: {
  timeline?: unknown[];
  gifts?: unknown[];
  loans?: unknown[];
  linkedContacts?: unknown[];
  messageThreads?: unknown[];
  dates?: unknown[];
} = {}) {
  // Memory entity endpoint (EntityDetail in ApiResponse envelope)
  await page.route(`**/api/memory/entities/${ENTITY_ID}**`, (route) => {
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(MOCK_ENTITY_DETAIL),
    });
  });

  // Relationship sub-endpoints — each returns empty array by default
  const subRoutes: Array<{ suffix: string; key: keyof typeof overrides }> = [
    { suffix: "timeline", key: "timeline" },
    { suffix: "gifts", key: "gifts" },
    { suffix: "loans", key: "loans" },
    { suffix: "linked-contacts", key: "linkedContacts" },
    { suffix: "message-threads", key: "messageThreads" },
    { suffix: "dates", key: "dates" },
  ];

  for (const { suffix, key } of subRoutes) {
    const data = overrides[key] ?? [];
    await page.route(
      `**/api/relationship/entities/${ENTITY_ID}/${suffix}**`,
      (route) => {
        route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(data),
        });
      },
    );
  }
}

// ---------------------------------------------------------------------------
// Test suite
// ---------------------------------------------------------------------------

test.describe("entity-redesign: entity detail page", () => {

  // -------------------------------------------------------------------------
  // Test 1: Route loads without error
  // -------------------------------------------------------------------------

  test("smoke: /entities/:id route loads without error", async ({ page, baseURL }) => {
    await installEntityStubs(page);

    const ok = await tryNavigate(page, `/entities/${ENTITY_ID}`);
    if (!ok) {
      test.skip(
        true,
        `Dev server not reachable at ${baseURL} — start it with: npm run preview`,
      );
      return;
    }

    // URL must contain the entity ID (no redirect away)
    expect(page.url()).toContain(`/entities/${ENTITY_ID}`);

    // The page must render some content (not an error screen)
    // The entity canonical_name from the mock should be visible.
    // Use .first() because the DetailPage archetype renders the title twice:
    // once in the page header (text-3xl) and once in the identity section (text-2xl).
    await expect(page.getByRole("heading", { name: "Alice Fixture" }).first()).toBeVisible();
  });

  // -------------------------------------------------------------------------
  // Test 2: Activity filter pills render in empty state
  //
  // NOTE: The spec described "five tabs" (Notes, Interactions, Gifts, Loans,
  // Timeline). The implementation uses a unified ActivityTimeline with filter
  // pills instead. This test verifies the six implemented filter pills are
  // present and the empty-state message is shown.
  // -------------------------------------------------------------------------

  test("activity filter pills render in empty state on a fresh entity", async ({ page, baseURL }) => {
    await installEntityStubs(page);

    const ok = await tryNavigate(page, `/entities/${ENTITY_ID}`);
    if (!ok) {
      test.skip(
        true,
        `Dev server not reachable at ${baseURL} — start it with: npm run preview`,
      );
      return;
    }

    // The "Activity" section heading must be present
    await expect(page.getByRole("heading", { name: "Activity" })).toBeVisible();

    // All six filter pills must be visible (the design always renders them)
    const expectedPills = [
      "All",
      "Interactions",
      "Notes",
      "Gifts",
      "Loans",
      "Life events",
    ];
    for (const label of expectedPills) {
      // Pills are <button> elements whose text content starts with the label.
      // Use getByRole('button') with a regex to handle the count suffix.
      await expect(
        page.getByRole("button", { name: new RegExp(`^${label}`) }),
      ).toBeVisible();
    }

    // With an empty timeline the empty-state copy should be visible
    await expect(
      page.getByText("No activity recorded yet."),
    ).toBeVisible();
  });

  // -------------------------------------------------------------------------
  // Test 3: Timeline includes dunbar_tier_override events
  // -------------------------------------------------------------------------

  test("timeline includes dunbar_tier_override events", async ({ page, baseURL }) => {
    await installEntityStubs(page, {
      timeline: [MOCK_DUNBAR_TIER_OVERRIDE_ITEM, MOCK_INTERACTION_ITEM],
    });

    const ok = await tryNavigate(page, `/entities/${ENTITY_ID}`);
    if (!ok) {
      test.skip(
        true,
        `Dev server not reachable at ${baseURL} — start it with: npm run preview`,
      );
      return;
    }

    // The timeline renders with the "All" pill active by default.
    // Both items should be visible.
    await expect(page.getByRole("heading", { name: "Activity" })).toBeVisible();

    // The dunbar_tier_override item content should appear in the feed
    await expect(page.getByText("Tier set to 2")).toBeVisible();

    // The interaction item content should also appear
    await expect(page.getByText("Caught up over coffee")).toBeVisible();

    // The dunbar_tier_override predicate renders as "dunbar tier override"
    // (predicate.replaceAll('_', ' ') — see TimelineRow in EntityDetailPage.tsx)
    await expect(page.getByText("dunbar tier override")).toBeVisible();
  });

  // -------------------------------------------------------------------------
  // Test 4 (stretch): Populated timeline + panels
  //
  // Full seeding of all sections (gifts, loans, linked contacts, message threads)
  // requires a wide stubbing surface and careful coordination with the PulseStrip
  // component, which independently fetches timeline/gifts/loans. Deferred as a
  // dedicated follow-up so we avoid fragile "stub everything exactly" coupling.
  // -------------------------------------------------------------------------

  test.skip(
    "stretch: populated tabs render after seeding facts (deferred — wide stubbing surface)",
    () => {
      // TODO(bu-81rkz): Stub all five section endpoints with non-empty data,
      // navigate to the entity detail page, and verify each panel renders its
      // content. The GiftsPanel and LoansPanel are only rendered when non-empty,
      // so this requires coordinating stubs across PulseStrip + section panels.
      // Implement once the entity detail layout is stable (no active refactors).
    },
  );

});

// ---------------------------------------------------------------------------
// Test suite: contact detail page
//
// The /contacts/:contactId route now uses ContactEntityRedirect (PR #2000).
// It calls GET /api/relationship/contacts/:id/entity (the entity-resolver
// sub-endpoint, NOT the full contact detail endpoint) and either:
//   - redirects to /entities/:entityId when entity_id is set, or
//   - shows a "Contact not linked to an entity" recovery state when unlinked.
// The old ContactDetailPage (with tabs) is no longer rendered.
// ---------------------------------------------------------------------------

test.describe("entity-redesign: contact detail page", () => {

  // -------------------------------------------------------------------------
  // Test 5: Unlinked contact shows recovery state (no tab block)
  //
  // When a contact has no entity_id, ContactEntityRedirect renders an empty
  // state ("Contact not linked to an entity") — the old tabbed layout is gone.
  // -------------------------------------------------------------------------

  test("contact detail page does not render a tab block", async ({ page, baseURL }) => {
    // Stub the entity-resolver sub-endpoint (unlinked: no entity_id)
    await page.route(`**/api/relationship/contacts/${CONTACT_ID}/entity**`, (route) => {
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ entity_id: null, status: "unlinked" }),
      });
    });

    const ok = await tryNavigate(page, `/contacts/${CONTACT_ID}`);
    if (!ok) {
      test.skip(
        true,
        `Dev server not reachable at ${baseURL} — start it with: npm run preview`,
      );
      return;
    }

    // Recovery state must be shown (contact not linked to entity)
    await expect(
      page.getByText("Contact not linked to an entity"),
    ).toBeVisible({ timeout: TIMEOUT_MS });

    // No tablist must be present anywhere on the page
    await expect(page.locator('[role="tablist"]')).not.toBeAttached();
  });

  // -------------------------------------------------------------------------
  // Test 6: Linked contact redirects directly to /entities/:id
  //
  // When the entity-resolver returns an entity_id, ContactEntityRedirect
  // performs a client-side navigate() to /entities/:entityId immediately.
  // -------------------------------------------------------------------------

  test("entity link in contact header navigates to /entities/:id", async ({ page, baseURL }) => {
    // Stub the entity-resolver sub-endpoint (linked: has entity_id)
    await page.route(`**/api/relationship/contacts/${CONTACT_ID}/entity**`, (route) => {
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ entity_id: ENTITY_ID, status: "linked" }),
      });
    });

    // Stub the entity detail endpoints so the redirect destination renders
    await installEntityStubs(page);

    const ok = await tryNavigate(page, `/contacts/${CONTACT_ID}`);
    if (!ok) {
      test.skip(
        true,
        `Dev server not reachable at ${baseURL} — start it with: npm run preview`,
      );
      return;
    }

    // ContactEntityRedirect navigates to /entities/:entityId immediately.
    await page.waitForURL(new RegExp(`/entities/${ENTITY_ID}`), { timeout: TIMEOUT_MS });
    expect(page.url()).toContain(`/entities/${ENTITY_ID}`);

    // The entity detail page renders the entity name
    await expect(
      page.getByRole("heading", { name: "Alice Fixture" }).first(),
    ).toBeVisible({ timeout: TIMEOUT_MS });
  });

});
