/**
 * Playwright smoke tests — entity-v3 view-depth flows (bu-8qfok)
 *
 * Extends the entity-redesign smoke (entity-redesign.spec.ts, bu-81rkz) to the
 * v3 lifecycle/depth surface defined by
 *   openspec/changes/entity-v3-lifecycle-and-depth/tasks.md §9.1 (PR #2165).
 *
 * Flows covered end-to-end (against the built bundle served by `vite preview`):
 *   1. Queue duplicate card → compare → dismiss (pair leaves queue) AND merge
 *      (final confirmation gates the merge POST, dialog closes, survivor detail reachable).
 *   2. Detail: delta banner after facts change between visits; sparkline renders
 *      exactly 90 sticks; core-dates + latest-interactions blocks present.
 *   3. Workbench toggle → three rails render; provenance grid sortable;
 *      duplicate panel → compare.
 *   4. Finder: Cmd-K → type → preview pane populates; Tab lands on
 *      /entities?center=... (the Plex); empty-query owner-pinned set renders;
 *      shared-Dialog focus/dismissal behavior works in a real browser.
 *   5. Closeout gaps (PR #2239): concentration provenance marks
 *      (data-testid=concentration-provenance), Index keyboard cursor focus
 *      (tr[data-cursor]), delta-since-last-visit fact-row highlight
 *      (data-testid=delta-fact-row).
 *
 * DESIGN (matches entity-redesign.spec.ts conventions):
 * - All API calls are intercepted via page.route() — no real backend / compose
 *   stack required. CI's `frontend-e2e` job runs this against `vite preview`.
 * - Real data-testids/roles are grepped from the implementation, never invented.
 * - HTTP 4xx/5xx responses are NOT mocked — they would signal a broken build.
 *
 * API base: client.ts uses `${VITE_API_URL ?? "/api"}` + path. The entity
 * detail page reads GET /api/memory/entities/{id}; every v3 sub-block reads
 * GET/POST /api/relationship/entities/... (the `/butlers/` in client docstrings
 * is not part of the fetched path).
 *
 * Run:
 *   cd frontend && npm run test:e2e -- entity-v3-depth.spec.ts
 */

import { test, expect, type Page, type Route } from "@playwright/test";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const ENTITY_ID = "ent-v3-0001";
const PEER_ID = "ent-v3-peer-0002";
const OWNER_ID = "ent-owner-0000";
const TIMEOUT_MS = 10_000;

// ---------------------------------------------------------------------------
// Fixture builders — minimal valid shapes derived from frontend/src/api/types.ts
// ---------------------------------------------------------------------------

/** A recent_fact (memory Fact shape) embedded on the entity detail. The editorial
 *  FactsSection renders these and marks a row delta when its `id` is in the
 *  delta-facts response, so delta fact ids must match a recent_fact id. */
function recentFact(id: string, predicate: string, content: string): unknown {
  return {
    id,
    entity_id: ENTITY_ID,
    subject: ENTITY_ID,
    predicate,
    content,
    object_entity_id: null,
    object_entity_name: null,
    entity_name: "Alice V3",
    validity: "active",
    metadata: {},
    session_id: null,
    source_butler: null,
    created_at: "2026-05-30T00:00:00Z",
  };
}

/** Base EntityDetail (memory endpoint) wrapped in an ApiResponse envelope. */
function entityDetail(
  id = ENTITY_ID,
  name = "Alice V3",
  recentFacts: unknown[] = [],
): unknown {
  return {
    data: {
      id,
      canonical_name: name,
      entity_type: "person",
      aliases: [],
      roles: [],
      fact_count: recentFacts.length,
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
      recent_facts: recentFacts,
      recent_facts_total: recentFacts.length,
      recent_facts_offset: 0,
      recent_facts_limit: 200,
      recent_facts_has_more: false,
      entity_info: [],
    },
    meta: {},
  };
}

/** 90 daily activity bins (ActivityBinsResponse). Some non-zero so the sparkline
 *  renders sticks rather than the canned empty line. */
function activityBins(): { bins: Array<{ date: string; count: number }> } {
  const bins: Array<{ date: string; count: number }> = [];
  const start = new Date("2026-03-01T00:00:00Z");
  for (let i = 0; i < 90; i++) {
    const d = new Date(start.getTime() + i * 86_400_000);
    bins.push({
      date: d.toISOString().slice(0, 10),
      // a handful of active days; the rest quiet (count 0)
      count: i % 17 === 0 ? 3 : 0,
    });
  }
  return { bins };
}

/** A relationship CompareFact (types.ts CompareFact). */
function compareFact(
  id: string,
  predicate: string,
  object: string,
  store: "identity" | "narrative" = "identity",
): unknown {
  return {
    id,
    entity_id: ENTITY_ID,
    predicate,
    object,
    object_kind: "literal",
    store,
    src: "memory",
    conf: 0.9,
    verified: true,
    primary: false,
    observed_at: "2026-05-01T00:00:00Z",
    last_seen: "2026-05-01T00:00:00Z",
    staleness_band: "fresh",
  };
}

