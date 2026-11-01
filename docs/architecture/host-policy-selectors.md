# Reviewed host-policy selector inventory

This is the owner-approved S3 inventory-only slice of `bu-m0vjeb.5`. S1 startup phases were
already delivered in #4931. S2 init-job movement and S4 formal module hooks remain HOLD; the
original task is not complete. This slice changes no production selector, runtime configuration,
identity, permission, schema, module hook or existing runtime test. It claims no reduction or
safe configurability. Source counts and collection do not establish runtime correctness.

The reviewed data is [scripts/host-policy-selectors.json](../../scripts/host-policy-selectors.json).
Each current row records its enclosing symbol, normalized expression, occurrence, semantic
category, purpose, governing requirement, actual public line-introducing commit and historical
lineage. Source line numbers are informational. The source is public
`773d1d81b8112a951776db56241eaf6988717263`; this inventory introduces no source change.

## Consistent counts

The roster vocabulary is the 13 `roster/*/butler.toml` parent directory names. Each `ast.Compare`
counts once, including chained/nested comparisons separately when they are separate AST nodes.
A **direct** match has at least one operand that is itself an exact roster string literal.
A **recursive** match has such a literal anywhere below an operand, including tuples, sets,
subscripts and call arguments. Recursive totals include direct matches. These definitions
reproduce the historical `d223c4f28012220bdfbfa805857b81fd6dc0388b` baseline: 59 direct
comparisons in 30 files, 63 recursively matched comparisons. The 59 original semantic labels
are retained from the reviewed historical TSV (SHA256
`5772327e04dce19ae15768a599be6e1e8d22d961d0dc1dc9f96bf0274fc8da62`).

| Definition | Historical d223 | Current public |
| --- | ---: | ---: |
| Direct Compare nodes | 59 | 76 |
| Files containing direct nodes | 30 | 34 |
| Recursive Compare nodes | 63 | 85 |
| Supplementary sites, counted separately | 28 | 30 |
| Total explicitly inventoried sites | 91 | 115 |

The following category counts use the **same direct definition** on both trees. In particular,
unrelated spellings and protected identity checks remain in the total; they are not described
as eliminated host branches.

| Semantic category | Historical direct | Current direct | Historical all 91 | Current all 115 |
| --- | ---: | ---: | ---: | ---: |
| Canonical identity, authority or provenance | 21 | 34 | 31 | 48 |
| Protected host/domain contract | 16 | 19 | 20 | 24 |
| Schema ownership | 7 | 7 | 7 | 8 |
| Historical/availability | 3 | 3 | 6 | 6 |
| Unrelated enum, module, field or state spelling | 5 | 5 | 19 | 20 |
| Unresolved policy candidates | 7 | 8 | 8 | 9 |
| Evidenced independent host policy | 0 | 0 | 0 | 0 |

`EVIDENCED_HOST_POLICY` means a positive case for host behavior independent of a protected
identity/ownership/contract check. None is established here. Zero evidenced entries is not a
claim that no host policy exists: the unresolved set remains included. The guard freezes this
evidenced baseline at zero; it does not count unresolved sites as safely configurable.

## Historical reconciliation

All historical sites retain explicit lineage. Exactly one is retired: #5050 (`04a9edf85`)
removed `_create_audit_pool`'s Switchboard branch when audit switched to the butler's own
verified-role pool. #5017 (`30b376895`) moved the retained Switchboard QA-client guard from
`_wire_module_runtime` into `_qa_switchboard_client` and moved the two canonical QA availability
checks from retired self-healing into `core.qa.relay`. Those moved guards remain inventoried;
they were not eliminated by this slice. Other unchanged expressions retain identities across
line shifts, including both separate spawner occurrences.

The 23 recursive matches without historical lineage are existing public contributions:

- #5011 (`3f32ef0172`): one fixed Chronicler continuation identity.
- #4986 (`74896a6afe`): three General calendar-producer provenance checks.
- #5083 (`b4df6d030f`): two authenticated Switchboard confirm-principal checks.
- #5025 (`a8e9d91a92`): nine Finance warning identity/catalog/registration bindings and four
  daemon transport-host bindings (nine direct and four recursive-only sites together).
- #5071 (`7156078027`): one Switchboard-local scheduler policy reader.
- #5044 (`497b58aaa1`): one recursive-only primary/owned-memory schema delegation pair.
- #5007 (`b65d6c5ba5`): one canonical Relationship model import identity.
- #5119 (`f33fd63bc4`): one Chronicler scheduled day-close memory-sink privacy gate,
  retained as an additional unresolved policy candidate. The subsequent public location
  reconciliation at `773d1d81b8` introduces no additional selector.

