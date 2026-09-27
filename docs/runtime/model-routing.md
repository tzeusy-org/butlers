# Model Routing

> **Purpose:** Describe the dynamic model selection system: the catalog, per-butler overrides, complexity tiers, token quotas, and usage tracking.
> **Audience:** Developers configuring model selection, operators managing token budgets, architects understanding the cost optimization strategy.
> **Prerequisites:** [LLM CLI Spawner](spawner.md), [Session Lifecycle](session-lifecycle.md).

## Overview

Model routing (`src/butlers/core/model_routing.py`) selects the best AI model for each spawner invocation based on task complexity and butler identity. Rather than hardcoding a single model per butler, the system uses a shared catalog with per-butler overrides and complexity tiers. This enables cost optimization --- cheap models for simple tasks, capable models for complex ones --- and allows operators to tune model selection without changing butler code.

## Complexity Tiers

Tiers are the `Complexity` enum in `src/butlers/core/model_routing.py`. They are ordered from most
to least capable, and `TIER_FALLTHROUGH_ORDER` is the order resolution falls through when a tier
has no usable candidate. `WORKHORSE` is the default for triggers and scheduled tasks. A caller that
still emits the retired vocabulary (`trivial`, `medium`, ...) is remapped with a loud warning by
`_check_deprecated_tier()`, so fix the caller when you see that warning.

## The Model Catalog

The `public.model_catalog` table is the global registry of available models. Each entry has:

- **`id`** --- UUID primary key (referenced by quota and usage tables)
- **`runtime_type`** --- the adapter to use (`"claude"`, `"codex"`, `"gemini"`, `"opencode"`,
  `"api"`)
- **`model_id`** --- the model identifier string
- **`complexity_tier`** --- which complexity tier this entry serves
- **`priority`** --- numeric priority (higher wins when multiple entries match)
- **`enabled`** --- whether this entry is active
- **`extra_args`** --- JSONB list of CLI token strings passed to the adapter
- **`created_at`** --- tie-breaker for entries with equal priority (older entries win)
- **`capabilities`** --- JSONB object (default `{}`) of per-entry capability overrides layered over
  the runtime adapter's declared baseline; see *Capability fit* below
- **`max_context_tokens`** / **`max_output_tokens`** --- nullable context envelope; `NULL` means
  undeclared, and undeclared is treated as unproven

### Field ownership: catalog vs. runtime config

`model`, `runtime_type`, `args`, and `session_timeout_s` live **on `public.model_catalog`** (`session_timeout_s INT NOT NULL DEFAULT 1800`), not on
`{schema}.runtime_config`. They are resolved per complexity tier by `resolve_model()`, which returns
the chosen catalog entry id and its `session_timeout_s`, and are edited via the dashboard's **Models
tab** / `GET/PATCH /api/model-settings` (`src/butlers/api/routers/model_settings.py`).

`{schema}.runtime_config` holds `core_groups`, `max_concurrent`, and `max_queued` (cold: require a
daemon restart to take effect) alongside `catalog_read_sensitivity` and `tool_exposure_policy` (hot: a PATCH takes effect for the next
planned session with no restart). All five fields are seeded from `[butler.runtime_seed]` in
`butler.toml` on first boot and edited via `GET/PATCH /api/butlers/{name}/runtime-config`
(`src/butlers/api/routers/runtime_config.py`), which reports each field's tier in the response's
`field_tiers` map.

Cold fields are read through the 30s TTL cache in `RuntimeConfigAccessor`
(`src/butlers/core/runtime_config.py`). `tool_exposure_policy` is closed to `eager_filtered` (default,
conservative) or `auto`, and every per-attempt caller MUST resolve it through
`RuntimeConfigAccessor.get_tool_exposure_policy()`, which always reads the DB directly instead of the
TTL cache --- the dashboard API and the butler daemon can be separate processes, so a cached read
cannot guarantee the first session planned after a committed PATCH sees the new policy. Do not look
for model settings on the runtime-config surface, and do not add operational limits to the catalog.

### Verification evidence is not routing evidence

