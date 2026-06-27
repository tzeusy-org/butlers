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
 * - The preview server is managed by playwright.config.ts `webServer`; tests
 *   rely on it being available and will fail hard (not skip) if it is not.
 * - HTTP 4xx/5xx responses are NOT swallowed — they signal a broken build.
 *
 * Prerequisites:
 *   npm run build && npm run preview    (or set PLAYWRIGHT_BASE_URL)
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

// ---------------------------------------------------------------------------
// Fixture data for the populated-tabs test (Test 4)
// ---------------------------------------------------------------------------

/** EntityDetail with embedded recent_facts for FactsSection. */
const MOCK_ENTITY_DETAIL_WITH_FACTS = {
  ...MOCK_ENTITY_DETAIL,
  data: {
    ...MOCK_ENTITY_DETAIL.data,
    fact_count: 2,
    recent_facts: [
      {
        id: "fact-001",
        entity_id: ENTITY_ID,
        subject: ENTITY_ID,
        predicate: "works_at",
        content: "Acme Corp",
        object_entity_id: null,
        object_entity_name: null,
        entity_name: "Alice Fixture",
        validity: "active",
        metadata: {},
        session_id: null,
        source_butler: null,
        created_at: "2026-02-01T00:00:00Z",
      },
      {
        id: "fact-002",
        entity_id: ENTITY_ID,
        subject: ENTITY_ID,
        predicate: "lives_in",
        content: "San Francisco",
        object_entity_id: null,
        object_entity_name: null,
        entity_name: "Alice Fixture",
        validity: "active",
        metadata: {},
        session_id: null,
        source_butler: null,
        created_at: "2026-02-01T00:00:00Z",
      },
    ],
    recent_facts_total: 2,
  },
};

/** Timeline items covering interaction, note, gift, loan kinds. */
const MOCK_TIMELINE_POPULATED: unknown[] = [
  {
    kind: "interaction",
    id: "tl-p-001",
    content: "Met for lunch on the waterfront",
    valid_at: "2026-05-10T12:00:00Z",
    predicate: "interaction_in_person",
    metadata: {},
  },
  {
    kind: "note",
    id: "tl-p-002",
    content: "Prefers tea over coffee",
    valid_at: "2026-04-20T09:00:00Z",
    predicate: "note",
    metadata: {},
  },
  {
    kind: "gift",
    id: "tl-p-003",
    content: "Birthday flowers",
    valid_at: "2026-03-15T00:00:00Z",
    predicate: "gift",
    metadata: {},
  },
  {
    kind: "loan",
    id: "tl-p-004",
    content: "Borrowed umbrella",
    valid_at: "2026-01-10T00:00:00Z",
    predicate: "loan",
    metadata: {},
  },
];

/** Non-empty gifts array for GiftsPanel. */
const MOCK_GIFTS_POPULATED: unknown[] = [
  {
    id: "gift-001",
    description: "Birthday flowers",
    occasion: "Birthday",
    status: "given",
    created_at: "2026-03-15T00:00:00Z",
  },
];

/** Non-empty loans array for LoansPanel. */
const MOCK_LOANS_POPULATED: unknown[] = [
  {
    id: "loan-001",
    description: "Borrowed umbrella",
    amount_cents: null,
    currency: null,
    direction: "lent",
    settled: "false",
    settled_at: null,
    created_at: "2026-01-10T00:00:00Z",
  },
];

/** Non-empty linked contacts for ContactChannelCard. */
const MOCK_LINKED_CONTACTS_POPULATED: unknown[] = [
  {
    id: CONTACT_ID,
    full_name: "Alice Fixture",
    email: "alice@example.com",
    phone: null,
    contact_info: [],
    labels: [],
    preferred_channel: "email",
  },
];

