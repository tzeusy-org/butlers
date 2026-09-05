> **ARCHIVED** — This implementation/design plan is historical. Archived on 2026-09-06.
> **Reason:** WhatsApp canonical-identity + reconciliation shipped via the repair-whatsapp-identity-reconciliation change.
> **Successor:** `openspec/changes/repair-whatsapp-identity-reconciliation`.
>
> The doctrine on archival lives in `openspec/specs/docs-information-architecture/spec.md`.

# WhatsApp Identity Resolution and Reconciliation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make WhatsApp message-time identity reuse canonical people, preserve a deterministic entity
anchor for every decomposed speaker, prevent transport-named fact entities, and provide a guarded
content-blind cleanup command for existing false transitory shells.

**Architecture:** Keep `whatsapp_user_client` as the persisted transport channel but canonicalize it
to `whatsapp_jid` inside the identity boundary. Normalize connector speaker identities before LLM
exposure, resolve each distinct batch speaker once in Switchboard, and join model-selected excerpts
back to authoritative messages by `message_id`. Reconciliation uses a FastAPI-free audited merge
service and applies only exact-digest-authorized, reference-free empty shells.

**Tech Stack:** Python 3.12, asyncio, asyncpg/PostgreSQL, FastAPI router discovery, pytest,
pytest-asyncio, testcontainers, Ruff, OpenSpec 1.9.0.

## Global Constraints

- Work only in `/home/tze/.butlers-worktrees/fix-whatsapp-identity-reconciliation` on branch
  `fix/whatsapp-identity-reconciliation`; keep the repository root on `main`.
- Do not create, update, close, export, or otherwise use a Beads issue for this feature.
- Follow strict red-green-refactor: every production behavior starts with a focused test that fails
  for the intended missing behavior.
- Preserve persisted `source_channel=whatsapp_user_client`; translation is identity-only.
- Never expose names, phone numbers, JIDs, LIDs, raw facts, merge evidence, SQL parameters, or DSNs in
  reconciliation output or exception text.
- Reconciliation is dry-run by default and is never invoked by migrations, startup, daemons,
  connectors, schedules, or deployment scripts.
- Do not add a dependency or database migration.
- Every new mandatory requirement ID is cited by at least one focused test.
- Update OpenSpec tasks as each independently verified outcome completes.

---

### Task 1: Canonical WhatsApp identity channel

**Files:**
- Modify: `src/butlers/identity.py`
- Modify: `roster/relationship/tools/relationship_assert_fact.py`
- Modify: `tests/core/test_whatsapp_identity.py`
- Modify: `tests/core/test_identity.py`
- Modify: `tests/core/test_identity_resolution_entity_facts.py`

**Interfaces:**
- Produces: `canonical_identity_channel_type(channel_type: str) -> str`
- Consumes later: Tasks 3, 4, and 7 use the same canonicalizer; no caller owns a private alias map.

- [ ] **Step 1: Write the failing single and bulk resolver tests**

Add tests with adjacent `Spec: REQ-switchboard-identity-001` citations that call both
`resolve_contact_by_channel()` and `resolve_contacts_by_channel_bulk()` using
`channel_type="whatsapp_user_client"`. Assert the same direct-handle and phone fallback sequence as
`whatsapp_jid`, including ambiguous digit matching returning `None`.

```python
async def test_whatsapp_user_client_uses_jid_phone_fallback():
    """Spec: REQ-switchboard-identity-001."""
    pool = _make_pool_with_rows(None, {"entity_id": _ENTITY_ID, "name": "Bob", "roles": []})
    result = await resolve_contact_by_channel(
        pool, "whatsapp_user_client", "1234567890@s.whatsapp.net"
    )
    assert result is not None
    assert result.entity_id == _ENTITY_ID
    assert pool.fetchrow.call_args_list[1].args[1] == "has-phone"
```

- [ ] **Step 2: Run the identity tests and verify RED**

Run:

```bash
uv run pytest tests/core/test_whatsapp_identity.py tests/core/test_identity.py \
  tests/core/test_identity_resolution_entity_facts.py -q --tb=short
```

Expected: the new transport-alias cases fail because predicate lookup and WhatsApp phone fallback do
not canonicalize `whatsapp_user_client`.

- [ ] **Step 3: Implement one canonical identity-channel function**

