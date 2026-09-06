## 0. Exact contract approval gate

- [ ] 0.1 Obtain an independent exact-head semantic review confirming that the
      change preserves the unified ActivityTimeline, the MCP-only Chronicler
      boundary, the commitment lifecycle contract, and every stated non-goal.
- [ ] 0.2 Obtain owner approval that names the exact reviewed commit or
      artifact digest. Draft publication, CI, or reviewer GO does not satisfy
      this gate.
- [ ] 0.3 Wait for foreign-owned `bu-2jtfw.12` to become terminal, update the
      implementation branch from its landed tree, and re-read
      `roster/relationship/api/router.py` plus the relationship endpoint tests
      before assigning one implementation owner.

## 1. Behavior-executing tests after approval

- [ ] 1.1 Extend the Meeting Prep component test to prove a resolved attendee
      links to the URI-encoded canonical entity path with the complete current
      query preserved, while a missing or blank legacy ID remains plain text.
- [ ] 1.2 Extend the Relationship activity API tests to prove
      the complete source matrix: `all` executes both sources;
      `relationship` skips Chronicler and cannot inherit its degradation;
      `chronicler` skips the Relationship activity query; invalid source
      returns 422 before either fetch; selected-source filtering precedes
      total, bins, deterministic timestamp/ID ordering, and pagination; and
      `bins_only` uses the selected set. Cover valid empty, total failure, and
      mixed readable/malformed Chronicler evidence, retaining valid rows while
      setting the fixed degraded reason. Preserve the default unfiltered
      response plus the owner/404 gates.
- [ ] 1.3 Add one parameterized organizer/participant integration test (or one
      case with two rows) that seeds both roles in the real Chronicler
      `episode_entities` table, resolves both through the real
      `chronicler_list_episodes(participant_entity_id=...)` tool, and consumes
      both results through the Relationship `source=chronicler` activity route.
      Neither row may rely on the owner role. Do not count a pre-shaped
      Relationship MCP mock as this evidence.
- [ ] 1.4 Extend entity-detail tests to prove the five-row Shared Chronicles
      bound, row date/title/episode doors, successful empty copy, partial and
      total degraded states, retry isolation, and continued rendering of the
      unified ActivityTimeline and other entity sections.
- [ ] 1.5 Add relationship commitments endpoint tests against the real query
      seam for both directions, every originating source, open and aging rows,
      exclusion of resolved/self/wrong-entity/non-commitment rows, the exact
      projection, stable ordering, pagination validation, owner gate, unknown
      entity, honest empty, and source-unavailable response.
- [ ] 1.6 Add Chronicles route tests proving `date` plus `episode` opens the
      existing drawer, close removes only `episode`, invalid episode failure
      stays inside the drawer, and date-only links remain unchanged.

## 2. API implementation after approval and serialization

- [ ] 2.1 Add the optional activity `source` filter, apply it before pagination,
      and preserve the current response when it is omitted.
- [ ] 2.2 Add the minimal commitments response model and read-only endpoint,
      reuse the existing owner and entity gates, query only
      `public.owner_conditions` through the core commitment seam, and return
      fixed content-blind degradation metadata on source failure.
- [ ] 2.3 Keep Relationship's Chronicler contribution MCP-only and retain the
      existing no-direct-`chronicler.*` boundary guard.

## 3. Frontend implementation after approval

- [ ] 3.1 Turn eligible Meeting Prep attendee names into canonical entity links
      while preserving the complete current query.
- [ ] 3.2 Add the bounded Shared Chronicles and Commitments sections to entity
      detail, reuse the established commitment-row vocabulary, and keep empty,
      partial, and unavailable states distinct.
- [ ] 3.3 Add the optional Chronicles `episode` query state around the existing
      EpisodeDrawer without adding a new detail route or component.

## 4. Documentation and verification after implementation

- [ ] 4.1 Update the Relationship API and frontend contract documentation with
      the source-filter, commitments response, summary coexistence, and episode
      door semantics.
- [ ] 4.2 Run the focused API and frontend behavior suites, the existing
      Relationship Chronicler-boundary tests, frontend lint/knip/build/tests,
      strict OpenSpec validation, spec-overwrite guard, repository guards, and
      one terminal hosted CI run for the exact implementation head.
- [ ] 4.3 Keep `bu-bbwur`, the independent ActivityTimeline `/timeline` versus
      required `/activity` wiring correction, open until behavior-executing
      evidence proves the baseline unified timeline contract. It has no
      dependency on approval of this preview, and the Shared Chronicles preview
      is not completion evidence for it.
