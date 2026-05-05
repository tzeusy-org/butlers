## ADDED Requirements

### Requirement: ConnectorDetailPage conforms to the detail-page archetype

The `ConnectorDetailPage` at `/ingestion/connectors/:connectorType/:endpointIdentity` SHALL conform to the detail-page archetype defined in the `detail-page-archetype` spec. The page MUST use the `<DetailPage>` shell (from `@/components/layout/DetailPage`) which wraps `<Page archetype="detail">` and enforces the six-tier body-slot contract.

**This requirement is a companion to `§Requirement: Dashboard Connector Page`.**
That requirement specifies what data to display; this requirement specifies how the
page shell, slot layout, and chrome must be structured.

**Relationship to implementation:** `frontend/src/pages/ConnectorDetailPage.tsx`
already uses `<DetailPage>` with the correct slot wiring (PR #1397). This requirement
closes the spec-to-implementation gap; no frontend changes are required.

#### Slot mapping

The six archetype tiers map to ConnectorDetailPage as follows:

| Tier | Slot | ConnectorDetailPage content |
|---|---|---|
| 1 | Header-hero (shell-owned) | `record.title` = `connector_type` (titleized); `record.subtitle` = `endpoint_identity`; breadcrumbs = Ingestion → Connectors → {connector_type} |
| 2 | Pulse (optional) | Ingest health strip: liveness badge, last heartbeat age, today's ingestion count. Currently `null`; a future `<PulseStrip>` implementation is the target. |
| 3 | Primary (required) | Events feed surface: Status card (liveness, uptime, last seen, first seen, registered via), Lifetime Counters card, Discretion Settings card, Batch Settings card (conditional), Period Summary card, Volume Trend chart, Connector-scoped Ingestion Rules section. |
| 4 | Supporting (omitted) | Not applicable. ConnectorDetailPage has no natural two-column supporting panels. |
| 5 | Auxiliary (optional) | Checkpoint Cursor card — a dangerous operator action (resets replay position on next restart). Rendered below the primary stack. Omitted when connector data is not loaded. |
| 6 | Practical drawer (reserved) | Reset / delete actions (destructive operator controls). Currently not implemented. MUST use `<PracticalDrawer>` when added. |

#### Shell adoption

1. **`<Page archetype="detail">` adoption via `<DetailPage>`.**
   The page MUST use `<DetailPage record={...} breadcrumbs={...} loading={...} error={...} pulse={...} primary={...} auxiliary={...} practical={...} />`.
   The `<DetailPage>` wrapper forwards all props to `<Page archetype="detail">` and
   enforces the slot layout. Direct use of `<Page archetype="detail">` without the
   `<DetailPage>` wrapper is also acceptable if the slot contract is preserved.

2. **Title — record-identity.** The `record.title` prop MUST be the connector's own
   `connector_type` field (e.g., `"gmail"`, `"telegram_bot"`). It MUST NOT be the
   generic string `"Connector"`. When the connector record is not yet loaded, the
   `connectorType` URL parameter MAY be used as a fallback.

3. **Subtitle.** The `record.subtitle` prop MUST be the connector's `endpoint_identity`
   (e.g., `"gmail:user:alice@gmail.com"`). This uniquely identifies the connector
   instance below the H1.

4. **Breadcrumbs.** The `breadcrumbs` prop MUST be:
   `[{ label: "Ingestion", href: "/ingestion" }, { label: "Connectors", href: "/ingestion?tab=connectors" }, { label: connector_type }]`

5. **Loading state.** The `loading` prop MUST be set to `true` while the connector
   detail API call is in flight. The `<Page>` shell MUST show `DetailSkeleton`. No
   inline skeleton blocks MUST be rendered at the page layer.

6. **Error state.** The `error` prop MUST carry any fetch error. The `<Page>` shell
   MUST render the destructive error card. No inline destructive-text block MUST be
   rendered at the page layer.

7. **Primary slot (required).** The full operator read surface — status card, counters,
   discretion settings, optional batch settings, period summary, volume trend chart,
   and connector-scoped ingestion rules — MUST be rendered inside the `primary` slot.
   These sections are rendered in a vertical stack (full width).

8. **Auxiliary slot (optional).** The Checkpoint Cursor card (inline cursor editing with
   confirmation dialog) MUST be rendered inside the `auxiliary` slot. The auxiliary slot
   MUST be `null` when the connector record has not yet loaded.

9. **Practical slot (reserved).** Reset and delete actions MUST NOT be placed inside the
   `primary` or `auxiliary` slots when implemented. They MUST be placed inside the
   `practical` slot using `<PracticalDrawer>`, collapsed by default. The drawer label
   MUST clearly communicate that it contains destructive operations.

#### Scenario: ConnectorDetailPage uses shell loading state

- **WHEN** `GET /api/connectors/:connectorType/:endpointIdentity` is in flight
- **THEN** the `<Page>` shell MUST show `DetailSkeleton` (card + two block skeletons)
- **AND** the page MUST NOT render inline `<Skeleton>` blocks outside the shell
- **AND** breadcrumbs MUST remain visible during the loading state

#### Scenario: ConnectorDetailPage uses shell error state

- **WHEN** the connector detail fetch fails (e.g., 404 connector not found)
- **THEN** the `error` prop on `<DetailPage>` (forwarded to `<Page>`) MUST be set
- **AND** the shell MUST render the destructive error card
- **AND** no inline destructive-text block MUST be rendered at the page layer

#### Scenario: ConnectorDetailPage title shows connector type

- **WHEN** the connector has `connector_type = "gmail"`
- **THEN** the `<h1>` rendered by the shell MUST read "gmail" (or titleized: "Gmail")
- **AND** it MUST NOT read "Connector" or "Connector Detail"

#### Scenario: Endpoint identity shown as subtitle

- **WHEN** the connector has `endpoint_identity = "gmail:user:alice@gmail.com"`
- **THEN** the subtitle line below the H1 MUST read "gmail:user:alice@gmail.com"

#### Scenario: Checkpoint cursor in auxiliary slot

- **WHEN** the connector detail page renders a resolved connector record
- **THEN** the Checkpoint Cursor card (with inline cursor editing) MUST appear below
  the primary stack in the auxiliary section
- **AND** it MUST NOT appear inside the primary card body

#### Scenario: Auxiliary slot is null when connector is not loaded

- **WHEN** the connector detail API call is in flight and `connector` is undefined
- **THEN** the `auxiliary` prop on `<DetailPage>` MUST be `null`
- **AND** no checkpoint cursor card MUST be rendered

#### Scenario: Destructive actions use practical drawer

- **WHEN** reset or delete connector actions are added to ConnectorDetailPage
- **THEN** they MUST be placed inside the `practical` slot using `<PracticalDrawer>`
- **AND** the drawer MUST be collapsed by default
- **AND** the drawer MUST NOT place destructive actions inside the `primary` or
  `auxiliary` slots

## Source References

- `detail-page-archetype` spec — archetype conformance contract
- `openspec/changes/detail-page-archetype/design.md` §D4 — ConnectorDetailPage scope note
- `openspec/changes/detail-page-archetype/tasks.md` §5.1 — deferred open item resolved by this change
- `frontend/src/pages/ConnectorDetailPage.tsx` — implementation (already conformant)
- `frontend/src/components/layout/DetailPage.tsx` — `<DetailPage>` shell wrapper
- `about/lay-and-land/detail-page-audit.md` §1.7 — ConnectorDetailPage audit score (17/25)
- Non-Negotiable Rule 2 (The Page is a primitive — `about/heart-and-soul/design-language.md`)
