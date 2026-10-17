## Why

The owner chose private deterministic custody capture/display on 2026-09-27. PR4211 head 9c1814c75564e53fd6ebc9ffbe288e3aabb2964c promises both model-backed locate results and no provider disclosure of those fields. This local successor resolves that contradiction. It is not yet adopted and does not modify the PR.

## What Changes

- Replace proposed model custody record/locate tools with authenticated private owner capture, confirmation, locate and export routes and a private dashboard screen.
- Keep the existing collection item UUID, opt-in profile, episode semantics, source qualification, revision/CAS, replay and retained history. Add a minimal General migration for a sticky private-collection flag and ordinary-only name uniqueness; private/ordinary collections may share labels, selected by UUID.
- Keep all record identity and values out of model outputs. Conversation supplies only the fixed generic private-screen link and content-blind capability availability.
- Enforce exposure fences on generic reads/search/export/catalog/prompt retrieval and generic writes, atomically with enrollment. The entire parent collection and all siblings become private, explicitly disclosed before private confirmation; no record is deleted. Fence the browser page-context/floating-chat bridge so private screens never attach snapshots.
- Replace proposed Switchboard owner-report confirmation with General-local server-held private capture/confirmation under central owner authentication. No private report needs an LLM, external ingress or another butler.
- Defer optional specialist enrichment entirely from this first private slice; unresolved labels stay unverified. A future linkage contract requires separate review.

## Capabilities

### New Capabilities
- `personal-item-custody`: private source-qualified reports, confirmed transitions, replay-safe history and model-exclusion boundaries.

### Modified Capabilities
The paired proposed General manifesto/routing amendment changes the user promise and consumer. Canonical General inventory, dashboard surface and routing documentation must be reconciled with owner-held bu-2jtfw.9 before integration; this local derivative does not edit those canonical files.

## Non-Goals

No conversational disclosure of selected custody facts; no model custody tools; no new provider integration; no finance/identity enrichment, notifications, reminders, physical operations, universal privacy framework, asset table or new schema. No retraction of historical disclosures or promise to erase previously known ordinary names. No adoption of generic-state PR4219: the owner's separate B risk acceptance remains unchanged. No live data or deployment.

## Authority and Impact

The owner selected direction A only, not these exact successor bytes. General owns private custody records and deterministic operations; existing owner middleware and server-derived principal authorize private routes. A new private dashboard surface is necessary and explicitly replaces the former no-new-page non-goal. Shared item/query/export boundaries must be reconciled with bu-2jtfw.9 without takeover. All five derivative files require independent review and exact owner adoption before canonical source integration or implementation allocation. Tests: +0 ~0 -0 (spec preparation only).