Add this interface in `src/butlers/identity.py` and use the canonical value before
`channel_value_for_storage()`, `_CHANNEL_TYPE_TO_PREDICATE` lookup, Telegram/WhatsApp fallback checks,
and `_channel_candidates()`:

```python
_IDENTITY_CHANNEL_ALIASES: dict[str, str] = {
    "whatsapp_user_client": "whatsapp_jid",
}


def canonical_identity_channel_type(channel_type: str) -> str:
    return _IDENTITY_CHANNEL_ALIASES.get(channel_type, channel_type)
```

In `assert_sender_channel_fact()`, canonicalize before predicate selection and storage normalization:

```python
canonical_channel = canonical_identity_channel_type(channel_type)
predicate = _CHANNEL_TYPE_TO_PREDICATE.get(canonical_channel)
stored_value = channel_value_for_storage(canonical_channel, channel_value)
```

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the Step 2 command. Expected: all selected tests pass with no warnings.

- [ ] **Step 5: Commit the identity boundary**

```bash
git add src/butlers/identity.py roster/relationship/tools/relationship_assert_fact.py \
  tests/core/test_whatsapp_identity.py tests/core/test_identity.py \
  tests/core/test_identity_resolution_entity_facts.py
git commit -m "fix(identity): canonicalize WhatsApp transport senders"
```

### Task 2: Normalize connector history before LLM exposure

**Files:**
- Modify: `src/butlers/connectors/whatsapp_user_client.py`
- Modify: `tests/test_passive_interaction_sender_identity.py`
- Modify: `tests/connectors/test_whatsapp_user_client.py`
- Modify: `tests/integration/test_whatsapp_pipeline.py`

**Interfaces:**
- Produces: device-free `sender_identity`, stable neutral `sender`, normalized history and text.
- Consumes: existing `_refresh_lid_map()` and `_split_jid()`.
- Used by: Task 3 batch resolution and Task 4 authoritative excerpt join.

- [ ] **Step 1: Add failing connector projection tests**

Cover mapped LID, unmapped LID, device ordinals, and two distinct unknown group speakers. Cite
`REQ-connector-base-spec-001`.

```python
def test_mapped_lid_is_normalized_in_history_and_text(connector):
    """Spec: REQ-connector-base-spec-001."""
    connector._lid_to_phone["122204922638508"] = "6591111111"
    envelope = connector._build_batch_envelope(
        "group@g.us",
        [_event(sender_jid="122204922638508:7@lid", message_id="m1", text="hello")],
        "batch-1",
    )
    history = envelope["payload"]["raw"]["conversation_history"]
    assert history[0]["sender_identity"] == "6591111111@s.whatsapp.net"
    assert "122204922638508" not in envelope["payload"]["normalized_text"]
    assert history[0]["sender"] == "Unknown WhatsApp sender 1"
```

- [ ] **Step 2: Run connector tests and verify RED**

```bash
uv run pytest tests/test_passive_interaction_sender_identity.py \
  tests/connectors/test_whatsapp_user_client.py \
  tests/integration/test_whatsapp_pipeline.py -q --tb=short
```

Expected: new history/text assertions fail because raw `sender_jid` is copied into both surfaces.

- [ ] **Step 3: Implement structured sender projection**

Keep `_build_batch_envelope()` synchronous. Change `_participant_identity()` to return a device-free
phone JID, mapped phone JID, or device-free opaque LID. Add:

```python
_WHATSAPP_UNKNOWN_SENDER_LABEL = "Unknown WhatsApp sender"


def _project_batch_senders(
    self,
    buffered_events: list[dict[str, Any]],
) -> dict[str, tuple[str, str]]:
    identities: list[str] = []
    for event in buffered_events:
        raw_jid = str(event.get("sender_jid") or event.get("from_jid") or "")
        identity = self._participant_identity(raw_jid)
        if identity and identity not in identities:
            identities.append(identity)
    labels = {
        identity: f"{_WHATSAPP_UNKNOWN_SENDER_LABEL} {index}"
        for index, identity in enumerate(identities, start=1)
    }
    return {
        str(event.get("message_id") or event.get("id")): (
            self._participant_identity(str(event.get("sender_jid") or event.get("from_jid") or ""))
            or "unknown",
            labels.get(
                self._participant_identity(
                    str(event.get("sender_jid") or event.get("from_jid") or "")
                )
                or "",
                _WHATSAPP_UNKNOWN_SENDER_LABEL,
            ),
        )
        for event in buffered_events
    }
```