The catalog also carries four verification columns, and they answer a narrower question than
anything else on this page. They are written by exactly one thing: a runtime probe that Switchboard
ran under a signed control capability, on the owner's `Test` / verify-all action or the scheduled
sweep (`src/butlers/jobs/model_verify.py`). Nothing else writes them, and the `SECURITY DEFINER`
function that does cannot reach `enabled`, `priority`, or breaker state.

So keep three signals apart:

| Signal | Says | Does not say |
|---|---|---|
| Verification columns | this entry answered a fixed probe prompt at that time | that routed traffic is healthy |
| `model_dispatch_attempts` | what real routed dispatch did | anything about probes --- a probe never writes an attempt, a session, or routed provenance |
| Breaker state | routing's own health judgement | anything a probe can change --- a green probe never closes an open breaker |

A failed probe is also not the same as a control-plane failure. `401`, `409`, `429`, `503`, and
`504` from the control plane are statements about the plane, not the model; they write no
verification evidence and the Models tab renders them as "could not be probed". See
[Runtime-Probe Control Keys](../operations/runtime-probe-control-keys.md).

### Operator attention and deliberate reissue

The Models page reads durable breaker-alert delivery separately from both verification and routing.
`GET /api/settings/models/attention` is a batched, content-blind projection: it publishes only the
episode identity, lifecycle timestamps/state, finite safe reason, and direct-successor state. It
does not publish the stored source snapshot or payload. The protected read and
`POST /api/settings/models/attention/{episode_id}/reissue` both require the fail-closed dashboard
owner key even when general API authentication is disabled; absent owner-control configuration is
`503`, while a missing or wrong key is `401` before database observation.

Only an `uncertain` original may be reissued. The database operation serializes on that original,
refuses a sending row or live delivery-service lease, and atomically creates or returns its one
pending successor. It never mutates the original, invokes transport, writes dispatch provenance,
or changes the breaker. Disabling the operator-v3 control stops new successors while retaining all
observation evidence.

Spend uses the same content-blind boundary for the current UTC month's fleet-halt episode at
`GET /api/spend/runtime-attention`. The durable alert source and the dispatch-denial attempts source
remain independent: either one failing is rendered as unavailable, not as “no alert” or “no
denials,” and the existing denial drawer and session links remain available whenever their source
is healthy.

## Per-Butler Overrides

The `public.butler_model_overrides` table allows per-butler customization without duplicating catalog entries. An override row references a catalog entry and can remap `enabled`, `priority`, and `complexity_tier`. Overrides use `COALESCE` semantics: when an override field is NULL, the catalog value is used.

## Resolution Algorithm

`resolve_model(pool, butler_name, complexity_tier)` executes a single SQL query:

1. LEFT JOIN `public.model_catalog` with `public.butler_model_overrides` on the butler name and catalog entry ID.
2. Compute effective values via COALESCE for enabled, priority, and complexity_tier.
3. Filter: effective `enabled = true` AND effective `complexity_tier = $tier`.
4. Order by effective `priority DESC`, then `created_at ASC` (stable tie-break).
5. Return the first matching row as `(runtime_type, model_id, extra_args, catalog_entry_id)`, or `None`.

There is no `butler.toml` model fallback: when no candidate resolves, a live spawner fails with
`ModelResolutionError` (see *Resolution Flow in the Spawner* below).

### Private-content purpose lane

Dispatch purpose is a closed, content-blind dimension. A trusted WhatsApp or Telegram source marks
the dispatch `private_content`; all other and unknown sources remain `standard`. The classifier
reads only the established routing/connector channel token. It never inspects prompt, message,
sender, recipient, or thread content to infer sensitivity.

`private_content` is provenance, not model-selection authority. It neither adds nor removes a
catalog candidate and does not change effective tier, priority, fit, verification, quota, breaker,
provider/runtime selection, or same-tier failover. Normal operator routing rules retain their
ordinary evaluation and authority; the lane itself creates no local-only requirement or special
remote-model exception.

New private discretion usage retains its existing spend purpose and carries the separate closed
`purpose_lane=private_content` on token-usage and dispatch-attempt evidence with the stable
dispatcher identity, never a raw chat or sender identifier. New ordinary sessions and their
dispatch attempts persist the same purpose lane for session-list and dossier visibility.

## Capability fit

