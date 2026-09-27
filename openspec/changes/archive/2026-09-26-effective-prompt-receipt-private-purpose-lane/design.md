## Context

`Spawner` currently computes the final system prompt immediately before runtime invocation, while
`sessions.prompt` stores the user/task prompt. Those are different artifacts and must remain
separate. The butler-management prompt route reads only `public.system_prompt_history`; it is not a
witness of the runtime-composed prompt. Connector discretion calls do not create ordinary session
rows, but they do pass through the model catalog, token ledger, and dispatch-attempt evidence.

The active `specify-roster-identity-owner-operations-overlay` change proposes a future composition
authority model. This change neither adopts nor implements it. Receipts describe the composition
that actually ran, including whether a database override selected the base.

## Decisions

### D1: Receipt the final bytes and the named inputs

The composer returns an immutable receipt beside the final system-prompt string. The receipt uses
SHA-256 over UTF-8 bytes and contains:

- `effective_prompt`, stored only on the session row and returned only by the prompt-detail door;
- `prompt_digest`, lowercase 64-character hex;
- `total_bytes`, derived from the stored effective prompt rather than trusted independently; and
- ordered provenance entries with `source`, `status`, `bytes`, and `sha`.

Source identifiers are stable logical names or `roster:<relative-posix-path>` values, never absolute
paths. Present effective layers use `present`; optional missing/unavailable layers use `unavailable`
with zero bytes and no digest; a roster base displaced by the current database override is retained
as `shadowed` provenance so the operator can see what did not run. Provenance never includes source
content. The digest is a pure function of final bytes and is independent of clock, process, or path.

The receipt is built after every current composition layer is resolved and before `session_create`
and runtime invocation. Receipt construction failure blocks invocation rather than running an
unreceipted session. Existing composition order and availability behavior remain unchanged.

### D2: Additive session storage and a narrow content door

New nullable columns preserve legacy rows. New rows require the spawner to provide the effective
prompt, digest, provenance, and purpose lane. `GET /api/sessions/{id}/prompt` performs the same
cross-butler fan-out and 404-versus-degraded distinction as session detail, verifies the stored
digest/byte count before returning, and fails closed on corrupt receipt data. Session list, ordinary
detail, spend, audit, metric, and telemetry projections do not gain effective prompt content.

### D3: Drift compares roster provenance, not mutable prompt history

The butler Configuration projection composes the current prompt using the same receipt builder and
compares its roster-source digest map with the latest executed session receipt for that butler.
`matches_git` means those roster sources are byte-identical; it does not claim dynamic context or a
database override is unchanged. A changed, added, missing, or unreadable roster-relative source
produces `drifted`, a bounded changed-source list, and `drifted_since` equal to the newest receipt
that no longer matches. No prior receipt produces `unknown`, never a green match.

The comparison is deterministic and idempotent. Every spawn receipts its resolved roster sources,
and the bus-aware read projection rechecks current files, so a file change becomes visible without
deployment or a separate prompt-authoring path.

### D4: Purpose lane is content-blind and source-derived

`purpose_lane` is exactly `standard` or `private_content`. It is derived from trusted routing or
connector context, never prompt inspection: WhatsApp and Telegram user-client/bot sources are
private; other or unknown sources are standard. The value is safe to persist on session,
token-usage, and dispatch-attempt evidence and safe to render as a badge. Sender, recipient, thread,
or message identifiers are never used as the lane value.

### D5: Purpose lane is evidence, not model-selection authority

`purpose_lane` is carried into session, dispatch-attempt, and token-usage evidence, but it does not
independently add or remove a catalog candidate. Initial selection, tier fallthrough, priority,
fit, verification, quota, breaker, and same-tier failover continue to use the canonical
model-catalog contracts and any separately adopted operator routing rules. In particular,
`private_content` does not require OpenCode, an `ollama/` model, locality proof, or a
private-purpose audited remote exception, and it does not authorize a
`private_content_remote_refused` outcome while an ordinarily eligible catalog candidate exists.

This preserves Heart-and-Soul's recorded trust model: external LLM providers are partially trusted,
and the owner accepts provider exposure as a condition of using the system. The lane remains useful
content-blind provenance; changing that trust model requires an explicit doctrine decision rather
than a routing implementation detail.

## Rollback

The schema additions are nullable and downgrade only removes the new columns when no new receipt
data exists. A requested downgrade that crosses the protected runtime-attention boundary preflights
that boundary before either newer revision changes schema or its version stamp; refusal preserves
the original head and all receipt/purpose evidence. Removing the UI projections falls back to
existing session and prompt displays.
Stored lane values are non-secret provenance and need no replay or reinterpretation; removing the
display later does not alter model-selection history.