Use this projection for normalized-text labels and history
`{message_id, sender_identity, sender, text, timestamp, is_new, reply_to}`. Retain raw provider JIDs
only inside `raw.events`. Keep `sender.participants` keyed by normalized identity with neutral labels.

- [ ] **Step 4: Run connector tests and verify GREEN**

Run the Step 2 command. Expected: all selected tests pass.

- [ ] **Step 5: Commit connector normalization**

```bash
git add src/butlers/connectors/whatsapp_user_client.py \
  tests/test_passive_interaction_sender_identity.py \
  tests/connectors/test_whatsapp_user_client.py tests/integration/test_whatsapp_pipeline.py
git commit -m "fix(whatsapp): keep transport identifiers out of speaker labels"
```

### Task 3: Resolve every batch speaker once

**Files:**
- Modify: `src/butlers/switchboard_wiring.py`
- Modify: `roster/switchboard/tools/identity/inject.py`
- Modify: `src/butlers/identity.py`
- Modify: `src/butlers/modules/pipeline.py`
- Modify: `tests/core/test_buffer.py`
- Modify: `roster/switchboard/tests/test_identity_injection.py`
- Modify: `tests/modules/test_module_pipeline.py`

**Interfaces:**
- Produces:

```python
async def resolve_sender_identities(
    pool: asyncpg.Pool,
    channel_type: str,
    channel_values: Sequence[str],
    *,
    notify_owner_fn: Callable[[str], Awaitable[None]] | None = None,
    state_pool: asyncpg.Pool | None = None,
) -> dict[str, IdentityResolutionResult]:
```

- Adds `display_name: str | None` to `IdentityResolutionResult`.
- Adds `raise_on_error: bool = False` to `resolve_contacts_by_channel_bulk()`.
- Produces `MessagePipeline._load_decomp_conversation_messages()` and
  `MessagePipeline._resolve_decomp_speakers()`.

- [ ] **Step 1: Add failing buffer and batch-resolution tests**

Test that `build_buffer_pipeline_inputs()` preserves `source_sender_identities` and
`owner_sender_identity`; that the batch resolver deduplicates stable input order; that known senders
use the bulk result; and that unknown senders use the existing reservation/notification path once.
Cite `REQ-switchboard-identity-002`.

- [ ] **Step 2: Run focused tests and verify RED**

```bash
uv run pytest tests/core/test_buffer.py roster/switchboard/tests/test_identity_injection.py \
  tests/modules/test_module_pipeline.py -q --tb=short
```

Expected: tests fail because the full participant set is lost during buffer reconstruction and the
pipeline loader returns formatted text rather than structured speakers.

- [ ] **Step 3: Preserve all reconstructed batch speakers**

In `build_buffer_pipeline_inputs()`, keep current primary selection and add:

```python
if isinstance(participants, dict) and participants:
    request_context["source_sender_identities"] = sorted(str(key) for key in participants)
if owner_sender_id:
    request_context["owner_sender_identity"] = str(owner_sender_id)
```

- [ ] **Step 4: Add strict bulk-resolution failure signaling**

Extend `resolve_contacts_by_channel_bulk()` with keyword-only
`raise_on_error: bool = False`. On query failure, re-raise when strict and retain current all-`None`
fail-open behavior otherwise. The batch speaker path passes `raise_on_error=True` so a DB outage
cannot mint a wave of false unknown entities.

- [ ] **Step 5: Implement `resolve_sender_identities()`**

Deduplicate channel values in stable order, canonicalize the channel, bulk-resolve known speakers,
construct known `IdentityResolutionResult` values using the existing preamble builder, then call the
existing unknown-sender reservation path only for actual unresolved values. Do not pass the neutral
display label to `create_temp_contact()`; its canonical fallback must remain keyed by the real channel
value.

- [ ] **Step 6: Keep structured messages through pipeline identity work**

Replace the string loader with exact signature
`async def _load_decomp_conversation_messages(self, message_inbox_id: Any | None) ->
list[dict[str, Any]] | None`.

Add exact signature `async def _resolve_decomp_speakers(self, *, source_channel: str, messages:
list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, IdentityResolutionResult]]`.