Everything above decides whether an entry is *allowed*. It does not decide whether the entry can do
the job. `resolve_dispatch(pool, butler_name, intent)` adds that step, and the spawner reaches it by
passing `intent=` to `resolve_model_with_effective_tier`.

**The intent.** `derive_dispatch_intent(trigger_source, complexity_tier, ...)`
(`src/butlers/core/dispatch_intent.py`) builds a deterministic, prompt-free `DispatchIntent`: the
capabilities the dispatch requires, an optional context floor / deadline / per-call budget, and a
consequence level (`observe` < `reversible` < `external`). The requirement that matters most in
practice is `tool_use`: the spawner wires MCP servers for every trigger source except `healing` and
`qa`, so every other source requires a runtime that can accept tools.

**The capability answer.** `src/butlers/core/model_capabilities.py` resolves three-valued support
(supported / unsupported / **unknown**) by layering a catalog row's `capabilities` envelope over the
`declared_capabilities` its `RuntimeAdapter` subclass declares (`session_resume` comes from
`supports_resume`). An empty envelope --- the default for every existing row --- changes nothing,
which is why the migration excludes nobody. An unregistered `runtime_type` answers *unknown* for
everything, and unknown fails closed above `observe` consequence.

**The ordering.** Fit is applied to every candidate in every candidate tier **before** the winning
tier is chosen, before priority narrowing, and before the tie-break. The concrete reason: the seeded
`api-haiku-cheap` entry has priority 30 --- top of the `cheap` tier --- while `ApiAdapter.invoke`
raises for any non-empty `mcp_servers`. Narrowing first means that one unusable entry takes the whole
tier down; filtering first lets a lower-priority, capable entry in the same tier win.

Ranking itself is unchanged. `preferred_features` are recorded on the receipt and never re-rank ---
preferring, say, resume-capable models for interactive triggers is an owner cost decision, not an
inference-contract one. An intent that requires nothing selects exactly what the pre-intent resolver
selects.

**The receipt.** `resolve_dispatch` returns a `DispatchResolution`: requested vs effective intent
(differing only in tier, when fallthrough occurred), every candidate with its outcome
(`selected` / `eligible` / `excluded_hard_fit` / `excluded_breaker` / `excluded_quota` /
`not_top_priority` / `tier_not_reached`) and fit findings, evidence age, and the winner reason
(`sole_candidate` / `evidence_score` / `round_robin`). An `excluded_breaker` candidate records
`exclusion="breaker_open"` in the persisted projection. It is prompt-free by construction and
`describe()` is JSON-safe.
It is carried on `TierQuotaExhausted.resolution` when quota blocks the tier. Catalog-backed
Spawner and DiscretionDispatcher attempts persist a projection of that receipt. Discretion receipt
capture observes the legacy winner without parsing capability envelopes or changing eligibility. A
spend-rule override may re-project the final winner only after the replacement is confirmed
fit-eligible for the original intent and effective tier. A hard-fit
exclusion remains non-invocable and is never cleared merely because an override selected it.
Failover projections carry the preceding failure class, while a
transparent retry of the same candidate after a failed resume handle is labeled
`same_candidate_cold_retry` instead. The durable JSON is measured with the registered asyncpg JSONB
encoder and bounded to 32 KiB across the entire projection, not only its candidate list; requested
and effective intent remain present in the bounded fallback. The row and receipt share one
monotonically increasing `attempt_index` across quota skips and runtime attempts.

**Vision is an exact-path claim.** The adopted target is
[RFC0036](../../about/legends-and-lore/rfcs/0036-models-exact-path-vision-proof.md) and the
active [Models vision-proof change](../../openspec/changes/models-exact-path-vision-proof/).
It requires image delivery through the production attachment path, an exact runtime/model/config
and account identity, and three same-tuple controls: a positive image, a text-only control, and a
removed-image control. Ordinary text Verify, a direct `attachment_view()` unit test, and model or
adapter-wide claims do not establish vision support. The direct API adapter cannot satisfy the
MCP attachment path because it does not accept the butler MCP server configuration.

