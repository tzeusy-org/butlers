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

The comparison is deterministic and idempotent. Daemon composition checks at boot/first spawn and
the read projection rechecks current files, so a file change is visible without deployment or a
separate prompt-authoring path.

### D4: Purpose lane is content-blind and source-derived

`purpose_lane` is exactly `standard` or `private_content`. It is derived from trusted routing or
connector context, never prompt inspection: WhatsApp and Telegram user-client/bot sources are
private; other or unknown sources are standard. The value is safe to persist on session,
token-usage, and dispatch-attempt evidence and safe to render as a badge. Sender, recipient, thread,
or message identifiers are never used as the lane value.

### D5: Private content defaults to local and remote use requires current audit evidence

A catalog model is local only when its canonical `model_id` begins `ollama/`; runtime type or zero
price alone is not proof. Before any adapter is created or invoked, private-content selection and
same-tier failover exclude non-local models.

The only exception is an existing operator spend-routing rule whose condition explicitly matches
`purpose=private_content`, whose action explicitly names the selected remote model, and whose latest
successful `spend.rule.create` or `spend.rule.update` audit evidence is at least as new as the rule.
A catch-all, tier-only rule, old audit for a subsequently changed rule, audit read failure, or
post-selection code path is not authority. The exception is recorded as `audited_remote_override`
without content.

If no local model and no valid audited exception exists, routing refuses before provider setup or
invocation, appends a bounded audit/dispatch-attempt reason, and exposes the refusal without falling
back remotely. Audit write failure does not turn refusal into permission.

## Rollback

The schema additions are nullable and downgrade only removes the new columns when no new receipt
data exists. Removing the UI projections falls back to existing session and prompt displays.
Removing lane enforcement requires reverting source and migration together; stored lane values are
non-secret evidence and need no replay or reinterpretation.
