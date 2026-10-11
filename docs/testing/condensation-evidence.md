# Executable condensation evidence

The evidence tools stage a survivor proof. They delete no test and grant no
retirement, migration, provider or CI authority. REQ-testing-053 governs their
admission; the ordinary test ladder and full terminal merge-group remain binding.

Run a proof from the owning checkout after committing the candidate source:

```bash
uv run --no-sync python scripts/condense_evidence.py prove \
  --config path/to/cluster-plan.json --output .tmp/condensation-proof/unique-run
uv run --no-sync python scripts/check_condensation_ledger.py \
  --base <exact-before-commit> --ledger .tmp/condensation-proof/unique-run/ledger.json
```

`make condense-prove CLUSTER=<plan-path> OUTPUT=<fresh-directory>` and
`make condense-verify CONDENSATION_BASE=<exact-base> OUTPUT=<directory>`
invoke these same producers and consumers. `make check-condensation-ledger`
and the required CI guard invoke the same complete source scan. No second
scanner or selector is introduced.

A plan supplies `base`, production Python `scope`, complete static `removed`
and `survivors` owners, `cluster`, `bead`, protected `contract:{class,cites}`, and
`mapping:{removed-owner:{survivors,reason}}`. Static owners have no parameter
suffix. The actual pytest collection records every parameter case as a fresh
opaque key; raw parameter identities and child error operands stay in RAM.
Empty, skipped, deselected, incomplete or changed populations cannot pass.

The removed population runs in the full exact Git base. Survivors run in a
separate full current-source copy. Production scope must have identical bytes
and modes. Changed implementations or Python/config dependencies need independent lineage
proof and cannot use the current same-source producer. The two proof-tool files
are current-bound execution machinery; their introduction grants no production
dependency exemption. Ordinary tests retain their coverage core,
markers and parallel defaults; only the isolated proof uses ctrace and `-n 0`.
Its finite total budget defaults to60s and may be explicitly set up to300s. These
proof times are not routine-lane or historical condensation savings.

A protected PASS requires nonempty actual branch arcs, zero residue, no waived
normative arcs, and every removed owner's semantic mutant kill retained by one
of its named survivors. Coverage is compared for each removed owner against
only its named executing survivors. An unrelated selected owner cannot supply
a missing branch, even when aggregate coverage and named mutation kills agree. Collection/setup/teardown errors, non-assertion failures,
timeouts and uncertain cleanup are UNKNOWN. Each mutation is restored byte for
byte and mode for mode before another mutation. The producer retains baseline,
mutant and restored phase records and the actual CoverageData SQLite files. The
stdlib consumer reads literal exact SQLite contexts and recomputes residue and
named kills; JSON counts or PASS labels alone cannot replace those carriers.

Evidence binds the exact base/head/tree, complete tracked current inputs, actual installed coverage/pytest/plugin source fingerprints, tool
and config bodies, selection, command predicates and fresh nonce. Store proof
outputs outside Git in a fresh ignored directory. Committing a ledger changes
its source identity; regenerate proof after the final commit. This first
implementation refuses changed heads rather than inferring protected-union
compatibility. A later verified reuse path must preserve dated proof identity
and all relevant inputs; event names and cached counts grant no exemption.

Tracked links are retained as Git mode120000 plus the hash and exact body of
the link, including directory links. Copies write regular objects first and
then recreate links without dereferencing them. Every link must resolve inside
the complete tracked source closure. Absolute, escaping, dangling, cyclic or
untracked targets refuse before execution. Changed links need independent
lineage proof; a link cannot be a mutation scope or an indirect source parent.
These checks preserve ordinary in-repository aliases without using host files
as undeclared proof inputs.

The stdlib source consumer examines `tests/` and `roster/*/tests/`, including
assertions within retained names and their execution structure, parameters, local fixture/helper/marker
context, class inheritance and ancestral conftests. Renames, moves and deleted
production files do not automatically allow protected test loss. A complete
executing survivor account remains required. An empty ledger population is
healthy only when the actual complete source scan finds no losses.

## Retained owners with additive changes

Unchanged names or assertion totals alone do not admit an additive change.
A retained owner may appear on both sides of an executing proof when every old
assertion, statement, control structure, decorator, marker and mode is retained.
Old assertions and abrupt exits remain literal at the same nesting. Added abrupt
exits, wrapped old assertions, changed existing defaults or replaced statements
refuse. Helper additions retain every old statement in order; optional helper
keyword probes are restricted to literal boolean/None defaults and literal probe
keywords on an otherwise unchanged call.