Enrich each message with canonical/neutral `sender`, structured `sender_identity`, and UUID-string or
null `sender_entity_id`. Assert a normalized channel fact only for successfully reserved unknown
speakers. Use the map entry for `args.source_id` as the top-level preamble/context result and skip the
old second resolution call.

- [ ] **Step 7: Run focused tests and verify GREEN**

Run the Step 2 command. Expected: selected tests pass, and strict bulk failure tests prove no unknown
entity creation on DB failure.

- [ ] **Step 8: Commit per-speaker resolution**

```bash
git add src/butlers/switchboard_wiring.py roster/switchboard/tools/identity/inject.py \
  src/butlers/identity.py src/butlers/modules/pipeline.py tests/core/test_buffer.py \
  roster/switchboard/tests/test_identity_injection.py tests/modules/test_module_pipeline.py
git commit -m "feat(switchboard): resolve buffered speakers deterministically"
```

### Task 4: Join excerpts to authoritative speaker identity

**Files:**
- Modify: `src/butlers/modules/pipeline.py`
- Modify: `tests/modules/test_module_pipeline.py`
- Modify: `tests/integration/test_decomposition_flow.py`

**Interfaces:**
- Extends `_normalize_decomp_excerpts`, `_normalize_decomp_signal`, and
  `_normalize_decomp_signals` with optional `authoritative_by_message_id` mappings.
- Consumes enriched messages from Task 3.
- Produces additive excerpt fields `sender_identity` and `sender_entity_id`.

- [ ] **Step 1: Add failing authoritative-join tests**

Add cases proving model-supplied UUID/identity/label/text/timestamp cannot override the source record,
unknown message IDs are dropped, and duplicate concepts reuse one anchor. Cite
`REQ-conversation-decomposition-001`.

```python
def test_model_cannot_replace_authoritative_sender_entity_id():
    """Spec: REQ-conversation-decomposition-001."""
    authoritative = {
        "m1": {
            "message_id": "m1",
            "sender": "Alice",
            "sender_identity": "6591111111@s.whatsapp.net",
            "sender_entity_id": "11111111-1111-1111-1111-111111111111",
            "text": "Dinner at seven",
            "timestamp": "2026-08-24T10:00:00Z",
        }
    }
    result = _normalize_decomp_excerpts(
        [{"message_id": "m1", "sender_entity_id": "attacker", "text": "changed"}],
        authoritative_by_message_id=authoritative,
    )
    assert result == [authoritative["m1"]]
```

- [ ] **Step 2: Run decomposition tests and verify RED**

```bash
uv run pytest tests/modules/test_module_pipeline.py \
  tests/integration/test_decomposition_flow.py -q --tb=short
```

Expected: new keyword arguments or identity fields fail because normalization trusts model output.

- [ ] **Step 3: Implement authoritative message-ID projection**

Use these compatible exact signatures:

- `def _normalize_decomp_excerpts(raw: Any, *, authoritative_by_message_id: Mapping[str,
  Mapping[str, Any]] | None = None) -> list[dict[str, Any]]`
- `def _normalize_decomp_signal(sig: Any, *, authoritative_by_message_id: Mapping[str,
  Mapping[str, Any]] | None = None) -> dict[str, Any] | None`
- `def _normalize_decomp_signals(raw: Any, *, authoritative_by_message_id: Mapping[str,
  Mapping[str, Any]] | None = None) -> list[dict[str, Any]]`

When an authoritative map is supplied, accept the model's `message_id` only as a selector and copy
`sender`, `sender_identity`, `sender_entity_id`, `text`, `timestamp`, and `message_id` from the source.
Drop missing, unknown, duplicate/colliding, or blank IDs. Preserve the old four-field behavior when no
authoritative map is supplied.

- [ ] **Step 4: Update the decomposition prompt and production call**

Tell signal extraction that `message_id` selects authoritative messages and identity fields are
injected after extraction. Pass the enriched-message index to `_normalize_decomp_signals()` in the
conversation-history branch.

- [ ] **Step 5: Run tests and verify GREEN**

Run the Step 2 command. Expected: selected unit and integration tests pass.

- [ ] **Step 6: Commit authoritative excerpts**

```bash
git add src/butlers/modules/pipeline.py tests/modules/test_module_pipeline.py \
  tests/integration/test_decomposition_flow.py
git commit -m "feat(pipeline): preserve speaker identity through decomposition"
```

