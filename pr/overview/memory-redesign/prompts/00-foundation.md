# 00 · Foundation

> Phase A. End state: routes, URL state, data hooks, and the API delta
> list are settled. No visual work yet. Read `VISION.md` and
> `MEMORY_LANGUAGE.md` first.

## Routes

| Route | Page | Notes |
|---|---|---|
| `/memory` | MemoryPage (rebuilt) | registers + rail + housekeeping |
| `/memory/facts/:factId` | FactDetailPage (reshaped) | editorial page shape (06) |
| `/memory/rules/:ruleId` | RuleDetailPage (reshaped) | editorial page shape (06) |
| `/memory/episodes/:episodeId` | EpisodeDetailPage (reshaped) | editorial page shape (06) |

All four routes exist today (`frontend/src/router-config.tsx:104` and
siblings). No new routes; no removed routes.

## URL state on `/memory`

| Param | Values | Default |
|---|---|---|
| `register` | `facts` \| `rules` \| `episodes` | `facts` |
| `q` | search string (submitted, not keystroke) | absent |
| `kind` | search scope when `q` set: `all` \| `fact` \| `rule` \| `episode` | `all` |
| `validity` | ledger filter: `active` \| `fading` \| `superseded` \| `expired` \| `retracted` | `active` |
| `offset` | pagination offset for the focused register | `0` |

Back button must move between register/filter states. The search text
*input* is local state; pressing Enter writes `q` to the URL.

## Existing API surface (evidence: live endpoints)

All under `/api/memory` (`src/butlers/api/routers/memory.py`):

| Endpoint | Used by | Notes |
|---|---|---|
| `GET /stats` | overture KPI + pipeline band | see delta below |
| `GET /facts?q&scope&validity&permanence&offset&limit` | ledger | `Fact` carries `confidence`, `decay_rate`, `permanence`, `validity`, `last_confirmed_at`, `entity_id`, `source_episode_id` |
| `GET /rules?q&scope&maturity&offset&limit` | standing orders | maturity vocabulary: `candidate` / `established` / `proven` / `anti_pattern` |
| `GET /episodes?butler&consolidated&since&until&offset&limit` | daybook | day-group client-side on `created_at` |
| `GET /activity?limit` | rail (recent activity) | |
| `GET /inspect?q&kind&offset&limit` | unified search | replaces both old search affordances |
| `GET/PUT /retention-policies` | housekeeping | PUT preserved as-is |
| `GET /compaction-log?limit` | housekeeping | |
| `GET /reembed/pending`, `POST /reembed` | housekeeping + rail (drift row) | |
| `GET /facts/:id`, `/rules/:id`, `/episodes/:id` | detail pages | |

## API deltas (backend epic — do not fake client-side)

| Delta | Why | Shape |
|---|---|---|
| Extend `GET /stats` with `last_consolidation_at`, `last_consolidation_facts_produced`, `dead_letter_episodes` | pipeline band + Voice line + rail rows are unbuildable without them | three nullable fields on `MemoryStats` |
| `POST /facts/:id/confirm` | the one commit action on the fact page (re-ink) | mirrors MCP `memory_confirm` |
| `POST /facts/:id/retract` | correction affordance on the fact page | mirrors MCP `memory_forget` for facts |

If a delta is descoped, the dependent affordance ships hidden — never a
dead button. Everything else on the page is buildable against live
endpoints today.

## Derived values (client-side, pure functions)

- `effectiveConfidence(fact) = confidence * exp(-decay_rate * daysSince(last_confirmed_at ?? created_at))` — display value in the
  belief column and the detail-page arithmetic line. **Dimming does not
  use this**: dim when server `validity === 'fading'` (server is the
  source of truth for thresholds).
- `permanenceTag`: `permanent→pm`, `stable→st`, `standard→sd`,
  `volatile→vo`, `ephemeral→ep`.
- `consolidationGlyph(episode)`: `pending→'◦'`, `consolidated→'•'`,
  `dead_letter/failed→'✕'`.

## Hooks

Reuse `frontend/src/hooks/use-memory.ts` wholesale (`useMemoryStats`,
`useFacts`, `useRules`, `useEpisodes`, `useMemoryActivity`,
`useMemoryInspect`, `useMemoryRetentionPolicies`,
`useMemoryCompactionLog`, re-embed hooks). New code is presentation;
hooks change only where the stats delta adds fields.

## Acceptance for this phase

- [ ] URL params round-trip: deep-link `/memory?register=rules` opens
      standing orders; back button returns to the previous register.
- [ ] Stats delta speced in the backend epic with the three field names
      above.
- [ ] `effectiveConfidence` unit-tested (zero decay, fresh confirm,
      old unconfirmed).
- [ ] No new client-side fabrication of pipeline state (no guessing
      last-run from activity rows).