/** CompareEntitiesResponse: two columns + shared + divergent groups. */
function compareResponse(): unknown {
  return {
    a: {
      entity: {
        id: ENTITY_ID,
        canonical_name: "Alice V3",
        entity_type: "person",
        aliases: [],
        tier: null,
        state: "duplicate-candidate",
      },
      identity_facts: [compareFact("cf-a1", "has-email", "alice@example.com")],
      narrative_facts: [compareFact("cf-a2", "works-at", "Acme", "narrative")],
    },
    b: {
      entity: {
        id: PEER_ID,
        canonical_name: "Alice V3 (dup)",
        entity_type: "person",
        aliases: [],
        tier: null,
        state: "duplicate-candidate",
      },
      identity_facts: [compareFact("cf-b1", "has-email", "alice@example.com")],
      narrative_facts: [],
    },
    shared: [compareFact("cf-s1", "has-email", "alice@example.com")],
    divergent: [compareFact("cf-d1", "works-at", "Acme", "narrative")],
  };
}

/** RelationshipQueueResponse with a duplicate-candidate entry for ENTITY_ID
 *  pointing at PEER_ID. Drives both the index queue rail and the detail-page
 *  duplicate panel/workbench panel (both read GET .../entities/queue). */
function queueResponse(): unknown {
  return {
    items: [
      {
        entity_id: ENTITY_ID,
        canonical_name: "Alice V3",
        entity_type: "person",
        bucket: "duplicate-candidate",
        evidence: {
          peer_entity_ids: [PEER_ID],
          predicate: "has-email",
          shared_value: "alice@example.com",
        },
        last_seen: "2026-05-01T00:00:00Z",
      },
      {
        entity_id: PEER_ID,
        canonical_name: "Alice V3 (dup)",
        entity_type: "person",
        bucket: "duplicate-candidate",
        evidence: { peer_entity_ids: [ENTITY_ID] },
        last_seen: "2026-05-01T00:00:00Z",
      },
    ],
    total: 2,
    limit: 100,
    offset: 0,
  };
}

/** Empty queue (after dismiss). */
function emptyQueue(): unknown {
  return { items: [], total: 0, limit: 100, offset: 0 };
}

/** RelationshipEntityListResponse for the index table. */
function entityList(): unknown {
  return {
    items: [
      {
        id: ENTITY_ID,
        canonical_name: "Alice V3",
        entity_type: "person",
        aliases: [],
        roles: [],
        metadata: {},
        tier: null,
        last_seen: "2026-05-01T00:00:00Z",
        contact_fact_count: 0,
        created_at: "2026-01-01T00:00:00Z",
        updated_at: "2026-01-01T00:00:00Z",
      },
      {
        id: PEER_ID,
        canonical_name: "Bob V3",
        entity_type: "person",
        aliases: [],
        roles: [],
        metadata: {},
        tier: null,
        last_seen: "2026-05-01T00:00:00Z",
        contact_fact_count: 0,
        created_at: "2026-01-01T00:00:00Z",
        updated_at: "2026-01-01T00:00:00Z",
      },
    ],
    total: 2,
    limit: 50,
    offset: 0,
  };
}

/** NeighboursResponse (ranked, per_predicate). */
function neighboursResponse(): unknown {
  return {
    neighbours: {
      knows: [
        {
          entity_id: PEER_ID,
          canonical_name: "Bob V3",
          entity_type: "person",
          weight: 5,
        },
      ],
    },
    remainders: { knows: 0 },
  };
}

/** EntityFinderSearchResponse. */
function searchResponse(q: string): unknown {
  return {
    results: [
      {
        entity_id: ENTITY_ID,
        canonical_name: "Alice V3",
        entity_type: "person",
        score: 100,
        match_kind: "prefix",
      },
    ],
    total: 1,
    q,
    limit: 8,
  };
}

/** ConcentrationResponse with src/verified/last_seen so the provenance marks
 *  + stale-dim render. */
function concentrationResponse(): unknown {
  return {
    predicate: "knows",
    items: [
      {
        entity_id: PEER_ID,
        canonical_name: "Bob V3",
        weight_sum: 12,
        fact_count: 4,
        share: 0.6,
        last_seen: "2026-05-01T00:00:00Z",
        src: "memory",
        conf: 0.9,
        verified: true,
        primary: false,
      },
    ],
    rollup: { total: 12, top3_share: 1.0 },
    predicate_tabs: [{ predicate: "knows", label: "Knows" }],
    total: 1,
  };
}

// ---------------------------------------------------------------------------
// Route helpers
// ---------------------------------------------------------------------------