The bounded diagnostic executor and proof application are not implemented yet. A passing check
will produce evidence only; Enable vision, Apply proof, or Refresh applied proof must then use an
explicit exact-row compare-and-swap action. A generic catalog `PUT` is not the adopted path for a
new `vision=true` declaration or an identity-changing write to an existing true row. Existing
unchanged historical declarations retain their current behavior until explicitly moved into the
managed proof lifecycle. Do not use a text-only canary or manually set `vision=true` to claim
proof under the new contract.

**Never give `attachment_view` structured output.** Codex CLI and Claude Code forward only
`structuredContent` when a tool result carries it, dropping `content[]` and the image with it
(openai/codex#10334). `tests/core/test_attachment_view.py` pins the wire shape.

## Token Quotas

The quota system prevents runaway costs by limiting token consumption per model on rolling time windows.

### Quota Check

`check_token_quota(pool, catalog_entry_id)` returns a `QuotaStatus` dataclass with `allowed`, `usage_24h`, `limit_24h`, `usage_30d`, and `limit_30d`. The check uses a CTE-based single round-trip query. A fast path skips the ledger query when no limits row exists. The check is **fail-open**: database errors return `allowed=True`. The quota guardrail must never block all sessions.

### Token Usage Recording

`record_token_usage()` writes to `public.token_usage_ledger` once per invoked spawner attempt. The row references the matching `public.model_dispatch_attempts.id`: parseable provider usage is stored with `usage_source=measured`, while a timeout or other invocation with no parseable usage stores `usage_source=unmeasurable` and NULL token buckets. Historical rows remain `measured` with a NULL attempt link. Month-to-date pricing sums measured rows and separately reports `unmeasurable_attempts`, so failover storms and unknown usage cannot disappear behind one confident session total. Recording remains best-effort: errors are logged and never propagate to the caller.

The ledger also carries a token digest for five tracked layers of the composed system prompt (`base_prompt_tokens`, `timezone_instruction_tokens`, `context_preamble_tokens`, `routing_instructions_tokens`, `memory_context_tokens`, from `spawner_context.compose_prompt_digest()`) and `resume_outcome` (whether a conversational turn resumed a provider-native session: `resumed`, `resume_failed_retried_cold`, `resume_failed_terminal`, or `NULL` when resume was never attempted). The separately governed blind-spot preamble is outside this ledger schema. Both fields are additive and nullable — a caller with no composed prompt of its own (the discretion dispatcher lane) omits them and the columns stay honestly `NULL` rather than a fabricated `0`.

## Resolution Flow in the Spawner

`resolve_model_with_effective_tier(pool, butler_name, complexity)` is the catalog entry point used by the spawner. It returns a 6-tuple:

```
(runtime_type, model_id, extra_args, catalog_entry_id, timeout_s, effective_tier)
```

The `effective_tier` is pinned at initial resolution and used to scope all same-tier failover candidates for the logical session.

1. Call `resolve_model_with_effective_tier(pool, butler_name, complexity, intent=...)` to query the
   catalog, where `intent` is the dispatch intent derived from the trigger source (see *Capability
   fit* above); candidates that cannot satisfy it are excluded before ranking.
2. If found, set `resolution_source = "catalog"`.
3. If a populated receipt has no winner, return `ModelResolutionError` before adapter setup and retain the receipt on the failed result.
4. If the catalog is empty or unavailable on a live Spawner, return `ModelResolutionError` before invocation because catalog-keyed authorization, budget, breaker, and provenance gates cannot run. Pool-free direct-adapter harnesses alone may evaluate `DEFAULT_RUNTIME_TYPE`'s adapter baseline and invoke it with no explicit model under `resolution_source = "direct_runtime"`.
5. Call `check_token_quota()` for catalog-resolved models (see quota section above).
6. If quota returns `allowed=False`, record a `quota_skip` row in `public.model_dispatch_attempts` and seek the next same-tier candidate via `next_same_tier_candidate()`.
7. Invoke the selected adapter.
8. After completion, call `record_token_usage()` to update the ledger.

Both `resolution_source` and `complexity` are recorded on the session row for observability.

## Same-Tier Failover

When a catalog-resolved model fails before any side effects occur, the spawner may retry using another model from the same effective tier. The **effective tier** is the tier that produced the initial candidate and is pinned for the logical session — failover never crosses tier boundaries.

### What Makes a Model Eligible for Same-Tier Failover

The `next_same_tier_candidate()` function returns the next enabled catalog entry that meets all of these conditions:

1. **Same effective tier** — matches the tier string pinned from initial resolution.
2. **Enabled** — `effective_enabled = true` after applying per-butler overrides.
3. **Not already attempted** — the catalog entry UUID is not in the `_attempted_ids` list.
4. **Priority ordering** — sorted by effective priority descending, then `created_at ASC` (stable tie-break).
5. **Original intent fit** — the initial `DispatchResolution` recorded the candidate as selected,
   eligible, or fit-but-lower-priority in the same effective tier. A candidate excluded for vision,
   tool use, context, deadline, or budget is recorded as a non-invoked suppressed attempt and skipped.

Butler-level overrides (`public.butler_model_overrides`) are applied via COALESCE: when an override field is NULL, the catalog value is used.

### Quota-Skip Loop

Before invoking any adapter, the spawner checks `check_token_quota()` for the current candidate. If quota is exhausted:

1. A `quota_skip` row is written to `public.model_dispatch_attempts` with `outcome='quota_skip'`.
2. The skipped catalog entry ID is appended to `_attempted_ids`.
3. `next_same_tier_candidate()` is called with `_attempted_ids` to get the next eligible candidate.
4. If no candidate remains, `record_failover_exhausted(tier=...)` is emitted and the session fails.

All `quota_skip` rows share the same `logical_session_id` as subsequent attempt rows, enabling end-to-end provenance correlation even when the initial `request_id` is None (scheduler/tick triggers).

### Adapter Signals

Each runtime adapter exposes adapter-level signals in `last_process_info` that inform the failover classifier:

- **`is_pre_tool_call`** (`bool`) — `True` when the failure happened before any MCP tool was executed. Set by all adapters on non-zero exit, timeout, and certain pre-invocation errors.
- **`error_detail`** (`str`) — Adapter-extracted error detail (stderr, structured error, etc.) for classifier pattern matching.
- **`internal_retry_count`** (`int`, on `MCPToolDiscoveryError`) — Number of adapter-internal retry attempts. The spawner treats `MCPToolDiscoveryError` as **one** logical failover attempt regardless of this count.

### Failover Classification

The `classify_failover_eligibility()` function (in `failover_classifier.py`) decides whether a failed attempt may be retried:

**Eligible (default-open for these classes):**
- Missing CLI binary (`FileNotFoundError`)
- Timeout before any tool call (`TimeoutError` with no captured calls)
- Rate-limit / auth / model-unavailable / provider-unavailable (`RuntimeError` matching known markers)
- MCP discovery failure with no captured tool calls (`MCPToolDiscoveryError`)

**Suppressed (default-closed):**
- Any captured MCP tool call — world may have been touched
- Guardrail terminations (`degenerate_tool_loop`, `tool_call_budget_exceeded`, `token_budget_exceeded`)
- Unknown error classes — cannot confirm no side effect occurred
- Business / validation errors (`ValueError`, `TypeError`)

The classifier is **default-closed**: unknown failures suppress failover to protect against duplicate side effects on retry.

### Attempt Provenance

Every attempt in the failover sequence writes a row to `public.model_dispatch_attempts`:

| `outcome` | Meaning |
|---|---|
| `quota_skip` | Candidate skipped before invocation due to quota exhaustion |
| `runtime_failure` | Adapter raised a failover-eligible error |
| `resume_failure` | Provider-native resume failed safely; the same candidate is retried cold without affecting its breaker |
| `suppressed` | Failover decision was ineligible (side effects or unknown error) |
| `exhausted` | All same-tier candidates tried, none succeeded |
| `success` | This attempt produced the final successful result |

Query provenance via the API: `GET /api/dispatch/attempts?session_id=<uuid>` or directly from `public.model_dispatch_attempts`.

Each catalog-backed row also carries `resolution_receipt`, the prompt-free
explanation computed by intent-aware resolution: requested/effective intent,
the winner and tie-break reason, and the ordered candidates with exclusions
such as `breaker_open`, capability fit, budget, or quota. A same-tier failover
receipt names the preceding attempt and its classified failure. A safe resume-handle failure that
retries the same candidate cold instead records `retry.kind="same_candidate_cold"`; it is not a
model failover. Receipts are bounded to 32 KiB by retaining an ordered candidate prefix and setting
`truncated=true` plus the original `candidate_count`; they are never silently dropped for size. The
size check uses the exact registered JSONB serializer, including its default ASCII escaping, and
the minimal projection retains both requested and effective intent. Historical and explicit
pool-free direct-runtime attempts honestly expose a null receipt rather than reconstructing a
decision from current catalog state.

Qualifying `runtime_failure` and `success` rows use one serialized recorder per catalog entry. The
recorder takes the advisory transaction lock before assigning `clock_timestamp()` and the stable
bigint ID, so `(ts, id)` reflects recorder order even when an older transaction reaches the lock
late. A breaker opening and its runtime-attention episode commit in that same transaction.
Fleet-halt denials use the same recorder and create at most one episode per UTC month without a
recorder-held lock of their own, so the deny path (which fires on every spawn while the fleet is
halted) is not serialized fleet-wide.

Checking, repairing, and pausing runtime-attention paging is covered in the
[Runtime Attention runbook](../operations/runtime-attention.md).

### Metrics

Three counters track failover at the process level:

| Metric | Labels | Meaning |
|---|---|---|
| `butlers.spawner.failover_attempts_total` | `butler, from_model, to_model, reason` | Successful failover transition (primary failed, next candidate invoked) |
| `butlers.spawner.failover_suppressed_total` | `butler, reason` | Failover suppressed by classifier |
| `butlers.spawner.failover_exhausted_total` | `butler, tier` | All same-tier candidates exhausted |

`runtime_attention_recorder_total` is covered in the
[Runtime Attention runbook](../operations/runtime-attention.md#check).

## Verification

To confirm the model routing behavior described here matches the running system:

```bash
# 1. Catalog entries exist and are enabled
psql -h localhost -U butlers -d butlers -c \
  "SELECT runtime_type, model_id, complexity_tier, priority, enabled
   FROM public.model_catalog ORDER BY priority DESC, created_at;"
# Expected: at least one enabled entry per complexity tier you use

# 2. Resolution source recorded on sessions
psql -h localhost -U butlers -d butlers -c \
  "SELECT model, complexity, resolution_source, COUNT(*) as sessions
   FROM general.sessions WHERE completed_at IS NOT NULL
   GROUP BY model, complexity, resolution_source ORDER BY sessions DESC LIMIT 10;"
# Expected: resolution_source is "catalog" for live catalog-resolved sessions;
#           "direct_runtime" appears only in explicit pool-free harnesses

# 3. Token quota ledger records usage
psql -h localhost -U butlers -d butlers -c \
  "SELECT catalog_entry_id, SUM(input_tokens + output_tokens) AS total_tokens,
          MAX(recorded_at) AS last_record
   FROM public.token_usage_ledger
   WHERE recorded_at > now() - interval '24 hours'
   GROUP BY catalog_entry_id;"
# Expected: rows with non-zero totals for models used today

# 4. Failover attempts are tracked (if any occurred)
psql -h localhost -U butlers -d butlers -c \
  "SELECT outcome, failure_reason, error_code, attempt_index
   FROM public.model_dispatch_attempts ORDER BY created_at DESC LIMIT 10;"
# Expected: "success" rows for normal sessions; "quota_skip" or "runtime_failure" for failovers

# 5. Per-butler overrides apply (if configured)
psql -h localhost -U butlers -d butlers -c \
  "SELECT butler, catalog_entry_id, enabled, priority, complexity_tier
   FROM public.butler_model_overrides;"
# Expected: override rows for any butler with custom model settings;
#           NULL columns fall back to catalog defaults via COALESCE
```

## Related Pages

- [LLM CLI Spawner](spawner.md) --- where model resolution integrates into the spawn pipeline, including same-tier failover flow
- [Session Lifecycle](session-lifecycle.md) --- how resolution metadata is recorded on sessions
- [Scheduler Execution](scheduler-execution.md) --- how scheduled tasks specify complexity tiers