### Task 5: Block transport-named fact entities

**Files:**
- Modify: `src/butlers/modules/memory/tools/entities.py`
- Modify: `src/butlers/modules/memory/__init__.py`
- Modify: `tests/modules/test_module_memory.py`
- Modify: `roster/shared/skills/butler-memory/SKILL.md`
- Modify: `roster/relationship/.agents/skills/fact-extraction/SKILL.md`

**Interfaces:**
- Produces: `is_whatsapp_transport_identifier(value: str) -> bool`.
- The runtime wrapper returns a stable content-blind structured error for prohibited fact-storage
  person creation.

- [ ] **Step 1: Add failing guard tests**

Cover phone JID, device JID, numeric LID, no identifier echo, ordinary at-sign names, and non-fact
provenance. Cite `REQ-entity-identity-001`.

- [ ] **Step 2: Run memory tests and verify RED**

```bash
uv run pytest tests/modules/test_module_memory.py -q --tb=short
```

Expected: JID/LID calls currently insert or delegate instead of returning the guarded error.

- [ ] **Step 3: Implement the narrow identifier recognizer and wrapper guard**

Match only numeric individual WhatsApp forms:

```python
_WHATSAPP_TRANSPORT_IDENTIFIER_RE = re.compile(
    r"^(?:\d+(?::\d+)?@s\.whatsapp\.net|\d+(?::\d+)?@lid)$"
)


def is_whatsapp_transport_identifier(value: str) -> bool:
    return bool(_WHATSAPP_TRANSPORT_IDENTIFIER_RE.fullmatch(value.strip()))
```

Guard only `entity_type == "person"`, `metadata.source == "fact_storage"`, and a matching identifier.
Return:

```python
{
    "error": "transport_identifier_not_entity_name",
    "message": (
        "Cannot create a person from a WhatsApp transport identifier. "
        "Use the conceptual excerpt's sender_entity_id; if it is absent, skip the fact."
    ),
}
```

Never include the rejected value.

- [ ] **Step 4: Update runtime guidance**

Add a sender-excerpt section to both skills: `sender_entity_id` is authoritative; `sender_identity`
is transport data; never resolve or create from JID/LID; skip a speaker fact when no entity anchor is
available.

- [ ] **Step 5: Run tests and verify GREEN**

Run the Step 2 command. Also run the skill packaging audit on both touched skill directories if the
repo audit accepts project-local skills; otherwise run link/frontmatter checks documented by
craft-and-care and report the limitation.

- [ ] **Step 6: Commit fact-storage protection**

```bash
git add src/butlers/modules/memory/tools/entities.py src/butlers/modules/memory/__init__.py \
  tests/modules/test_module_memory.py roster/shared/skills/butler-memory/SKILL.md \
  roster/relationship/.agents/skills/fact-extraction/SKILL.md
git commit -m "fix(memory): reject WhatsApp identifiers as entity names"
```

### Task 6: Extract the audited relationship merge service

**Files:**
- Create: `roster/relationship/tools/entity_merge.py`
- Modify: `roster/relationship/api/router.py`
- Create: `roster/relationship/tests/test_entity_merge_service.py`
- Modify: `tests/api/test_relationship_entities_merge.py`
- Modify: `roster/relationship/tests/test_merge_review_no_llm.py`
- Verify unchanged: `roster/relationship/tests/test_merge_repoints_all_source_refs.py`
- Verify unchanged: `roster/relationship/tests/test_contacts_merge_no_stranded_triples.py`
- Verify unchanged: `roster/relationship/tests/test_merge_cardinality_single_resolution.py`

**Interfaces:**

```python
@dataclass(frozen=True)
class LockedEntityPair:
    source: Mapping[str, Any]
    target: Mapping[str, Any]

@dataclass(frozen=True)
class EntityMergeResult:
    kept_entity_id: UUID
    tombstoned_entity_id: UUID
    subject_facts_rewired: int
    object_facts_rewired: int
    review_id: UUID

LockedMergeGuard = Callable[[asyncpg.Connection, LockedEntityPair], Awaitable[None]]

async def merge_entity_pair(
    pool: asyncpg.Pool,
    *,
    source_entity_id: UUID,
    target_entity_id: UUID,
    locked_guard: LockedMergeGuard | None = None,
) -> EntityMergeResult:
    """Atomically merge one locked entity pair and return content-blind counts."""
```