function json(route: Route, body: unknown, status = 200): Promise<void> {
  return route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

/**
 * Install the full detail-page stub surface for ENTITY_ID. `delta` controls the
 * delta-since-last-visit response; pass items + a prior mark to make the banner
 * and the delta-fact-row highlight render.
 */
async function installDetailStubs(
  page: Page,
  opts: {
    deltaItems?: unknown[];
    deltaMarkedAt?: string | null;
    queue?: unknown;
    detail?: unknown;
  } = {},
) {
  // Base entity (memory endpoint). When delta items are provided, seed matching
  // recent_facts (same ids) so the editorial FactsSection can mark delta rows —
  // the highlight matches recent_fact.id against the delta-facts id set.
  const recentFacts = (opts.deltaItems ?? []).map((d) => {
    const item = d as { id: string; predicate: string; object: string };
    return recentFact(item.id, item.predicate, item.object);
  });
  await page.route(`**/api/memory/entities/${ENTITY_ID}**`, (route) =>
    json(route, opts.detail ?? entityDetail(ENTITY_ID, "Alice V3", recentFacts)),
  );

  // 90-day sparkline
  await page.route(
    `**/api/relationship/entities/${ENTITY_ID}/activity**`,
    (route) => json(route, activityBins()),
  );

  // delta-since-last-visit (read) — the fact ids here also seed the fact list
  // below so the delta-fact-row highlight has a matching row to mark.
  await page.route(
    `**/api/relationship/entities/${ENTITY_ID}/delta-facts**`,
    (route) =>
      json(route, {
        marked_at: opts.deltaMarkedAt ?? null,
        items: opts.deltaItems ?? [],
      }),
  );

  // view-mark (write) — fires AFTER delta-facts read; return a fresh mark.
  await page.route(
    `**/api/relationship/entities/${ENTITY_ID}/view-mark**`,
    (route) =>
      json(route, { entity_id: ENTITY_ID, marked_at: "2026-06-01T00:00:00Z" }),
  );

  // core dates
  await page.route(
    `**/api/relationship/entities/${ENTITY_ID}/core-dates**`,
    (route) =>
      json(route, {
        items: [
          {
            id: "cd-1",
            predicate: "has-birthday",
            value: "--04-12",
            month: 4,
            day: 12,
            year: null,
            next_occurrence: "2027-04-12",
            days_until: 30,
            src: "memory",
            conf: 0.9,
            verified: true,
            staleness_band: "fresh",
          },
        ],
      }),
  );

  // message-threads — feeds LatestInteractionsBlock
  await page.route(
    `**/api/relationship/entities/${ENTITY_ID}/message-threads**`,
    (route) =>
      json(route, [
        {
          source_channel: "email",
          thread_identity: "thread-1",
          sender_identity: "alice@example.com",
          message_count: 3,
          last_received_at: "2026-05-20T00:00:00Z",
          last_direction: "inbound",
          last_snippet: "Latest email touch on the waterfront",
        },
      ]),
  );

  // timeline — also feeds LatestInteractionsBlock + activity sections
  await page.route(
    `**/api/relationship/entities/${ENTITY_ID}/timeline**`,
    (route) =>
      json(route, [
        {
          kind: "interaction",
          id: "tl-1",
          content: "Coffee catch-up",
          valid_at: "2026-05-18T00:00:00Z",
          predicate: "interaction_in_person",
          metadata: {},
        },
      ]),
  );

  // neighbours — workbench context rail + finder preview
  await page.route(
    `**/api/relationship/entities/${ENTITY_ID}/neighbours**`,
    (route) => json(route, neighboursResponse()),
  );

  // provenance grid facts (keyset paginated). The delta items are echoed here so
  // their fact-rows exist and can carry the delta highlight.
  await page.route(
    `**/api/relationship/entities/${ENTITY_ID}/facts**`,
    (route) =>
      json(route, {
        items: (opts.deltaItems ?? []).map((d) => {
          const fact = d as { id: string; predicate: string; object: string };
          return {
            id: fact.id,
            entity_id: ENTITY_ID,
            predicate: fact.predicate,
            object: fact.object,
            object_kind: "literal",
            store: "identity",
            src: "memory",
            conf: 0.9,
            verified: true,
            primary: false,
            weight: 4,
            last_observed_at: "2026-05-30T00:00:00Z",
            created_at: "2026-05-30T00:00:00Z",
            staleness_band: "fresh",
          };
        }),
        next_cursor: null,
        has_more: false,
      }),
  );

  // queue — duplicate-candidate bucket for ENTITY_ID
  await page.route("**/api/relationship/entities/queue**", (route) =>
    json(route, opts.queue ?? queueResponse()),
  );

  // compare / merge / dismiss
  await page.route("**/api/relationship/entities/compare**", (route) =>
    json(route, compareResponse()),
  );
  await page.route(
    `**/api/relationship/entities/${ENTITY_ID}/merge**`,
    (route) =>
      json(route, {
        kept_entity_id: ENTITY_ID,
        tombstoned_entity_id: PEER_ID,
        subject_facts_rewired: 1,
        object_facts_rewired: 0,
      }),
  );
  await page.route("**/api/relationship/entities/dismiss-pair**", (route) =>
    json(route, {
      review_id: "rev-1",
      entity_a: ENTITY_ID,
      entity_b: PEER_ID,
      outcome: "dismissed",
      shared_facts: [],
    }),
  );
}

/** Two facts that "changed since the last visit". */
const DELTA_ITEMS: unknown[] = [
  {
    id: "df-1",
    subject: ENTITY_ID,
    predicate: "works-at",
    object: "Globex",
    object_kind: "literal",
    src: "memory",
    conf: 0.9,
    store: "identity",
    validity: "active",
    created_at: "2026-05-30T00:00:00Z",
    changed_at: "2026-05-30T00:00:00Z",
  },
  {
    id: "df-2",
    subject: ENTITY_ID,
    predicate: "lives-in",
    object: "Portland",
    object_kind: "literal",
    src: "memory",
    conf: 0.9,
    store: "identity",
    validity: "active",
    created_at: "2026-05-30T00:00:00Z",
    changed_at: "2026-05-30T00:00:00Z",
  },
];

// ===========================================================================
// Suite 1: Queue → compare → dismiss / merge
// ===========================================================================

test.describe("entity-v3: queue → compare → dismiss/merge", () => {
  test("duplicate card opens compare, dismiss removes the pair from the queue", async ({
    page,
  }) => {
    // First load: queue has the duplicate pair. After dismiss, the queue
    // endpoint flips to empty so the re-fetch drops the pair.
    let dismissed = false;
    await page.route("**/api/relationship/entities/queue**", (route) =>
      json(route, dismissed ? emptyQueue() : queueResponse()),
    );
    await page.route("**/api/relationship/entities/search**", (route) =>
      json(route, { results: [], total: 0, q: "", limit: 8 }),
    );
    await page.route("**/api/relationship/entities?**", (route) =>
      json(route, entityList()),
    );
    await page.route("**/api/relationship/entities/compare**", (route) =>
      json(route, compareResponse()),
    );
    await page.route("**/api/relationship/entities/dismiss-pair**", (route) => {
      dismissed = true;
      return json(route, {
        review_id: "rev-1",
        entity_a: ENTITY_ID,
        entity_b: PEER_ID,
        outcome: "dismissed",
        shared_facts: [],
      });
    });

    await page.goto("/entities/index", { timeout: TIMEOUT_MS });

    // Queue rail with the duplicate-candidate pair is present.
    await expect(page.getByTestId("queue-rail")).toBeVisible({
      timeout: TIMEOUT_MS,
    });
    const peerButton = page.getByTestId("queue-duplicate-peer").first();
    await expect(peerButton).toBeVisible();

    // Click the peer link → compare dialog opens and renders the diff (no merge
    // can commit before the diff renders — spec "no merge bypasses review").
    await peerButton.click();
    await expect(page.getByTestId("merge-compare-dialog")).toBeVisible({
      timeout: TIMEOUT_MS,
    });
    await expect(page.getByTestId("compare-column-A")).toBeVisible();
    await expect(page.getByTestId("compare-column-B")).toBeVisible();
    // Shared evidence row carries the triggering fact.
    await expect(page.getByTestId("compare-fact").first()).toBeVisible();

    // Dismiss → dialog closes; the pair leaves the queue on re-fetch.
    await page.getByTestId("compare-dismiss").click();
    await expect(page.getByTestId("merge-compare-dialog")).not.toBeVisible({
      timeout: TIMEOUT_MS,
    });
    await expect(page.getByTestId("queue-duplicate-peer")).toHaveCount(0, {
      timeout: TIMEOUT_MS,
    });
  });

  test("merge commits the survivor and the survivor detail is reachable", async ({
    page,
  }) => {
    const mergeBodies: string[] = [];
    await page.route("**/api/relationship/entities/queue**", (route) =>
      json(route, queueResponse()),
    );
    await page.route("**/api/relationship/entities/search**", (route) =>
      json(route, { results: [], total: 0, q: "", limit: 8 }),
    );
    await page.route("**/api/relationship/entities?**", (route) =>
      json(route, entityList()),
    );
    await page.route("**/api/relationship/entities/compare**", (route) =>
      json(route, compareResponse()),
    );
    await page.route(
      `**/api/relationship/entities/${ENTITY_ID}/merge**`,
      (route) => {
        mergeBodies.push(route.request().postData() ?? "");
        return json(route, {
          kept_entity_id: ENTITY_ID,
          tombstoned_entity_id: PEER_ID,
          subject_facts_rewired: 1,
          object_facts_rewired: 0,
        });
      },
    );

    await page.goto("/entities/index", { timeout: TIMEOUT_MS });
    await page.getByTestId("queue-duplicate-peer").first().click();
    await expect(page.getByTestId("merge-compare-dialog")).toBeVisible({
      timeout: TIMEOUT_MS,
    });

    // Survivor radio defaults to A (the triggering entity). The first action
    // only opens the final accessible confirmation; it must not commit yet.
    await page.getByTestId("compare-merge").click();
    const confirmation = page.getByTestId("merge-confirmation");
    await expect(confirmation).toBeVisible({ timeout: TIMEOUT_MS });
    await expect(confirmation).toHaveAttribute("role", "alertdialog");
    expect(mergeBodies).toHaveLength(0);

    await page.getByTestId("merge-confirm").click();
    await expect(confirmation).not.toBeVisible({ timeout: TIMEOUT_MS });
    await expect(page.getByTestId("merge-compare-dialog")).not.toBeVisible({
      timeout: TIMEOUT_MS,
    });
    // The merge POST fired against the survivor's id, keeping A.
    await expect.poll(() => mergeBodies.length, { timeout: TIMEOUT_MS }).toBe(1);
    expect(mergeBodies[0]).toContain(ENTITY_ID);
    expect(mergeBodies[0]).toContain('"keepAs":"A"');

    // The survivor detail page is reachable (audit-confirmed merge target).
    await installDetailStubs(page);
    await page.goto(`/entities/${ENTITY_ID}`, { timeout: TIMEOUT_MS });
    await expect(
      page.getByRole("heading", { name: "Alice V3" }).first(),
    ).toBeVisible({ timeout: TIMEOUT_MS });
  });
});

// ===========================================================================
// Suite 2: Detail quick-refresh — sparkline, delta banner, core dates, latest
// ===========================================================================

test.describe("entity-v3: detail quick-refresh blocks", () => {
  test("sparkline renders exactly 90 sticks; core-dates + latest-interactions present", async ({
    page,
  }) => {
    await installDetailStubs(page);
    await page.goto(`/entities/${ENTITY_ID}`, { timeout: TIMEOUT_MS });

    await expect(
      page.getByRole("heading", { name: "Alice V3" }).first(),
    ).toBeVisible({ timeout: TIMEOUT_MS });

    // Sparkline: one stick per day in the 90-day window, never collapsed.
    await expect(page.getByTestId("activity-sparkline")).toBeVisible({
      timeout: TIMEOUT_MS,
    });
    await expect(page.getByTestId("sparkline-stick")).toHaveCount(90);

    // Core dates block + at least one row.
    await expect(page.getByTestId("core-dates-block")).toBeVisible();
    await expect(page.getByTestId("core-date-row-has-birthday")).toBeVisible();

    // Latest interactions block + the email touch snippet.
    await expect(page.getByTestId("latest-interactions-block")).toBeVisible();
    await expect(
      page.getByText("Latest email touch on the waterfront").first(),
    ).toBeVisible();
  });

  test("delta banner appears after facts change between visits; delta rows highlight", async ({
    page,
  }) => {
    // marked_at set (prior visit) + changed items → banner + delta-fact-row.
    await installDetailStubs(page, {
      deltaItems: DELTA_ITEMS,
      deltaMarkedAt: "2026-05-01T00:00:00Z",
    });
    await page.goto(`/entities/${ENTITY_ID}`, { timeout: TIMEOUT_MS });

    await expect(
      page.getByRole("heading", { name: "Alice V3" }).first(),
    ).toBeVisible({ timeout: TIMEOUT_MS });

    // Deterministic "N new facts since <date>" banner.
    await expect(page.getByTestId("delta-banner")).toBeVisible({
      timeout: TIMEOUT_MS,
    });
    await expect(page.getByTestId("delta-banner")).toContainText("2");

    // The changed fact rows carry the delta highlight testid.
    await expect(page.getByTestId("delta-fact-row").first()).toBeVisible();
    await expect(
      page.getByTestId("delta-fact-row").first(),
    ).toHaveAttribute("data-delta", "true");
  });

  test("first visit shows no delta banner (mark is created, nothing to diff)", async ({
    page,
  }) => {
    // marked_at null → first visit → no banner even with items present.
    await installDetailStubs(page, {
      deltaItems: DELTA_ITEMS,
      deltaMarkedAt: null,
    });
    await page.goto(`/entities/${ENTITY_ID}`, { timeout: TIMEOUT_MS });

    await expect(
      page.getByRole("heading", { name: "Alice V3" }).first(),
    ).toBeVisible({ timeout: TIMEOUT_MS });
    await expect(page.getByTestId("delta-banner")).toHaveCount(0);
  });
});

// ===========================================================================
// Suite 3: Workbench — three rails, sortable provenance grid, duplicate panel
// ===========================================================================

test.describe("entity-v3: workbench mode", () => {
  test("toggle renders three rails; provenance grid sortable; duplicate panel → compare", async ({
    page,
  }) => {
    await installDetailStubs(page, { deltaItems: DELTA_ITEMS });
    // Land directly in workbench via the URL param (the toggle persists this).
    await page.goto(`/entities/${ENTITY_ID}?mode=workbench`, {
      timeout: TIMEOUT_MS,
    });

    await expect(
      page.getByRole("heading", { name: "Alice V3" }).first(),
    ).toBeVisible({ timeout: TIMEOUT_MS });

    // The toggle is the workbench mode switch and reports workbench is active.
    const toggle = page.getByTestId("entity-mode-toggle");
    await expect(toggle).toHaveAttribute("aria-checked", "true");

    // Three rails render.
    await expect(page.getByTestId("workbench-three-rail")).toBeVisible({
      timeout: TIMEOUT_MS,
    });
    await expect(page.getByTestId("workbench-context-rail")).toBeVisible();
    await expect(page.getByTestId("workbench-kpi-strip")).toBeVisible();
    await expect(page.getByTestId("workbench-action-rail")).toBeVisible();

    // Provenance grid is present and sortable: clicking a sort header flips its
    // aria-sort. The header is the "Predicate" sort button (default direction).
    // bu-86c4c.16: aria-sort moved from the button to the <th> (columnheader
    // role) — it isn't a valid ARIA attribute on role=button and only applies
    // to columnheader/rowheader — so the assertion targets the columnheader,
    // while the click still goes through the sort button inside it.
    const grid = page.getByTestId("provenance-grid");
    await expect(grid).toBeVisible();
    const sortHeader = grid.getByRole("button", { name: /Predicate/ });
    const sortColumnHeader = grid.getByRole("columnheader", { name: /Predicate/ });
    await expect(sortColumnHeader).toHaveAttribute("aria-sort", "none");
    await sortHeader.click();
    await expect(sortColumnHeader).toHaveAttribute("aria-sort", "ascending");
    await sortHeader.click();
    await expect(sortColumnHeader).toHaveAttribute("aria-sort", "descending");

    // Duplicate panel (amber) sits atop the action rail; commit opens compare.
    await expect(page.getByTestId("workbench-duplicate-panel")).toBeVisible();
    await page.getByTestId("workbench-duplicate-commit").click();
    await expect(page.getByTestId("merge-compare-dialog")).toBeVisible({
      timeout: TIMEOUT_MS,
    });
    await expect(page.getByTestId("compare-column-A")).toBeVisible();
  });

  test("mode toggle switches editorial → workbench in place", async ({
    page,
  }) => {
    await installDetailStubs(page);
    await page.goto(`/entities/${ENTITY_ID}`, { timeout: TIMEOUT_MS });

    const toggle = page.getByTestId("entity-mode-toggle");
    await expect(toggle).toBeVisible({ timeout: TIMEOUT_MS });
    // Editorial by default → no three-rail layout.
    await expect(toggle).toHaveAttribute("aria-checked", "false");
    await expect(page.getByTestId("workbench-three-rail")).toHaveCount(0);

    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-checked", "true");
    await expect(page.getByTestId("workbench-three-rail")).toBeVisible({
      timeout: TIMEOUT_MS,
    });
  });
});

// ===========================================================================
// Suite 4: Finder — Cmd-K, preview, Tab-to-hop, empty-query owner-pinned
// ===========================================================================

test.describe("entity-v3: Cmd-K finder", () => {
  /** Stubs shared by the finder tests. */
  async function installFinderStubs(page: Page) {
    // typed query → search results
    await page.route("**/api/relationship/entities/search**", (route) =>
      json(route, searchResponse("ali")),
    );
    // owner setup-status → owner id for the empty-query pinned set
    await page.route("**/api/relationship/owner/setup-status**", (route) =>
      json(route, { entity_id: OWNER_ID, status: "ready" }),
    );
    // neighbours for both the active-row preview and the owner-pinned set
    await page.route(
      `**/api/relationship/entities/${ENTITY_ID}/neighbours**`,
      (route) => json(route, neighboursResponse()),
    );
    await page.route(
      `**/api/relationship/entities/${OWNER_ID}/neighbours**`,
      (route) =>
        json(route, {
          neighbours: {
            knows: [
              {
                entity_id: ENTITY_ID,
                canonical_name: "Alice V3",
                entity_type: "person",
                weight: 9,
              },
            ],
          },
          remainders: { knows: 0 },
        }),
    );
    // the home page renders behind the finder; stub its summary feeds loosely
    await page.route("**/api/relationship/entities?**", (route) =>
      json(route, entityList()),
    );
  }

  test("Cmd-K opens finder; typing populates the preview pane", async ({
    page,
  }) => {
    await installFinderStubs(page);
    await page.goto("/", { timeout: TIMEOUT_MS });

    // Cmd-K / Ctrl-K opens the entity finder (global keydown handler).
    await page.keyboard.press("ControlOrMeta+k");
    const input = page.getByTestId("entity-finder-input");
    await expect(input).toBeVisible({ timeout: TIMEOUT_MS });

    await input.fill("ali");
    // The active result drives the preview pane (name/mark/type + top relations).
    // The misleading neutral-default gloss was intentionally removed (#2385) —
    // the authoritative gloss lives on the entity detail page, not the finder.
    await expect(page.getByTestId("entity-finder-preview")).toBeVisible({
      timeout: TIMEOUT_MS,
    });
    await expect(
      page.getByTestId("entity-finder-preview-relation").first(),
    ).toBeVisible({ timeout: TIMEOUT_MS });
  });

  test("shared Dialog traps and restores focus and dismisses by Escape or outside pointer", async ({
    page,
  }) => {
    await installFinderStubs(page);
    // Keep the active result null after typing so Tab exercises the Dialog's
    // focus scope instead of EntityFinder's intentional entity Tab-to-hop.
    await page.route("**/api/relationship/entities/search**", (route) =>
      json(route, { results: [], total: 0, q: "contacts", limit: 8 }),
    );
    await page.goto("/", { timeout: TIMEOUT_MS });

    const trigger = page.getByRole("button", { name: "Open command menu" });
    const initialH1Count = await page.locator("h1").count();
    expect(initialH1Count).toBeLessThanOrEqual(1);

    await trigger.click();
    const dialog = page.getByRole("dialog", { name: "Command menu" });
    const input = page.getByTestId("entity-finder-input");
    await expect(dialog).toHaveCount(1);
    await expect(dialog).toHaveAttribute("data-slot", "dialog-content");
    await expect(input).toBeFocused();
    await expect(dialog).toHaveAccessibleDescription(
      "Search entities, pages, butlers, and actions, then choose a result.",
    );
    await expect(dialog.getByRole("dialog")).toHaveCount(0);
    await expect(page.locator("h1")).toHaveCount(initialH1Count);

    const dialogIdsAreUnique = await dialog.evaluate((element) => {
      const ids = [...element.querySelectorAll("[id]")].map((child) => child.id);
      return (
        new Set(ids).size === ids.length &&
        ids.every(
          (id) => document.querySelectorAll(`[id="${CSS.escape(id)}"]`).length === 1,
        )
      );
    });
    expect(dialogIdsAreUnique).toBe(true);

    const stacking = await page.evaluate(() => {
      const overlay = document.querySelector<HTMLElement>('[data-slot="dialog-overlay"]');
      const content = document.querySelector<HTMLElement>('[data-slot="dialog-content"]');
      return {
        overlayZ: Number.parseInt(getComputedStyle(overlay!).zIndex, 10),
        contentZ: Number.parseInt(getComputedStyle(content!).zIndex, 10),
        overlayBeforeContent: Boolean(
          overlay!.compareDocumentPosition(content!) & Node.DOCUMENT_POSITION_FOLLOWING,
        ),
      };
    });
    expect(stacking.contentZ).toBeGreaterThanOrEqual(stacking.overlayZ);
    expect(stacking.overlayBeforeContent).toBe(true);

    await input.fill("contacts");
    await expect(page.getByTestId("entity-finder-page-item").first()).toBeVisible({
      timeout: TIMEOUT_MS,
    });
    await input.press("Tab");
    await expect(input).toBeFocused();
    await input.press("Shift+Tab");
    await expect(input).toBeFocused();

    await input.press("Escape");
    await expect(dialog).not.toBeVisible();
    await expect(trigger).toBeFocused();

    // A keyboard opener produces a visible 2px focus indicator on the initial
    // command input and restores focus to the keyboard's prior target.
    await page.keyboard.press("ControlOrMeta+k");
    await expect(input).toBeFocused();
    const searchFieldRing = await input.evaluate(
      (element) => getComputedStyle(element.parentElement!).boxShadow,
    );
    expect(searchFieldRing).not.toBe("none");
    await input.press("Escape");
    await expect(trigger).toBeFocused();

    // A non-header component can dispatch the shared custom event and still
    // receive focus back when the dialog closes.
    await page.evaluate(() => {
      const opener = document.createElement("button");
      opener.id = "entity-finder-review-opener";
      opener.textContent = "Review opener";
      document.body.append(opener);
      opener.focus();
      window.dispatchEvent(new CustomEvent("open-entity-finder"));
    });
    await expect(input).toBeFocused();
    await input.press("Escape");
    await expect(page.locator("#entity-finder-review-opener")).toBeFocused();

    await trigger.click();
    await expect(input).toBeFocused();
    await page.mouse.click(4, 4);
    await expect(dialog).not.toBeVisible();
    await expect(trigger).toBeFocused();
    await expect(page.locator("h1")).toHaveCount(initialH1Count);

    // Repeating the global shortcut while the already-open dialog owns focus
    // must not replace the original opener with an element inside the portal.
    // Closing still returns focus to the header trigger.
    await trigger.click();
    await expect(input).toBeFocused();
    await page.keyboard.press("ControlOrMeta+k");
    await input.press("Escape");
    await expect(trigger).toBeFocused();
  });

  test("shared Dialog fits a 320px mobile viewport and honors reduced motion", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 320, height: 568 });
    await page.emulateMedia({ reducedMotion: "reduce" });
    await installFinderStubs(page);
    await page.goto("/", { timeout: TIMEOUT_MS });

    await page.getByRole("button", { name: "Open command menu" }).click();
    const dialog = page.getByRole("dialog", { name: "Command menu" });
    await expect(dialog).toBeVisible();
    await expect(page.getByTestId("entity-finder-input")).toBeFocused();

    const geometry = await page.evaluate(() => {
      const rect = (selector: string) => {
        const bounds = document.querySelector<HTMLElement>(selector)!.getBoundingClientRect();
        return {
          top: bounds.top,
          right: bounds.right,
          bottom: bounds.bottom,
          left: bounds.left,
        };
      };
      const durationMs = (value: string) =>
        Math.max(
          ...value.split(",").map((part) => {
            const duration = part.trim();
            return duration.endsWith("ms")
              ? Number.parseFloat(duration)
              : Number.parseFloat(duration) * 1000;
          }),
        );
      const overlay = document.querySelector<HTMLElement>('[data-slot="dialog-overlay"]')!;
      const content = document.querySelector<HTMLElement>('[data-slot="dialog-content"]')!;
      return {
        overlay: rect('[data-slot="dialog-overlay"]'),
        content: rect('[data-slot="dialog-content"]'),
        bodyScrollWidth: document.body.scrollWidth,
        overlayAnimationMs: durationMs(getComputedStyle(overlay).animationDuration),
        overlayTransitionMs: durationMs(getComputedStyle(overlay).transitionDuration),
        contentAnimationMs: durationMs(getComputedStyle(content).animationDuration),
        contentTransitionMs: durationMs(getComputedStyle(content).transitionDuration),
      };
    });

    expect(geometry.overlay).toEqual({ top: 0, right: 320, bottom: 568, left: 0 });
    expect(geometry.content.left).toBeGreaterThanOrEqual(0);
    expect(geometry.content.right).toBeLessThanOrEqual(320);
    expect(geometry.content.top).toBeGreaterThanOrEqual(0);
    expect(geometry.content.bottom).toBeLessThanOrEqual(568);
    expect(geometry.bodyScrollWidth).toBeLessThanOrEqual(320);
    expect(geometry.overlayAnimationMs).toBeLessThanOrEqual(0.01);
    expect(geometry.overlayTransitionMs).toBeLessThanOrEqual(0.01);
    expect(geometry.contentAnimationMs).toBeLessThanOrEqual(0.01);
    expect(geometry.contentTransitionMs).toBeLessThanOrEqual(0.01);
  });

  test("Tab on the active result lands on /entities?center=<id>", async ({
    page,
  }) => {
    await installFinderStubs(page);
    // The hop destination must render after navigation.
    await page.route(
      `**/api/relationship/entities/${ENTITY_ID}**`,
      (route) => json(route, entityDetail()),
    );
    await page.goto("/", { timeout: TIMEOUT_MS });

    await page.keyboard.press("ControlOrMeta+k");
    const input = page.getByTestId("entity-finder-input");
    await expect(input).toBeVisible({ timeout: TIMEOUT_MS });
    await input.fill("ali");
    await expect(
      page.getByTestId("entity-finder-entity-item").first(),
    ).toBeVisible({ timeout: TIMEOUT_MS });

    await input.press("Tab");
    await page.waitForURL(/\/entities\?center=/, { timeout: TIMEOUT_MS });
    expect(page.url()).toContain(`center=${ENTITY_ID}`);
  });

  test("empty-query finder shows the owner-pinned set", async ({ page }) => {
    await installFinderStubs(page);
    await page.goto("/", { timeout: TIMEOUT_MS });

    await page.keyboard.press("ControlOrMeta+k");
    await expect(page.getByTestId("entity-finder-input")).toBeVisible({
      timeout: TIMEOUT_MS,
    });
    // No typing → owner-pinned group renders the inner circle.
    await expect(page.getByTestId("entity-finder-pinned-group")).toBeVisible({
      timeout: TIMEOUT_MS,
    });
    await expect(
      page.getByTestId("entity-finder-pinned-item").first(),
    ).toBeVisible();
  });
});

