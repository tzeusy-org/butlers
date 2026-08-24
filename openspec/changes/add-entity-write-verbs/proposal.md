## Why

Entity detail and the Plex dossier expose notes, interactions, and gifts as
GET-only surfaces. The three operator verbs the relationship butler advertises
-- log an interaction, capture a gift idea, draft a reach-out -- have no write
path from either surface, so bu-86c4c.15 (PR #2894) deferred all three. The
owner can read a relationship history they cannot add to, and "draft a
reach-out" has no home at all: no predicate, no endpoint, no panel.

The gap must close without inventing a parallel store. Notes, interactions, and
gifts already live in the shared `facts` table under `scope = 'relationship'`,
which is exactly what the five entity-tab GET endpoints read; the writes belong
in the same rows. Reach-out drafts are the one genuinely new shape, and the
whole point of a draft is that it is inert -- drafting must not acquire a send
path by accident.

## What Changes

- Add owner-gated `POST` endpoints for notes, interactions, and gifts on the
  entity tab surface, writing the same `facts` rows the matching GET endpoints
  already read.
- Add `reach_out_draft` as a temporal, append-only relationship fact with a
  `GET`/`POST` pair, carrying `status: 'draft'` and no delivery path of any
  kind.
- Surface all four verbs as one entity verb rail rendered on both entity detail
  and the Plex dossier, with honest pending, duplicate, and rejection states.
- Extend the Clause 12a owner-gate endpoint list to name the four new mutations.

## Capabilities

### Modified Capabilities

- `dashboard-relationship`: Add the entity-keyed write endpoints behind the
  existing owner gate, add the reach-out draft read/write pair, and require the
  operator verb rail on entity detail and Plex.

## Impact

- Affected backend: `roster/relationship/api/router.py`,
  `roster/relationship/api/models.py`, and the `gifts`, `notes`, and new
  `reach_out` tool modules.
- Affected frontend: typed client and hooks, a shared `EntityVerbRail`
  component, the entity detail page, and the Plex dossier.
- No database migration. `facts` already carries every column these writes need
  and `store_fact` auto-registers a novel predicate as `status='proposed'`.
- No external send, no `notify()`, no connector, and no new dependency.