- [ ] **Step 1: Add failing service contract tests**

Test deterministic locks, guard ordering, rollback on guard rejection, current subject/object conflict
behavior, tombstone plus in-transaction audit, and identifier-free exception messages. Cite
`REQ-entity-identity-002`.

- [ ] **Step 2: Run merge tests and verify RED**

```bash
uv run pytest roster/relationship/tests/test_entity_merge_service.py \
  tests/api/test_relationship_entities_merge.py \
  roster/relationship/tests/test_merge_repoints_all_source_refs.py \
  roster/relationship/tests/test_contacts_merge_no_stranded_triples.py \
  roster/relationship/tests/test_merge_cardinality_single_resolution.py -q --tb=short
```

Expected: the new service import fails; existing endpoint tests remain baseline green.

- [ ] **Step 3: Move the existing audited transaction unchanged behind the service**

Move the transaction from the relationship merge endpoint into `merge_entity_pair()`. Keep
deterministic `ORDER BY id FOR UPDATE`, single-cardinality resolution, relationship subject/object
rewiring, memory facts rewiring, `contact_entity_map`, source tombstone, and `merge_reviews` write in
one transaction. Extend locked rows with `entity_type`, `aliases`, `roles`, `canonical_name`, and
`updated_at`. Invoke `locked_guard` after both locks and tombstone validation but before any writes.

Define stable domain errors (`SameEntityError`, source/target missing/tombstoned,
`LockedGuardRejected(category: str)`) whose string forms contain only classifications.

- [ ] **Step 4: Reduce the FastAPI handler to authorization and translation**

Retain owner gate, same-entity validation, `keepAs` selection, domain-error to existing HTTP mapping,
and response conversion. Mock the service in HTTP tests so SQL behavior belongs only to service/DB
tests.

- [ ] **Step 5: Run merge tests and verify GREEN**

Run the Step 2 command plus:

```bash
uv run pytest roster/relationship/tests/test_merge_review_no_llm.py -q --tb=short
```

Expected: all selected tests pass and no model client is reachable from the service.

- [ ] **Step 6: Commit the merge service**

```bash
git add roster/relationship/tools/entity_merge.py roster/relationship/api/router.py \
  roster/relationship/tests/test_entity_merge_service.py \
  tests/api/test_relationship_entities_merge.py \
  roster/relationship/tests/test_merge_review_no_llm.py
git commit -m "refactor(relationship): share the audited entity merge transaction"
```

### Task 7: Build guarded WhatsApp reconciliation

**Files:**
- Create: `roster/relationship/tools/whatsapp_reconciliation.py`
- Create: `scripts/reconcile_whatsapp_entities.py`
- Create: `roster/relationship/tests/test_whatsapp_reconciliation.py`
- Create: `tests/scripts/test_reconcile_whatsapp_entities.py`
- Modify: `roster/relationship/tests/test_merge_review_no_llm.py`

**Interfaces:**

```python
class ReconciliationCategory(StrEnum):
    UNIQUE_EMPTY_SHELL = "unique_empty_shell"
    UNMATCHED = "unmatched"
    AMBIGUOUS = "ambiguous"
    INVALID_IDENTIFIER = "invalid_identifier"
    OWNER_OR_SYSTEM_TARGET = "owner_or_system_target"
    EXISTING_REVIEW_DECISION = "existing_review_decision"
    REFERENCED_SOURCE = "referenced_source"
    PLAN_DRIFT = "plan_drift"

@dataclass(frozen=True)
class PlannedWhatsAppMerge:
    source_entity_id: UUID
    target_entity_id: UUID
    source_updated_at: datetime
    target_updated_at: datetime
    review_state: str

@dataclass(frozen=True)
class WhatsAppReconciliationPlan:
    pairs: Sequence[PlannedWhatsAppMerge]
    counts: Mapping[ReconciliationCategory, int]
    digest: str

@dataclass(frozen=True)
class ContentBlindReconciliationReport:
    mode: Literal["dry_run", "apply"]
    counts: Mapping[str, int]
    planned: int
    applied: int
    plan_digest: str
```