// ===========================================================================
// Suite 5: Closeout gaps (PR #2239) — concentration provenance, index cursor
// ===========================================================================

test.describe("entity-v3: closeout gaps (PR #2239)", () => {
  test("concentration rows carry src/verified provenance marks", async ({
    page,
  }) => {
    await page.route(
      "**/api/relationship/entities/concentration**",
      (route) => json(route, concentrationResponse()),
    );
    await page.goto("/entities/concentration", { timeout: TIMEOUT_MS });

    await expect(page.getByTestId("concentration-panel")).toBeVisible({
      timeout: TIMEOUT_MS,
    });
    // Each row carries its src + verified marks (spec closeout gap).
    await expect(
      page.getByTestId("concentration-provenance").first(),
    ).toBeVisible({ timeout: TIMEOUT_MS });
  });

  test("index list keyboard cursor moves with ArrowDown when focused", async ({
    page,
  }) => {
    await page.route("**/api/relationship/entities/queue**", (route) =>
      json(route, emptyQueue()),
    );
    await page.route("**/api/relationship/entities/search**", (route) =>
      json(route, { results: [], total: 0, q: "", limit: 8 }),
    );
    await page.route("**/api/relationship/entities?**", (route) =>
      json(route, entityList()),
    );
    await page.goto("/entities/index", { timeout: TIMEOUT_MS });

    // The table renders ≥2 rows.
    await expect(page.getByTestId("entity-table")).toBeVisible({
      timeout: TIMEOUT_MS,
    });
    await expect(page.locator("tbody tr")).toHaveCount(2);

    // No cursor until the focused list container receives keyboard input.
    const container = page.getByTestId("entity-list-container");
    await container.focus();
    await page.keyboard.press("ArrowDown");

    // The cursor lands on the first row (tr[data-cursor='true']).
    await expect(page.locator("tr[data-cursor='true']")).toHaveCount(1, {
      timeout: TIMEOUT_MS,
    });
  });
});