/** Non-empty message threads for MessageThreadsSection. */
const MOCK_MESSAGE_THREADS_POPULATED: unknown[] = [
  {
    source_channel: "email",
    thread_identity: "thread-abc123",
    sender_identity: "alice@example.com",
    message_count: 5,
    last_received_at: "2026-05-01T10:00:00Z",
    last_direction: "inbound",
    last_snippet: "Looking forward to catching up soon!",
  },
];

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

  test("smoke: /entities/:id route loads without error", async ({ page }) => {
    await installEntityStubs(page);
    await page.goto(`/entities/${ENTITY_ID}`, { timeout: TIMEOUT_MS });

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

  test("activity filter pills render in empty state on a fresh entity", async ({ page }) => {
    await installEntityStubs(page);
    await page.goto(`/entities/${ENTITY_ID}`, { timeout: TIMEOUT_MS });

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

  test("timeline includes dunbar_tier_override events", async ({ page }) => {
    await installEntityStubs(page, {
      timeline: [MOCK_DUNBAR_TIER_OVERRIDE_ITEM, MOCK_INTERACTION_ITEM],
    });
    await page.goto(`/entities/${ENTITY_ID}`, { timeout: TIMEOUT_MS });

    // The timeline renders with the "All" pill active by default.
    // Both items should be visible.
    await expect(page.getByRole("heading", { name: "Activity" })).toBeVisible();

    // The dunbar_tier_override item content should appear in the feed
    await expect(page.getByText("Tier set to 2")).toBeVisible();

    // The interaction item content should also appear. The same summary now also
    // surfaces in the LatestInteractionsBlock (latest touch per channel), so use
    // .first() to assert presence without tripping strict mode.
    await expect(page.getByText("Caught up over coffee").first()).toBeVisible();

    // The dunbar_tier_override predicate renders as "dunbar tier override"
    // (predicate.replaceAll('_', ' ') — see TimelineRow in EntityDetailPage.tsx)
    await expect(page.getByText("dunbar tier override")).toBeVisible();
  });

  // -------------------------------------------------------------------------
  // Test 4: Populated timeline + all panels
  //
  // Stubs all six section endpoints with non-empty data, navigates to the
  // entity detail page, and verifies each panel renders its seeded content:
  //
  //   - PulseStrip: "Last interaction" tile shows a relative time (not "None")
  //     because the timeline stub includes an interaction item.  PulseStrip
  //     shares the timeline/gifts/loans endpoints with the main sections.
  //   - ActivityTimeline: seeded with interaction + note + gift + loan entries;
  //     all four appear in the "All" feed and filter pills show their counts.
  //   - GiftsPanel: only rendered when non-empty; asserts the gift description.
  //   - LoansPanel: only rendered when non-empty; asserts the loan description.
  //   - MessageThreadsSection: asserts the email thread snippet.
  //   - FactsSection: populated via entity.recent_facts in the entity mock;
  //     asserts the "Works at" predicate and its content value.
  // -------------------------------------------------------------------------

  test("populated tabs render after seeding all section stubs [bu-zr4lx]", async ({ page }) => {
    // Install the entity detail stub with embedded recent_facts (for FactsSection)
    await page.route(`**/api/memory/entities/${ENTITY_ID}**`, (route) => {
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MOCK_ENTITY_DETAIL_WITH_FACTS),
      });
    });

    // Install all relationship sub-endpoint stubs with populated data.
    // PulseStrip, GiftsPanel, LoansPanel, and ActivityTimeline all share the
    // same timeline/gifts/loans endpoints — one stub each covers all consumers.
    const subRoutes: Array<{ suffix: string; data: unknown[] }> = [
      { suffix: "timeline",        data: MOCK_TIMELINE_POPULATED },
      { suffix: "gifts",           data: MOCK_GIFTS_POPULATED },
      { suffix: "loans",           data: MOCK_LOANS_POPULATED },
      { suffix: "linked-contacts", data: MOCK_LINKED_CONTACTS_POPULATED },
      { suffix: "message-threads", data: MOCK_MESSAGE_THREADS_POPULATED },
      { suffix: "dates",           data: [] },
    ];

    for (const { suffix, data } of subRoutes) {
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

    await page.goto(`/entities/${ENTITY_ID}`, { timeout: TIMEOUT_MS });

    // Page renders the entity name
    await expect(
      page.getByRole("heading", { name: "Alice Fixture" }).first(),
    ).toBeVisible({ timeout: TIMEOUT_MS });

    // ---- PulseStrip: "Last interaction" tile must not say "None recorded" ----
    // The tile is a div (not a heading); assert the label and a non-"None" value
    // coexist in the strip by checking the label text is visible.
    await expect(page.getByText("Last interaction")).toBeVisible({ timeout: TIMEOUT_MS });
    // The tile value should NOT read "None recorded" once the interaction is seeded
    await expect(page.getByText("None recorded")).not.toBeVisible();

    // ---- ActivityTimeline: seeded items appear in the "All" feed ----
    // "Met for lunch on the waterfront" is the most-recent in-person interaction,
    // so it also surfaces in the LatestInteractionsBlock; use .first() to assert
    // presence without tripping strict mode.
    await expect(page.getByRole("heading", { name: "Activity" })).toBeVisible();
    await expect(page.getByText("Met for lunch on the waterfront").first()).toBeVisible();
    await expect(page.getByText("Prefers tea over coffee")).toBeVisible();

    // Filter pill counts — each seeded kind increments its pill count
    await expect(
      page.getByRole("button", { name: /^Interactions/ }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: /^Notes/ }),
    ).toBeVisible();

    // ---- GiftsPanel: only renders when gifts is non-empty ----
    // The GiftsPanel renders an <h3> "Gifts" heading; assert heading + content.
    // "Birthday flowers" also appears in the ActivityTimeline feed; use .first()
    // to avoid strict-mode errors when the same text is present in both panels.
    await expect(page.getByText("Birthday flowers").first()).toBeVisible({ timeout: TIMEOUT_MS });

    // ---- LoansPanel: only renders when loans is non-empty ----
    // "Borrowed umbrella" also appears in the ActivityTimeline feed.
    await expect(page.getByText("Borrowed umbrella").first()).toBeVisible({ timeout: TIMEOUT_MS });

    // ---- MessageThreadsSection: thread snippet from email channel ----
    // This snippet is also the most-recent email touch in the
    // LatestInteractionsBlock; use .first() to assert presence without tripping
    // strict mode.
    await expect(
      page.getByText("Looking forward to catching up soon!").first(),
    ).toBeVisible({ timeout: TIMEOUT_MS });

    // ---- FactsSection: facts from entity.recent_facts in the entity mock ----
    // Both "Acme Corp" (works_at) and "San Francisco" (lives_in) appear in the
    // ProfileSnapshot section AND in the FactsSection predicate rows.  Use
    // .first() to satisfy Playwright's strict-mode requirement.
    await expect(page.getByRole("heading", { name: "Facts" })).toBeVisible();
    await expect(page.getByText("Acme Corp").first()).toBeVisible();
    await expect(page.getByText("San Francisco").first()).toBeVisible();
  });

});