Together with one legitimate retirement, these explain recursive 63 -> 85. Direct 59 -> 76
uses the same rows and definition. Supplementary growth is +2: the Chronicler continuation
registry lookup (#5011) and the owner home-place label comparison in the OwnTracks adapter
(`0f5586470d`). The latter is an unrelated place-label spelling, not a Home daemon selector.
The historical Calendar response-owner aliases remain covered on both trees; no supplementary
site is silently dropped. Their actual lineage is recorded in the data. The individual row identities, purposes and source commits are the review surface,
not a claim that labels alone prove safe conversion.

## Unresolved candidates

The original seven candidates remain unresolved. Their callers/effects and protected behavior
must be established before any typed configuration replacement. No current category authorizes
a conversion.

| Historical location | Current enclosing symbol | Why unresolved |
| --- | --- | --- |
| `src/butlers/config.py:932` | `load_config` | Resolve default Switchboard URL for other butlers; missing/invalid/default equivalence of a replacement is unproven. |
| `src/butlers/core/fleet_conditions.py:497` | `controller_after_cycle` | Select QA patrol contract for the fleet controller; identity/policy dependencies remain unresolved. |
| `src/butlers/core/spawner.py:2360` | `Spawner._run` | Switchboard-specific spawner prompt/runtime path; caller, effect and privacy preservation for configurability remain unresolved. |
| `src/butlers/core/spawner.py:2465` | `Spawner._run` | Switchboard-specific spawner prompt/runtime path; caller, effect and privacy preservation for configurability remain unresolved. |
| `src/butlers/daemon.py:1130` | `ButlerDaemon._build_scheduler_runtime_context` | Bind Chronicler day-close prompt and completion hooks, including schema-local write-back; safe configuration is unresolved. |
| `src/butlers/mcp_wrappers.py:51` | `_manual_day_close_tool_policy` | Manual day-close suppresses other Chronicler tools to protect no-write/no-send behavior; safe typed replacement is unproven. |
| `src/butlers/scheduled_jobs.py:2121` | `_retention_pruner_job_handlers` | Select General-only retention-pruner handlers; typed representation and behavior preservation remain unresolved. |

The expanded alias coverage also retains `_ordered_configs`' Switchboard-first priority as an
**additional unresolved** candidate on both trees. Its deployment-boot pool dependency is not a
proven safely configurable policy. Public #5119 adds another unresolved candidate at
`src/butlers/core/spawner.py:3395`, in `Spawner._run`: the canonical Chronicler identity
and exact scheduled day-close trigger select delivery-status filtering before automatic episode
storage. [Owner-Day Narrative Composition](../../openspec/specs/butler-chronicler/spec.md)
requires this memory-sink exclusion while preserving audit and legitimate retrospective
interactions. Its caller/effect/privacy preservation under configuration remains unproven.
Thus the full inventory has nine unresolved sites: the original seven direct candidates,
the additional CLI priority candidate and this new public direct candidate. No candidate
silently disappears through a constant.

## Enforcement and static limits

Run `make check-host-policy-selectors` or `python3 scripts/check_host_policy_selectors.py`.
`make check-guards` and the mandatory hosted `guards` job both run this command. The hosted
finalizer includes its outcome, so failure, cancellation or a skipped check cannot pass.
All existing guards, job bounds and protected checks remain in place.

The scanner additionally inventories simple lexical assignment aliases (including literal
containers and string concatenation), comparisons containing `required_name`, and roster-name
subscripts / `.get`, `.pop` and `.setdefault` lookups. Current supplementary coverage is 22
alias comparisons, one dynamic comparison and seven structural lookups. Aliases are conservative
and flow-insensitive; recursive historical counting does not borrow their extra matches.
The dynamic core-group warning is protected by the independent canonical identity gates in
[Core Tool Surface](../../openspec/specs/core-daemon/spec.md). Channel egress remains bound by
[Channel Egress Ownership Enforcement](../../openspec/specs/core-modules/spec.md), and recovery
issuer/schema checks remain bound by
[authenticated provider handoff](../../openspec/specs/approval-delivery-intent-recovery/spec.md).
Git identity and runtime tuning retain [doctrine rule 5](../../about/heart-and-soul/vision.md).

Unknown discovered selectors fail; missing reviewed sites fail. Identity includes path,
enclosing symbol, expression, kind and duplicate occurrence, so relocating a selector to a
wrapper or another file does not erase custody. All historical records need a current lineage
or an explicit retirement reason. Future inventory loss/reclassification is compared against
`origin/main` and requires an explicit `review_changes` reason; original policy candidates
cannot be reclassified by merely editing their current label. Inventory reasons require review:
the guard cannot prove that a prose assertion is true. There is no update/refreeze option.

This is not whole-Python semantic analysis. Imported aliases, arbitrary dynamic calls,
reflection/getattr results, runtime-computed names, structural lookups with unknown keys, SQL
strings, pattern matching and policy tables outside the supported AST forms may require manual
review. It does not prove dominance, side effects, caller authentication, authorization, RLS,
concurrency, retries, no-send/privacy or source-to-runtime equality. Do not evade a failed
inventory by moving code into an unsupported form, changing names or relabeling protected checks.
Any conversion still needs the original preservation matrix and contract/owner review.

The owning contract test exercises actual scanning and review validation, then positive and
negative source controls for new unclassified sites, explicit host-policy growth, removal,
relocation, aliases/getattr, unknown inventory loss and unresolved reclassification. Existing
CI contract checks execute the real finalizer with each required outcome failing/skipped.
Production source remains byte-identical; runtime survivors are preserved, not replayed or
represented as conversion validation. Different-author review, exact-head hosted broad checks,
protected queue checks and a confirmed merge remain necessary. `make check` and the original
whole-task acceptance are not claimed complete by scoped local static/software evidence.