Create exact async entry points `build_whatsapp_reconciliation_plan(pool: asyncpg.Pool) ->
WhatsAppReconciliationPlan` and `apply_whatsapp_reconciliation(pool: asyncpg.Pool, *,
authorized_digest: str) -> ContentBlindReconciliationReport`.

- [ ] **Step 1: Add failing real-PostgreSQL planner tests**

Create fixtures for `public.entities`, `public.whatsmeow_lid_map`, relationship identity/memory facts,
`contact_entity_map`, `merge_reviews`, pending decisions, an arbitrary test-only FK table, and an
explicit text-object fact. Cover unique, unmatched, ambiguous, LID mapped/unmapped, owner/system,
roles/aliases/unexpected metadata, every reference class, decisions, digest stability, digest drift,
locked drift, audit, post-count, and stop-on-first-failure. Cite `REQ-entity-identity-002`.

- [ ] **Step 2: Run planner tests and verify RED**

```bash
uv run pytest roster/relationship/tests/test_whatsapp_reconciliation.py -q --tb=short
```

Expected: import fails because the planner does not exist.

- [ ] **Step 3: Implement candidate enumeration and content-blind planning**

Parse only numeric individual phone/device JIDs and numeric LIDs from approved provenance. Translate
LIDs through `whatsmeow_lid_map`. Enumerate all distinct live confirmed phone candidates using exact
and bounded digit matching; never use a first-row bulk result as uniqueness evidence. Discover FK
references through `pg_constraint` and explicitly inspect textual entity-object references.

Sort pairs and hash canonical JSON of source UUID, target UUID, both `updated_at` values, and normalized
review-decision state. Return category counts and digest without display content.

- [ ] **Step 4: Implement locked apply and postconditions**

Rebuild the whole plan and compare the exact digest before the first service call. Execute pairs
sequentially through `merge_entity_pair()` with `locked_guard=validate_empty_shell_locked`. The guard
revalidates plan state and empty-shell references inside the transaction. After each success, verify
source tombstone, live target, zero references, one merged review outcome, and disappearance from a
fresh plan. Abort on the first failure.

- [ ] **Step 5: Add failing CLI safety and privacy tests**

Test default dry-run, argument pairing, missing DSN, digest mismatch, JSON allowlist, sentinel absence
from stdout/stderr/caplog, PEP 723 metadata, and absence of startup/scheduler imports.

- [ ] **Step 6: Run CLI tests and verify RED**

```bash
uv run pytest tests/scripts/test_reconcile_whatsapp_entities.py -q --tb=short
```

Expected: script import fails.

- [ ] **Step 7: Implement the thin operator command**

Use PEP 723 metadata and exact entry points `async def run(*, apply: bool, plan_digest: str | None) ->
ContentBlindReconciliationReport` and `async def main(argv: list[str] | None = None) -> int`.

Accept only environment-provided `BUTLERS_DATABASE_URL`. Reject `--apply` without `--plan-digest` and
the inverse. Emit deterministic JSON with only `mode`, `counts`, `planned`, `applied`, and
`plan_digest`; map exceptions to content-blind category/error codes without `str(exc)`.

- [ ] **Step 8: Run reconciliation tests and verify GREEN**

```bash
uv run pytest roster/relationship/tests/test_whatsapp_reconciliation.py \
  tests/scripts/test_reconcile_whatsapp_entities.py \
  roster/relationship/tests/test_merge_review_no_llm.py -q --tb=short
```

Expected: all selected tests pass.

- [ ] **Step 9: Commit reconciliation**

```bash
git add roster/relationship/tools/whatsapp_reconciliation.py \
  scripts/reconcile_whatsapp_entities.py \
  roster/relationship/tests/test_whatsapp_reconciliation.py \
  tests/scripts/test_reconcile_whatsapp_entities.py \
  roster/relationship/tests/test_merge_review_no_llm.py
git commit -m "feat(relationship): add guarded WhatsApp entity reconciliation"
```

### Task 8: End-to-end proof, contract closeout, and review

**Files:**
- Modify: `tests/integration/test_decomposition_flow.py`
- Modify: `tests/integration/test_whatsapp_pipeline.py`
- Modify: `openspec/changes/repair-whatsapp-identity-reconciliation/tasks.md`
- Modify if behavior wording changed: affected delta specs and design
- Modify only with new generalizable knowledge: `AGENTS.md` under `# Notes to self`

