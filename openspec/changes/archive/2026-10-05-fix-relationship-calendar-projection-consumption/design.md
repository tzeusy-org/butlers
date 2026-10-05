## Context

Core creates calendar projection tables unqualified in the migration target schema. Relationship config, pool search_path and per-acquire role select its own schema; the Calendar module writes through that pool. Public calendar fixtures do not prove this topology. Routine provider polling defaults to disabled; internal, explicit force-sync and mutation projection paths remain available. This change proves consumption of local synthetic projected rows only, never deployed population or provider ingestion.

## Decisions

Use explicit `relationship.calendar_events` so a missing own table cannot fall back through public search_path. Preserve existing status/window/attendee/identity/RSVP semantics and result keys. Differentiate successful empty reads from query failures with a local success flag. Missing table increments errors with a warning; other failures retain exception category/logs. Failed calendar reads retain the old shared checkpoint and committed message work; existing dedup and the 30-day cap govern the next normal run.

Fixtures follow actual core -> roster -> enabled-module migration ordering, with real Switchboard inbox migrations and read-only runtime exception. The actual local Calendar writer creates sources/events/instances with normalized JSONB. Control acquisitions perform disposable setup/fault injection, while the writer/job run under actual SET ROLE. Public and inaccessible synthetic health controls use real core migrations. Table/column/SELECT faults keep T−3 checkpoint, T−2 event and T−1 message causally within the retained interval; restoration and explicit bounded test replay assert stable real fact/edge identities.

The existing positive species includes a historical-query causal mutation control: exactly one Step 4 SQL constant changes back to public; remaining bytecode/constants/globals/pool/writers remain bound. Real wrong-contact outputs are positive witnesses. The corrected call must add own outputs while wrong IDs remain unchanged. This is not full historical-function execution or a local behavior red when setup fails.

## Scope and Risks

No provider polling enablement, foreign-schema import, new grants/RLS, schema/bootstrap changes, invitation eligibility rewrite, per-source cursor or new retry workflow. Historic deployed missed intervals remain unknown; there is no automatic rewind beyond 30 days. Calendar metadata does not become an expected-signal liveness attestation.

Only the two changed legacy requirements gain trace metadata. Message/Schedule/Stats stay verbatim and may retain existing trace debt. Named OpenSpec strict/carry-over validation and whole-repository authoring findings are separate: preserve actual findings, assess changed IDs against real canonical source after sync, and do not create a synthetic mirror, extra legacy rewrites or ratchet changes to force global green.

## Completion

Official exact-head CI 37257833164 establishes the real local projection/role/control/fault/recovery cases at `3e815488f31fba553d80704e10ec6f8f58773b5c`: all 103 affected cases pass. The selected owning Relationship roster has 1280 passes and one existing posture-role skip (no credit); all 16 sender-identity SQL cases pass. Local Docker access remains denied, and the full historical function and provider/deployed population remain unproven.

The normal CLI archive applied the approved complete blocks, and the actual canonical source exactly matches the approved full reconstruction: 27 original scenario bodies/names retained, 30 total, with Purpose's bounded table spelling corrected. Changed canonical IDs and real test citations were inspected without a mirror or legacy rewrite. Only the two changed requirements pass the scoped authoring inspection; existing whole-baseline/repository debt remains explicit. Python/SQL/test source stays unchanged from the hosted proof head. Independent review and terminal exact-tree hosted gates on the final pushed head remain required before merge/closure.