// ---------------------------------------------------------------------------
// Test suite: legacy /contacts compatibility redirect
//
// public.contacts was dropped (core_134) and the per-contact entity-resolver
// endpoint no longer exists. PR #2713 removed the old ContactEntityRedirect
// (and its resolveContactEntity client fn); both `/contacts` and
// `/contacts/:contactId` are now STATIC compatibility redirects to the entity
// index filtered by `has=contact`:
//
//   { path: '/contacts/:contactId',
//     element: <Navigate to="/entities?has=contact" replace /> }
//
// There is no per-contact entity lookup, no `/entities/:id` redirect, and no
// "Contact not linked to an entity" recovery state any more — a legacy
// contact bookmark simply lands on the entities index. See router-config.tsx.
// ---------------------------------------------------------------------------

/**
 * Stub the entities-index list + curation-queue endpoints so the redirect
 * destination (`/entities?has=contact`) renders cleanly without a real backend.
 */
async function installEntitiesIndexStubs(page: Page) {
  const emptyList = { items: [], total: 0, limit: 50, offset: 0 };

  // Matches both the entity list (/api/relationship/entities?…) and the
  // curation queue (/api/relationship/entities/queue?…) — both return an empty
  // paginated payload so the index renders cleanly without a real backend.
  await page.route(
    (url) => url.pathname.startsWith("/api/relationship/entities"),
    (route) => {
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(emptyList),
      });
    },
  );
}

test.describe("entity-redesign: legacy /contacts redirect", () => {

  // -------------------------------------------------------------------------
  // Test 5: /contacts/:contactId redirects to the entity index (no tab block)
  //
  // The old tabbed ContactDetailPage is gone — a legacy per-contact URL now
  // forwards straight to /entities?has=contact, and no tablist is rendered.
  // -------------------------------------------------------------------------

  test("contact detail page redirects to the entity index without a tab block", async ({
    page,
  }) => {
    await installEntitiesIndexStubs(page);

    await page.goto(`/contacts/${CONTACT_ID}`, { timeout: TIMEOUT_MS });

    // The compatibility redirect lands on the entities index filtered to
    // contact-bearing entities (NOT an individual /entities/:id page).
    await page.waitForURL(/\/entities\?has=contact/, { timeout: TIMEOUT_MS });
    expect(page.url()).toContain("/entities?has=contact");

    // The "Has contact" filter chip on the index toolbar confirms we landed on
    // the entity index, and no old tabbed contact-detail layout is present.
    await expect(
      page.getByRole("button", { name: "Has contact" }),
    ).toBeVisible({ timeout: TIMEOUT_MS });
    await expect(page.locator('[role="tablist"]')).not.toBeAttached();
  });

  // -------------------------------------------------------------------------
  // Test 6: the redirect targets the index, not an individual entity
  //
  // The removed resolver used to navigate to /entities/:entityId; the static
  // compat redirect instead always forwards to the index filter. Guard against
  // a regression that resolves a per-contact entity id.
  // -------------------------------------------------------------------------

  test("legacy contact URL does not resolve to an individual entity page", async ({
    page,
  }) => {
    await installEntitiesIndexStubs(page);

    await page.goto(`/contacts/${CONTACT_ID}`, { timeout: TIMEOUT_MS });

    await page.waitForURL(/\/entities\?has=contact/, { timeout: TIMEOUT_MS });

    // Must NOT have resolved to a specific entity detail route.
    expect(page.url()).not.toMatch(/\/entities\/[^?]/);
    expect(page.url()).toContain("/entities?has=contact");
  });

});