**Interfaces:**
- Consumes all previous tasks.
- Produces exact-head merge-readiness evidence and a content-blind PR.

- [ ] **Step 1: Add the mixed-speaker end-to-end regression**

Drive a mapped known speaker and an unmapped unknown speaker through connector envelope construction,
Switchboard decomposition, authoritative excerpt normalization, and routed conceptual messages. Assert
the known UUID and reserved unknown UUID remain distinct and no public entity canonical name matches a
WhatsApp JID/LID. Cite all six requirement IDs in adjacent test docstrings/comments.

- [ ] **Step 2: Run focused feature suites**

```bash
uv run pytest \
  tests/core/test_whatsapp_identity.py tests/core/test_identity.py \
  tests/core/test_identity_resolution_entity_facts.py tests/core/test_buffer.py \
  tests/test_passive_interaction_sender_identity.py \
  tests/connectors/test_whatsapp_user_client.py \
  roster/switchboard/tests/test_identity_injection.py \
  tests/modules/test_module_pipeline.py tests/modules/test_module_memory.py \
  tests/integration/test_whatsapp_pipeline.py tests/integration/test_decomposition_flow.py \
  roster/relationship/tests/test_entity_merge_service.py \
  roster/relationship/tests/test_whatsapp_reconciliation.py \
  tests/scripts/test_reconcile_whatsapp_entities.py -q --tb=short
```

Expected: all selected tests pass.

- [ ] **Step 3: Close the spec test-citation warnings**

Run:

```bash
uv run /home/tze/.dotfiles/ai-bootstrap/skills/personal/th-projects/scripts/spec-trace-check.py \
  /home/tze/.butlers-worktrees/fix-whatsapp-identity-reconciliation --authoring 2>&1 | \
  rg 'repair-whatsapp-identity-reconciliation|spec-trace-check:'
```

Expected: no warning for any of the six new requirement IDs. Repository-wide legacy errors may remain
only if unchanged from baseline.

- [ ] **Step 4: Run spec and source quality gates**

```bash
openspec validate repair-whatsapp-identity-reconciliation --strict
make check-spec-overwrites
uv run ruff check src/ tests/ roster/ conftest.py --output-format concise
uv run ruff format --check src/ tests/ roster/ conftest.py -q
git diff --check
```

Expected: every command exits zero.

- [ ] **Step 5: Run broader merge-readiness tests**

```bash
make test-qg
```

Expected: exit zero. If a baseline failure appears, prove it against `origin/main` before classifying
it; do not waive a feature regression.

- [ ] **Step 6: Run independent review**

Request separate behavior/spec, engineering, privacy/security, and operator-safety reviews of the
exact head. Resolve every blocking finding with a new red-green cycle and rerun affected gates.

- [ ] **Step 7: Mark OpenSpec tasks complete and verify apply-ready state**

Change each completed checkbox in
`openspec/changes/repair-whatsapp-identity-reconciliation/tasks.md` to `[x]`, then run:

```bash
openspec status --change repair-whatsapp-identity-reconciliation
openspec validate repair-whatsapp-identity-reconciliation --strict
```

Expected: planning artifacts complete and change valid; implementation verification has no unmet task.

- [ ] **Step 8: Commit final verification/docs changes**

```bash
git add tests/integration/test_decomposition_flow.py tests/integration/test_whatsapp_pipeline.py \
  openspec/changes/repair-whatsapp-identity-reconciliation
git commit -m "test(whatsapp): prove identity reconciliation end to end"
```

- [ ] **Step 9: Push and open the pull request**

```bash
git pull --rebase
git push
gh pr create --base main --head fix/whatsapp-identity-reconciliation \
  --title "Fix WhatsApp identity reconciliation" --body-file /tmp/whatsapp-pr-body.md
```

The PR body must contain no names, phone numbers, JIDs, LIDs, session links, secrets, or raw live-data
evidence. It should summarize behavior, safety boundaries, and commands actually run.

- [ ] **Step 10: Verify exact-head CI and final worktree state**

Confirm the PR head SHA equals local `HEAD`, required checks are green, review threads are resolved,
and:

```bash
git status --short --branch
git rev-parse HEAD
git rev-parse @{u}
```

Expected: clean worktree and identical local/upstream SHAs. Do not execute reconciliation or deploy
without a separate reviewed dry-run digest and deployment authorization.