Added imports retain every original binding and origin in order. A new alias
cannot shadow any original lexical name; wildcard changes and rebinding refuse.
Appended test execution and appended fixture arguments may qualify an account
when old statements and arguments remain intact. Existing defaults cannot change.
These structural conditions only qualify fresh execution. They grant no import,
fixture, runtime or zero-ledger exemption. Both complete before/current opaque
parameter populations, actual branches, named semantic kills and every restored
setup/call/teardown must still agree.

Use complete impacted static owners in both `removed` and `survivors`, mapping
each overlapping owner only to itself. Here `removed` means old execution, not
permission to remove that test. Account for every affected owner. A manual fresh
proof uses the ordinary `prove` command with an exact before commit, followed by
`--ledger` on the same required consumer.

For the actual default CI invocation, track a strict JSON execution request under
`tests/condensation-plans/`. Use the same plan fields with `"base":"event-base"`.
The request's `scope`, owners, mapping and governing citations are source-owned;
it contains no claimed PASS, nonce or historical coverage. The same consumer
first validates the actual CI union/head and resolves this request to that event's
exact before commit, then invokes the actual producer in fresh owned copies.
It reads the resulting SQLite/run carriers through its ordinary strict admission.
Every run uses a new ignored `.tmp/condensation-ci/run-*` directory. That directory
is retained for failures and recovery and is never searched for cached accounts.
Malformed requests, incomplete proof or uncovered losses refuse. A request runs
only when its declared old owners intersect actual losses at that exact base;
retained unaffected owners in that request still execute and preserve their full
population. Later unrelated changes do not rerun an obsolete proof or treat its
request as an admission.
A new public base causes new execution, not rebinding of an older ledger.

For example, a retained imported owner uses this mapping within the existing full
plan format:

```json
{
  "base": "event-base",
  "removed": ["tests/test_owner.py::test_owner"],
  "survivors": ["tests/test_owner.py::test_owner"],
  "mapping": {
    "tests/test_owner.py::test_owner": {
      "survivors": ["tests/test_owner.py::test_owner"],
      "reason": "Complete original owner and bindings retained"
    }
  }
}
```

The snippet omits required `scope`, `cluster`, `bead` and protected `contract`
fields; it is not an executable complete request. Each proof keeps its existing
finite60s budget. Multiple requests execute sequentially and retain their own
carriers; no selector, CI predicate, general time cap or ordinary fork mode changes.

Changed production/runtime or config dependencies still require independent
lineage proof and cannot use this same-source route. This includes the dated
backend805 comparison: its31 import changes, two execution extensions and two
fixture additions are classifications, not proven preservation of concurrent
production changes. Real migrated roles and other protected runtime evidence
retain their ordinary requirements. A plan cannot make those changes context-only.

## Recovery and delivery

Every proof begins with a durable UNKNOWN receipt and owns separate full source
copies, a journal and launch records. The child cannot enter pytest until its
process identity is durably recorded. Ordinary failure, timeout and cancellation
use the delivered R1 group-completion admission. A killed writer cannot declare
PASS. Once its processes have settled, supported scoped recovery restores only
the recorded owned copies:

```bash
uv run --no-sync python scripts/condense_evidence.py recover .tmp/condensation-proof/unique-run
```

Recovery refuses a live owner, reused/live process identity, unknown group exit,
indirect paths or changed journal bytes. It signals no later numeric PID. A
recovered receipt stays UNKNOWN and requires a fresh proof. Concurrent runs do
not share output directories or source copies. No tool changes the live tests,
common hook configuration, unrelated processes or environment.

Catalog and attested deletion modes are refused by this producer/consumer.
Real migrated PostgreSQL roles, constraints, indexes, defaults, functions, RLS,
grants, bootstrap, rollback and protected negative companions require their
ordinary genuine runtime evidence. SQL text or synthetic snapshots cannot
substitute for it.

Genuine native preparation and ROOT release authorize the required guard.
CI binds the exact pull-request union, merge-group head or main-push before/after
commits. Missing/unsupported identities refuse; no event can use HEAD as its
own before baseline. The current CI invocation supplies no historical ledgers. Current tracked
execution requests generate fresh exact-event carriers through this same consumer;
a cluster change must provide its complete request and named mapping. Missing carriers refuse every observed
loss; zero-loss source changes need no invented ledger. Supported baseline
administration, archive and earned source task marks still need their separate
ROOT release after actual source qualification. Fresh resulting-head review,
normal CI, protected merge-group and actual squash remain mandatory. The first
three real clusters, dogfood, shared-app isolation, nine folded outcomes and
all original timings/survivors remain unfinished by this tooling packet.
