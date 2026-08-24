## 1. Write path in the canonical store

- [x] 1.1 Add `gift_add_for_entity` and `note_create_for_entity` entity-keyed writers with duplicate detection, `scope = 'relationship'`, and `entity_id` set.
- [x] 1.2 Add the `reach_out` tool module writing an inert temporal `reach_out_draft` fact with `status: 'draft'` and no delivery path.
- [x] 1.3 Widen `note_list` and `gift_list` subject matching so contact-keyed reads still see entity-keyed writes.

## 2. API surface

- [x] 2.1 Add request models with blank-input rejection and the reach-out draft response model.
- [x] 2.2 Add the four owner-gated `POST` endpoints plus `GET /entities/{id}/reach-out-drafts`, returning 403 `owner_required`, 404 for unknown entities, 422 for invalid input, and 409 with `existing_id` for duplicates.

## 3. Dashboard surface

- [x] 3.1 Add typed client functions, read hooks, and non-optimistic write hooks that invalidate only what the write actually changed.
- [x] 3.2 Add the `EntityVerbRail` component with per-verb forms, honest pending and error copy, and a permanent "nothing is sent" note on the draft form.
- [x] 3.3 Render the rail on entity detail and the Plex dossier, and add a drafts panel to entity detail.

## 4. Verification

- [x] 4.1 Add red-first API coverage for each verb, including the assertion that drafting issues no MCP call.
- [x] 4.2 Add front-end coverage for payload shape, duplicate and rejection messaging, and the absence of any send affordance.
- [x] 4.3 Run the targeted quality gates and hand off the branch.
