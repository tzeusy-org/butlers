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

Within one admission, the consumer reuses current test shapes only after a
fresh read returns exactly the same path and source text. The reuse is local
to that invocation; it does not cache filesystem or Git identity. Changed and
new files are still parsed, classification owns separate mode metadata, and
the final body, mode, link, tracked-path, HEAD and tree checks still run. This
removes repeated AST construction without changing which losses need evidence.

A plan supplies `base`, production Python `scope`, complete static `removed`
and `survivors` owners, `cluster`, `bead`, protected `contract:{class,cites}`, and
`mapping:{removed-owner:{survivors,reason}}`. Static owners have no parameter
suffix. The actual pytest collection records every parameter case as a fresh
opaque key; raw parameter identities and child error operands stay in RAM.
Empty, skipped, deselected, incomplete or changed populations cannot pass.

The removed population runs in the full exact Git base. Survivors run in a
separate full current-source copy. The default route requires identical production bytes
and modes. Changed implementations or Python dependencies need the explicit
paired route below. Changed installed environments or links still refuse. The proof-tool files
are current-bound execution machinery; their introduction grants no production
dependency exemption. Ordinary tests retain their coverage core,
markers and parallel defaults; only the isolated proof uses ctrace and `-n 0`.
Coverage's isolated path resolver re-resolves each file's canonical parent on
every call, including during subject execution. It retains no directory result
across calls: a subject may temporarily retarget a directory symlink and restore
it before source readback. Actual SQLite arcs must retain both executed targets;
unchanged final source records alone cannot establish that population.
Its finite total budget defaults to60s and may be explicitly set up to300s. These
proof times are not routine-lane or historical condensation savings.
Within each baseline, mutant or restored phase, the two source sides execute
concurrently in their separate owned copies. Each side still creates its own
request, CoverageData SQLite file and process group; every phase executes anew.
Both groups settle before mutation restoration or the next phase. An interruption
or failed side cancels the other owned run and retains the existing cleanup and
UNKNOWN requirements, within the same total budget. Completion from either side
is observed promptly; successful carriers retain removed-then-survivors order
regardless of completion order.
Sibling cancellation retains an observed terminal child status while still
stopping running children within the existing finite wait quantum.

Every ordinary phase overrides inherited `TMPDIR` and `PYTEST_DEBUG_TEMPROOT`
with its side's fresh real `removed.runtime-tmp` or `survivors.runtime-tmp`
directory directly beneath the canonical fresh proof output. Root creation and
every launch remain inside the same absolute proof deadline. Direct `suite`
callers keep their existing environment behavior; contract evolution retains its
existing owned temporary placement. No phase falls back to inherited roots or
cleans host pytest trees. The two runtime directories remain with the proof
carriers for independent consumption; source copies are restored and removed.

Each original `<id>.request.json` has exactly the two string environment fields
`TMPDIR` and `PYTEST_DEBUG_TEMPROOT`. Every actual ordinary worker run adds the
required closed `runtime_environment` record with those fields and the observed
`tempfile_root`; all three equal the independently derived side directory.
A separate closed `runtime_directory:{device,inode}` record in the original
request and worker run binds the fresh directory's actual filesystem identity.
The producer rechecks identity before every launch. The reader rejects missing,
replaced, indirect, shared or escaping roots, compares current identity with the
original request and saved run, and reconstructs the complete request from the
binding, side, selection, scope, nonce, output and protected control inputs. Its
source-copy root comes from the retained restored journal, including its exact
mutation and batch account, rather than trusting a supplied measurement path.
Original requests must be real direct sibling files. Rewriting a ledger and its
matching run JSON/digest cannot replace this independent original request check.

Old carriers without these required records need fresh execution. Directory
identity detects replacement while original request evidence remains intact;
these local unsigned files provide no cryptographic guarantee if all original
evidence is rewritten. This ownership boundary does not sandbox arbitrary host
access by subjects. Existing protected-request observation, complete SQLite
contexts, source binding, settlement and UNKNOWN rules remain mandatory.

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

Whole production files can generate more than 64 finite mutations. The producer
partitions the complete deterministic population into consecutive batches of at
most 64, journaling each batch and restoring every mutation before continuing.
The ledger carries complete original scope bodies with source-bound hashes. The
reader runs the same source-owned generator against those bodies, verifies every
operation and exact ordered batch, and requires all mutation and restoration
phases. Omitting a source, batch or mutation, changing its operation, duplicating
a batch or leaving one UNKNOWN refuses the entire account. A completed batch
provides no partial admission and cannot be resumed or reused as current proof.

Batch boundaries do not renew the one absolute proof deadline. The ordinary
producer still defaults to 60 seconds, with the existing bounded diagnostic
timeout option and owned cleanup. A complete large owner that exceeds that
deadline remains UNKNOWN; batching proves no router timing, deletion safety or
wall-clock gain by itself. All named survivors, protected arcs and kills must
still qualify before any original test can be removed.

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
context, class inheritance and ancestral conftests. An edited, deleted or newly added
`conftest.py` is context change for every owner below it
(`ancestral-fixture-context-change`). A conftest whose module, before or after, holds
anything beyond imports, docstrings, plain constant assignments and `@pytest.fixture`
functions (any `pytest_*` hook, `pytest_plugins`, `collect_*`, module-level calls,
conditionals, undecorated helpers, unparseable source) can change co-collected tests
anywhere in the tree, so its change is attributed to every owner
(`session-hook-context-change`), whatever directory it lives in. A fixture counts only
when the decorator is `pytest.fixture` or `fixture` bound by the module's own
`import pytest` / `from pytest import fixture`, its decorator arguments and parameter
defaults are literals, and its annotations only name things, because Python evaluates all
of these at import; a rebinding of those names is a hook too. Import-time code in other
modules (a helper a conftest imports, a test module) is not covered by this classifier
(`bu-qwcgvu`). An added, edited or deleted pytest configuration (`pytest.toml`,
`.pytest.toml`, `pytest.ini`, `.pytest.ini`, `tox.ini` with `[pytest]`,
`setup.cfg` with `[tool:pytest]`, or a `pyproject.toml` `[tool.pytest]` table) is likewise
attributed to every owner (`pytest-configuration-change`); a symlinked one is refused
(`indirect-pytest-configuration`). Renames, moves and deleted
production files do not automatically allow protected test loss. A complete
executing survivor account remains required. An empty ledger population is
healthy only when the actual complete source scan finds no losses.

## Frontend test-case deletions

The Python definition scan above sees pytest definitions only. The same
`check_condensation_ledger.py` entry point (so `make check-condensation-ledger` and the
required CI guard) therefore also runs `scripts/frontend_test_deletions.py` after the Python
verdict. It derives case ids `<path>::<describe> > <it>` for every
`frontend/**/*.{test,spec}.{ts,tsx,mjs}` file at the exact base and in the working tree. An id
whose live count dropped is a loss: a deleted case, a deleted file, a `.skip` or `.todo`, or a
commented-out case. Added cases and untouched files never need evidence.

A loss is excused only by a mapping file that the same change adds under
`tests/frontend-test-deletions/`:

```json
{"deleted": {"<lost id>": {"successors": ["<live id>"], "reason": "why the successor covers it"}}}
```

Every lost id needs an entry, every entry must be a real loss, and each successor must be a
live id that is not itself lost. Mapping files already on the base excuse nothing, so a later
deletion is never hidden by an old entry. The Python ledger result and its output are
unchanged; a frontend refusal prints `{"status": "REFUSED", "category": ...}` and exits 1.
Limits: the scanner is a small lexer, not a TypeScript parser; a case with a non-literal title
is counted as `<dynamic>`, and a row removed from an `.each` table is not seen (the id is the
title), so table edits stay a review item. This does not run any frontend test.

## Independently authorized contract evolution

On 2026-10-15 the repository owner answered **Yes** to the narrow policy amendment
allowing intentional governing-contract changes to replace obsolete test
expectations. That decision authorizes this admission mechanism, not arbitrary
test loss. Genuine condensation continues to require the branch/mutation survivor
proof above. This first evolution mode retains the same static owners and every
opaque collected case identity; it admits no deletion, rename, reduced case
population, environment waiver or substitute for migration/runtime evidence.

Authorization and execution are separate:

1. An independent review identifies the precise normative amendment, obsolete
   expectations, replacement enforcement and preserved obligations. Its strict
   `contract-evolution-authorization/v1` manifest lands in a separate protected
   main PR under `tests/contract-evolution-authorizations/` before the candidate
   can consume it. The owner/date/reference fields record provenance; their text
   does not itself grant authority.
2. The required consumer reads that manifest from the trusted public source,
   never from the candidate's copy. For a provider PR union this is its validated
   public first parent; for merge-group it is the exact event base. The original
   event comparison base still governs the complete loss scan and old execution,
   even if the PR's public parent is newer. A candidate-only authorization refuses.
3. A candidate request selects that authorization and causes new execution in
   separate full before/current source snapshots. The existing owned runner,
   complete collection and raw per-case phase/coverage carriers remain the
   evidence boundary. Every named owner and exact opaque case key must appear
   on both sides with successful setup, call and teardown. Skips, deselection,
   failed or missing phases, timeout and uncertain cleanup refuse. A finite
   proof deadline, at most 300 seconds, includes both sides and owned cleanup.
4. The same complete source-loss consumer checks fresh source, installed tools,
   config, selection, nonce, carriers and final source stability. Exact reviewed
   input drift refuses; unrelated unchanged public history is not a waiver or
   an artificial whole-head authorization lock. Historical requests do nothing
   when their owners have no current loss. Every unlisted loss still needs its
   ordinary survivor account.

The manifest binds complete old/new test files, their helper/import context,
ancestral conftests, and explicitly enumerated runtime/fixture inputs by Git
mode and SHA-256 of exact bytes. Owner/context files must exist on both sides.
`pyproject.toml` and `uv.lock` must remain identical across the two execution
sides. A changed installed environment needs a separate real proof; this mode
does not certify it. Relevant normative requirements are bound separately by
path, qualified ID and exact before/after block hashes. Each block starts at its
level-three `Requirement` heading and ends at the next heading of level three
or higher, outside fenced code. Both hashes must differ. An existing citation or
the policy requirement alone cannot authorize an unrelated expectation change.

Every candidate change outside inherited public source must have an explicit
account: exact approved input records, including proof machinery and request
bytes, or reviewed requirement blocks with unchanged surrounding public text.
There is no machinery, request, Markdown, JSON, test-file or directory exemption.
A candidate authorization must equal the trusted public bytes; its own input
list cannot authorize a replacement of itself. A request cannot select extra
owners, rebind old evidence or extend the reviewed contract. Fresh passing suites establish
actual execution; the independent amendment review establishes which semantic
changes are intended. Neither replaces the other, or the ordinary final hosted
and protected merge gates.

This mode approves all candidate input changes instead of claiming that subjects
cannot observe excluded tooling. Ordinary pytest discovery may inspect approved
source metadata. That grants no unreviewed input or evidence reuse: the trusted
manifest, exact source/tool records and fresh raw carriers remain mandatory.
The execution supervisor must still establish bounded cleanup of every owned
descendant, including nested sessions, on success, failure and interruption.
The ordinary condensation protocol and its excluded-request observer are unchanged.

The CLI prints the category of the first refusal, for example
`{"status": "REFUSED", "category": "missing-protected-contract-authority"}`. Manifest
and request structure categories include:

- Authority lookup: `missing-protected-contract-authority` (no manifest at the
  trusted ref) and `changed-protected-contract-authority` (the candidate's copy
  differs from the trusted bytes).
- Identity and review record: `contract-request-identity`,
  `contract-request-population-or-bound`, `contract-authority-identity` and
  `missing-contract-review-record`.
- Owner population: `incomplete-authorized-owner-population`,
  `contract-evolution-is-retained-owner-only` and `missing-retained-contract-owner`.
- Contract blocks: `ambiguous-or-self-authorizing-contract`,
  `stale-or-unchanged-authorized-contract` and `unmapped-changed-contract`.
- Input records: `stale-authorized-source-or-context` and
  `unapproved-contract-runtime-input`.

Proof execution has its own categories (for example `nonpass-contract-case` and
`contract-proof-timeout`) that are not listed here. Run as a script, the CLI keeps
these categories only because the script's `if __name__ == "__main__":` block aliases
`__main__` as `check_condensation_ledger` in `sys.modules` before `main()` runs and the
consumer imports it; a subprocess regression test pins
that.

### CI speed authorization, 2026-10-15

The first scoped authorization is
[`bu-q1hzqn-ci-speed-20261015.json`](../../tests/contract-evolution-authorizations/bu-q1hzqn-ci-speed-20261015.json),
owned by bead `bu-q1hzqn.10` for the implementation in PR #4530. It records the
owner's approval above and a separately reviewed source mapping. It is inert
until an executor satisfies this protocol; landing the manifest is neither a
test PASS nor permission to merge the candidate without fresh evidence.

The reviewed before source is
`d276a32a1f6e5f44c11ef8e0b4eb1d2d8200f20c`; the approved subject is
`2560ef2caf90ef95c2289498fafebfa9c63c7ddd`. These are review provenance, not a
whole-head reuse exemption. The manifest contains five retained owners, nine
changed requirement blocks and twenty-four exact input records. The original
eighteen subject inputs remain unchanged; six additional machinery, owning-test
and request records bind the reviewed execution source
`1bec9aa09b200aafa776fe62172c88f6932040f4`. The authorization covers
REQ-ci-shard-assurance-001/002/004/006/007/008 and REQ-testing-034/049; the
REQ-testing-034 amendment is bound in both its baseline and active change file.
Independent review corrected the final DAG account to twenty expanded full-mode
rows and twenty-one declared rows including the mutually exclusive affected row;
the existing peak-fourteen and twenty-one-row test assertions were preserved.

The review distinguishes these five retained owners and their obligations:

| Retained owner | Intentional replacement and surviving enforcement |
| --- | --- |
| `test_ci_gate_reads_every_needed_verdict_without_counting_preflight_as_a_shard` | Inventory becomes a separate prerequisite. Full/main-push modes require it; scoped/docs modes require its explicit skip. The actual shell and Python verdict reader retain malformed, missing, failed, skipped, cancelled and unknown prerequisite controls, and gain an inventory fault control. |
| `test_ci_workflow_shards_full_lanes_without_coverage_or_privacy_drift` | Full tests await fresh inventory while guards remain mandatory at fan-in; affected and Vitest may start after route. All five unit and six integration children retain their ordinary evidence. Raw covered execution and reporting move to the owned assurance workflow, with eleven distinct coverage inputs, required reconciliation/smoke, finite caps and restricted badge publication. Browser still waits for the guards-built artifact. |
| `test_ci_coverage_report_rejects_any_bad_input_before_publication` | The actual report command is read from the assurance workflow. Existing corrupt, empty, missing, extra and wrong-identity raw CoverageData controls remain; new genuine traced inputs validate scheduled/manual/PR event identity and reject borrowed merge-group metadata and unsupported push reporting. |
| `test_partition_preserves_fresh_membership_with_unknown_weights` | Advisory weight fixtures move from schema v1 to tracer-specific v2 profiles. Missing, malformed, stale and duplicate-key advisory inputs still degrade deterministically; real fresh inventory, unchanged budgets, exact whole-file assignment and strict identity admission remain authoritative. |
| `test_reconciliation_requires_complete_node_identity` | The candidate is read from its actual tracer profile. Original complete identity, origin, artifact, ambiguity and missing-input controls remain; untraced costs gain their own genuine profile and absent tracer evidence refuses. |

Both complete test files are bound, including `_corpus`, `_weights` and the new
`test_owned_coverage_workflow_retains_complete_assurance` companion. That
companion checks the scheduled/manual/pipeline-PR triggers, actual eleven matrix
rows and commands, sanitized execution artifacts, fresh inventory, prerequisite
failure controls, complete reconciliation, smoke evidence and main-only badge
publication. Retaining obsolete queue-coverage literals would contradict the
approved cadence change; their replacement obligations live in the actual
covered workflow and these executing controls. No assertion count or manifest
hash substitutes for the fresh old/new runs.

## Governing retirement

REQ-testing-053 admits the deletion or rename-away of a test owner only with body
and case continuity "or genuine governing retirement authority with a protected
survivor account". Contract evolution cannot do this (it retains every owner and
case), and a retired expectation has no honest survivor, so the required consumer
has a third account: `governing-retirement-authorization/v1`. It follows the same
trust shape as contract evolution, by the owner's 2026-10-15 answer.

A manifest lives under `tests/contract-evolution-authorizations/` and lands in a
separate protected main PR before any candidate consumes it. Fields:

- `schema`, `id` (the file stem), `bead`, `authority`: provenance text only; it
  grants nothing by itself.
- `retired`: `{owner, base_sha256, reason}` rows. `owner` is `path::qualname`.
  `base_sha256` hashes the owner's own source (decorators included, nothing else
  in the file) at the event base, so unrelated edits do not stale the manifest.
- `requirements`: `{path, requirement, before_sha256, after_sha256?}` rows binding
  the governing spec block, keyed by its `ID:` or, when it has none, by its exact
  `### Requirement:` heading text. Each block must hash to `before_sha256` at the
  event base and must differ or be absent at HEAD; when `after_sha256` is given it
  must match exactly (optional so a manifest can land before its product PR).
- `replacements` (optional): owners that must exist in the candidate. Ordinary CI
  runs them; this mode never executes retired owners.

`retirement_accounts` in `scripts/contract_evolution.py` runs after ledger clusters
and contract evolution. It reads manifests only from the trusted ref and requires
the candidate copy to equal the trusted bytes. Every owner a manifest names must be
a whole-owner loss (`removed-or-moved`); anything else, and any unlisted loss, keeps
its ordinary survivor or contract-evolution account. A manifest whose owners have no
current loss is inert. A healthy admission reports `retirement_accounts` (manifest
id, trusted sha256, admitted owner count, base and public authority).

Refusal categories: `candidate-only-retirement-authority`,
`changed-protected-retirement-authority`, `retired-owner-not-wholly-removed`,
`stale-retired-owner-hash`, `stale-retirement-requirement`,
`unchanged-retirement-requirement`, `mismatched-retirement-requirement`,
`missing-retirement-replacement`, `duplicate-retirement-owner-account`, and the
generic `unproven-test-or-assertion-loss` for everything unlisted.

A trusted manifest (one on main) that claims the retirement schema but is malformed
refuses when `retirement_accounts` consults it, which only happens while unadmitted
losses remain. Structural categories include `retirement-schema-fields` (missing or
extra top-level fields), `retirement-authority-identity` (the `id` is not the file
stem, an identity field is empty or mistyped, or `retired` or `requirements` is empty
or not a list), `retirement-owner-record` (a `retired` row or replacement is not a
`path::qualname` with a hex `base_sha256`, or an owner repeats) and
`retirement-requirement-record` (a `requirements` row has missing, extra or non-hex
fields). A `retired` row with the wrong keys refuses with `schema-fields`. A malformed
manifest that only the candidate adds is currently ignored rather than refused
(follow-up bu-qzyxoy).

The first authorization is
[`bu-ys9hdx-general-steam-retirement-20261018.json`](../../tests/contract-evolution-authorizations/bu-ys9hdx-general-steam-retirement-20261018.json):
General no longer loads Steam (owner decision 2026-10-14, epic bu-ys9hdx; bead
bu-ys9hdx.7, PR #4514). It retires `test_general_butler_toml_has_steam_module` and
`test_general_steam_config_is_empty_dict` of `TestButlerTomlSteamConfig` in
`roster/lifestyle/tests/test_lifestyle_steam_integration.py`, bound to the
"General Butler Identity and Runtime" block of `openspec/specs/butler-general/spec.md`,
and requires the two replacement owners that PR adds.

## Authority that has already landed

A pull request's event base (`github.event.pull_request.base.sha`) can predate the
trusted parent (`HEAD^1`) of the provider's merge checkout. The loss scan keeps that
event base, so a contract-evolution request or retirement manifest that landed on
main in between shows up again as a loss. Both consumers treat it as public history
instead of re-proving it, only from bytes committed at the trusted parent:

- Contract evolution (`_already_landed`): all of these must be identical between the
  trusted parent and the candidate by Git mode and bytes: the request, its
  authorization, every retained owner file, every `conftest.py` in each owner's
  ancestor directories up to the repository root (an added, edited or deleted
  conftest does not match), and every authorized contract block. These are exactly
  the inputs `destructive_changes` uses to classify an owner: the owner's own file
  (shapes, imports, markers, in-file helper context, mode) and ancestral conftest
  changes, which it attributes to every owner below them. Helper modules, package
  `__init__.py`, `pyproject.toml` and `uv.lock` are not loss inputs, so they are not
  compared; ordinary CI runs the owners against them. The owners are then admitted
  without a fresh proof and without holding the candidate's unrelated delta to the
  request. If the candidate touched any compared input, or the request or
  authorization is candidate-only or edited, the ordinary fresh proof applies and
  refuses as before.
- Retirement: when every manifest owner is already absent from the trusted parent
  and the candidate (`_retired_in_public_history`), the base owner hash and the
  governing-requirement bindings are not re-checked against the older event base.
  Owners still present at the trusted parent keep the full checks.

A loss the candidate introduces is never covered by this: it is not a manifest or
request owner, or one of the compared inputs differs from the trusted parent, so it
stays in the ordinary account. The shortcut does not look at the CI event: it applies
whenever the trusted bytes already carry the authority and every compared input is
identical, including a `merge_group` run, where it can only matter if the base
predates the landing. The contract shortcut needs an explicit trusted ref, so a local
run without `--ci-event` (which resolves `origin/main` itself) skips it and uses the
fresh proof; the retirement shortcut does not need one and applies locally too.

Trade-off: identity is trusted-versus-candidate, not candidate-versus-the-authorization's
recorded `inputs[].after` bytes. A public edit to a named owner after landing therefore
stays admitted for a stale-base pull request instead of re-refusing it every time main
legitimately touches that owner. Every normal path to main runs this guard on a fresh
base, so the edit was itself gated when it landed. Retirement needs no conftest comparison: a manifest owner that
is absent from the candidate is classified `removed-or-moved`, which no conftest
attribution can override.

## Retained owners with additive changes

The additive route below preserves the original contract. Intentional replacement
of obsolete expectations uses the separately authorized route described in
[contract evolution](#independently-authorized-contract-evolution), never a
relaxation of the additive preservation rules.

Unchanged names or assertion totals alone do not admit an additive change.
A retained owner may appear on both sides of an executing proof when every old
assertion, statement, control structure, decorator, marker and mode is retained.
Old assertions and abrupt exits remain literal at the same nesting. Added abrupt
exits, wrapped old assertions, changed existing defaults or replaced statements
refuse. Helper additions retain every old statement in order; optional helper
keyword probes are restricted to literal boolean/None defaults and literal probe
keywords on an otherwise unchanged call.

Creating a new nested function or lambda does not execute its body in the
enclosing owner. Its local exits therefore do not become enclosing-owner exits.
The compiler still checks its definition-time defaults, decorators and
annotations, checks class bodies at their existing boundary, and preserves
every old callback body's own statements and exits. This structural check only
qualifies the fresh complete executing account; it is not a callback exemption.

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

For a pull request, the event's exact comparison base and the checkout's actual
public parent are separately bound. The comparison base must be an ancestor of
the first parent; the second parent must be the exact event source head. The
checkout tree must equal the freshly computed merge of those actual ordered
parents. A provider checkout may include newer public history than the event
payload. The complete loss scan and fresh requests still use the original event
base, so that history cannot hide an unproved deletion. Wrong parents, unrelated
bases or a forged union tree refuse before proof execution. Push and merge-group
retain their existing exact-head and ancestor rules.

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
lineage proof and cannot use the identical-source route. This includes the dated
backend805 comparison: its31 import changes, two execution extensions and two
fixture additions are classifications, not proven preservation of concurrent
production changes. Real migrated roles and other protected runtime evidence
retain their ordinary requirements. A plan cannot make those changes context-only.

## Fresh paired production lineage

Add an optional `lineage` object to the same complete plan when production
operations move or gain additional execution. The top-level `scope` binds the
complete current Python scope; `lineage.scope` binds the complete old scope.
Every changed Python dependency must be explicitly included on each applicable
side. The two full source universes and their Git bodies, modes and installed
tools remain bound. Changed `uv.lock`, `pyproject.toml`, links and migration
runtime do not qualify for this software route.

```json
{
  "mode": "paired",
  "scope": ["old_module.py"],
  "functions": [
    {
      "before": {"path": "old_module.py", "function": "reply"},
      "current": {"path": "new_module.py", "function": "reply"}
    }
  ]
}
```

This is the `lineage` value, not a complete plan. Function identities are
source-qualified names, including class/nested owners. Unchanged homes need
no function row. A declared pair only identifies the two actual functions:
the reader independently parses their signatures, defaults, decorators,
return annotations, type parameters, keyword interface, source operations and
control nesting. Ambiguous, missing
or changed old operations refuse. Source coordinates select actual AST points;
they never establish equivalence. Unmapped multiline/lambda or other unsupported
source shapes remain unknown rather than disappearing from the account.

Each mutation records its selected target kind, end coordinate and complete
literal AST preimage. The source index retains every node at a shared starting
coordinate, so a child Name cannot overwrite an enclosing Compare, Attribute or
Subscript. Selection must match exactly one actual target. Pairing retains the
target's lexical owner, normalized operation and enclosing control fields. When
the same semantic operation repeats, its complete enclosing control preimage
must distinguish it on both sides; ordinal, line-number and first/last matches
grant no equivalence. Identical or changed ambiguous contexts still refuse.
The reader regenerates targets and full mutated bodies from the bound sources
and verifies both journal sides, including each literal preimage. This static
pairing does not replace executed branches, named kills or runtime proof.

Parameter-slot normalization applies only to the owning body's proven bindings.
Definition-time defaults, decorators, argument/return annotations and type parameters
retain their literal expressions; they cannot borrow the new function's parameter
scope. Keyword interfaces stay exact. Enclosing bindings, namespace-observing calls
and shadowing captures must retain their original lexical identity. Unsupported
lambda/comprehension binding equivalence refuses rather than guessing. A closed
comprehension may retain literal captures of explicitly declared enclosing
parameters or locals. Its iterator, target and condition bindings are checked
in order; shadowing, unbound captures, nested scopes, assignment expressions,
suspension and namespace observers remain unsupported. These captures are never
alpha-renamed: actual unchanged spellings and source operations must still match,
and full executing branch/mutation authority remains mandatory. Synthetic
comparison slots cannot collide with a real Python global name.

References to namespace/frame observers preserve spellings across their module,
including local and imported aliases. Renamed bindings are supported only in
transparent linear local loads, fresh local bindings and direct returns. An
assignment to an already-bound parameter or local refuses: dropping its old
reference can invoke an implicit finalizer that observes the frame, even when
the assigned value is a literal. This restriction applies to every assignment
target, including repeated targets in a chained assignment. Other
syntax can invoke user code through explicit calls, operators, descriptors,
indexing, coercion, iteration or context protocols; this route refuses that
unproved equivalence rather than selecting a safe callable, type or protocol by
name. The actual producer and reader retain the healthy pure positional subset.

The existing paired-source and same-source owning tests share the complete
independent boundary companions. Their allocation keeps the twelve indirect
species and finalizer control outside the already substantial paired-runtime
item, without removing a case, borrowing a proof, or changing the 300-second
pytest item limit or the producer's 60-second subprocess limit.

Both modes compare the complete tracked dependency universe. Changed non-Python
configuration/data or an unclassified input requires independent runtime proof;
changed lock/environment inputs remain unsupported. A tracked execution request
qualifies only through the same complete strict validator used by the required
consumer, with its full body and mode bound. Its discovery directory or suffix
alone gives no exemption. Each finite worker forbids that control object from
being used as runtime data: even catching the access refusal leaves the run
incomplete. Ordinary valid requests still execute through the original default
reader. No metadata role authorizes a changed executed setting.

Admission routes for a PR that changes non-Python inputs (bu-ihitkl). Evidence
basis: the no-loss split was confirmed by running `scripts/check_condensation_ledger.py`
against scratch trees; the contract-evolution statements are read from
`contract_evolution.authorize` and `contract_evolution.retirement_accounts` as of this
change, with `authorize` also run against the merged bu-q1hzqn and bu-2hlwhh
manifests (it accepted both; refusals were probed on the bu-q1hzqn tree). A full
contract-evolution replay was not completed. The refusal
`changed-non-python-dependency-needs-independent-runtime-proof` belongs to the
survivor-evidence route only (`production_dependencies`, reached from
`verify_ledger` and the producer); the contract-evolution route does not call it.

- **Split a no-loss PR.** The guard demands evidence only for source loss, so a PR
  that retains every test and changes only documentation, OpenSpec files, a
  `pyproject.toml` marker line or an executed data or shell file passes with
  `losses: 0`. Removing those paths from a PR that does lose or restructure tests
  leaves the same `unproven-test-or-assertion-loss` refusal, so the split moves the
  non-Python concern but never admits the loss itself.
- **Contract-evolution manifest (retained-owner changes).** `authorize` admits every
  changed path, whatever its type, when the protected manifest lists it as an exact
  `inputs` record. The merged bu-q1hzqn manifest lists changed
  `.github/ci-test-weights.json`, workflow YAML and docs; the merged bu-2hlwhh
  manifest lists changed docs and an OpenSpec change spec. It refuses an unlisted
  changed path (`unapproved-contract-runtime-input`), a listed path edited after the
  manifest merged (`stale-authorized-source-or-context`), any change to
  `pyproject.toml` or `uv.lock`
  (`changed-environment-needs-independent-installed-tools`), and a cited requirement
  block that does not exist on the comparison base (`missing-contract-block`, or
  `git-source-unavailable` when the spec file itself is new) or does not change in
  the PR (`stale-or-unchanged-authorized-contract`). The amended requirement
  therefore stays in the PR that changes the tests; do not split it out.
- **Governing retirement (whole-owner removals).** `retirement_accounts` admits owners
  that are wholly removed, under trusted manifest bytes and a changed bound
  requirement (see "Governing retirement" above). It is the route for whole-owner
  removals.
- **Neither route.** A retained-owner loss PR (a retained test changed or
  restructured) that carries no changed existing requirement block, or that needs a
  changed environment input in the same PR, is not admitted by the contract-evolution
  route, and the survivor-evidence route refuses the same inputs. Land the environment
  change in a separate no-loss PR first, or file a spec-first amendment of
  REQ-testing-053; do not add an exemption.

The finite Python worker still refuses any runtime read of an excluded request,
including a caught audit refusal. Its descendant event is evidence of execution,
not evidence that the child could not read the input. A metadata-bearing child
can qualify only with the complete external no-access protocol below; the
required reader binds the Python descendant observation to actual post-entry
kernel actors. A forged event, absent actor history or unsupported native child
provenance refuses. Ordinary no-metadata children retain their existing behavior.
No library, executable, import, suffix or body hash grants a child exemption.

Metadata-bearing runs require installed `strace`, bound by executable body/mode
in the existing tool closure and by version/options hashes in each observation.
CI installs that prerequisite when absent; local callers receive UNKNOWN rather
than installing or skipping it. The isolated launcher starts tracing before the
worker interpreter. The observer consumes the full file, descriptor, process and
memory syscall stream in RAM, with kernel-resolved descriptor annotations. It
observes absolute and relative opens, actual cwd/dirfd resolution, duplicated,
inherited and reused descriptors, libc reads and mmap. An open or pathname/alias
operation involving an input is conservatively a refusal; a successful read has
a separate counter and cannot borrow open-only evidence.

Each original input must be a stable regular single-link object, with its full
body/mode bound before and after execution and the same private device/inode
identity retained. If the complete original Git inventory predates a current
request's creation, its observation binds an explicit absent original object
before and after, while retaining that pathname in the full access matcher.
Creating, opening or consuming it still refuses. The current side must bind its
actual present body/mode; a missing current object cannot borrow original
absence. Kernel paths and actual object identities match accesses;
relative unresolved candidates refuse conservatively. Input alias/ancestor
renames are accesses, and deleted descriptors or unsupported namespace changes
make observation UNKNOWN. Incomplete or unpaired records, unresolved read
provenance, reused actors, missing exits, or unsupported tracing never become
zero-access evidence. The observer reconciles original parent/child histories,
including inherited executable identity, and every actual actor must exit.

The worker's own original request must have a successful read as a live-observer
probe. A distinct fixed nonce-body read immediately before the Python subject
hook separates installed tracer setup from subject entry; both probes are
mandatory. That marker grants no callable authority. Descendants qualify only
when their complete source/dependency bindings, original actor graph, whole
shutdown and zero actual input accesses all validate together in the required
reader. Existing CTracer, pytest phases, mutation journal and SQLite authority
remain necessary. Unsupported forms stay UNKNOWN rather than borrowing a
library or executable allowlist.

A worker failure, timeout or interrupt cancels the sibling side promptly,
preserving the original failure and deadlines. Cancellation signals only
currently live tracees whose original start identity and owning process group
still match. The ordinary observer captures `signal=all`: strace's signal
filter also controls killed terminal records, so a delivery filter would omit
other actual deaths. Complete TERM and KILL histories retain each real
`+++ killed by ... +++` record within the unchanged deadline.

The strace 6.8 `CLD_STOPPED` notification with numeric zero status is also
accepted as nonterminal; it cannot settle a pending call or replace an exit record.
The parser recognizes only complete canonical nonterminal notifications: standard
(non-realtime) signal names with matching `si_signo`, `SI_USER` or `SI_TKILL`
source PID/UID fields; `SIGCHLD` with `CLD_EXITED` numeric status or
`CLD_KILLED/DUMPED/TRAPPED/STOPPED/CONTINUED` signal status, PID/UID and numeric
user/system ticks (including strace's numeric seconds comments); and
`--- stopped by SIGSTOP/SIGTSTP/SIGTTIN/SIGTTOU ---` group stops. Other siginfo
layouts, malformed/truncated records and notifications after exit remain UNKNOWN.
These records never provide an exit, executable, parent, probe or input read and
never clear a pending syscall. Only a paired resumed syscall settles its pending
entry; even a genuine terminal cannot erase an unfinished call. A notification
cannot substitute for an omitted terminal. Handler syscalls remain fully observed,
including genuine excluded-input accesses. The real ordinary producer and strict
reader exercise TERM/KILL descendants, group stop/continue, a nonfatal handler and
its continuation, both probes, and an omitted actual killed-terminal refusal.

The existing whole-group finally still settles all owned processes. Missing or
uncertain actor settlement remains incomplete; a completed diagnostic is not a
successful proof. The changed canonical trace options invalidate older observer
bindings: regenerate actual source/tool/options/nonce-bound evidence instead of
restamping a receipt. Unsupported session/namespace changes remain refused.

A worker that completed and published, but whose observation refuses, is not
such a failure and does not cancel its sibling. Both sides settle inside the
same total deadline, then refusals raise in fixed side order, removed first, so
the receipt and both carriers never depend on which side finished first. If the
sibling fails or times out after an earlier completed refusal, that refusal
remains the reported cause.

A closed incomplete observation is published before launch and merged onto an
already-published actual worker result even when observation or cancellation
raises. Absence remains UNKNOWN, and no diagnostic defaults authorize admission.
Raw syscall/path/argv/child text stays in the observer's RAM. Durable data is
limited to fixed status/counts, public request body/mode bindings, opaque
nonce-hashed actor relations, executable hashes/modes and version/options hashes.
No software observation establishes SQL, whole-cluster or timing outcomes.
Observer finalization preserves the identical pending primary exception through
join, descriptor close, owned FIFO unlink, stream completion, input/process
availability and readback, and diagnostic publication. Process-record stat
failures use the fixed `process-stat` stage code. Each failed stage records only its fixed
`finalization_failures` code; any such failure leaves observation incomplete
and the required reader refuses it. Finalization continues to retain already
observed counts and attempt closed publication. If publication itself fails,
one bounded incomplete-publication attempt preserves that status when possible;
the last available carrier is diagnostic only; the failed whole invocation
cannot admit proof. Without a pending primary, the first finalization exception
is propagated after the remaining stages are attempted. Normal cleanup remains
healthy, and missing or unsupported stream provenance never becomes complete.

Both full selected populations execute against their own source, with the same
finite ctrace protocol. For each old owner, only its named current survivors may
supply measured branches or semantic kills. Every old branch endpoint must
retain its independently derived unique source operation. New operations may
connect an old edge, but cannot skip or reorder another old operation. Every
old generated mutation must have one uniquely matching operation/operator on
the current side. Each side executes its own independently generated mutant
body and restores its own original bytes and mode. The old/current mutation
hashes and coordinates need not match; an arbitrary branch map, summary count,
or copied old mutant body cannot substitute for this pairing.

The producer qualifies the complete declared owners, their imports, helpers,
arguments, markers, statements and every actual ancestral fixture. It does not
repeat a scan of unrelated owners inside each finite execution. The required
reader still scans the entire old and current test universe independently: a
destructive owner omitted from the request refuses even if the declared proof
passes. Neither a producer PASS nor a request's selected population replaces
that complete accounting.

The stdlib reader recomputes the pairing from the exact base Git bytes and
current inputs, then verifies every literal SQLite/run carrier, named kill,
full case population and restoration. Missing mapped branches or kills refuse;
unhealthy baselines and unsupported/uncertain execution stay UNKNOWN. This
proves only preservation of the named old contracts under the measured changes.
Additional behavior still needs its owning assertions, genuine review and full
gates. It grants no SQL, retirement, changed-environment or general semantic
equivalence credit. Default CI accepts this same optional object in a tracked
`event-base` request and generates fresh evidence through the existing producer;
it never rebinds an older receipt. Existing budgets, event predicates, selectors
and mandatory delivery remain unchanged.

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

When the fresh CI child refuses, the reader retains `proof-N-refusal.json`
beside that child's directory and prints the same bounded diagnostic. It binds
the canonical execution request and tracked source record, actual worker nonce,
closed receipt category and scalar tracer/access-completion observations. Raw
child output, paths, case identities and arbitrary error text are excluded;
unknown or malformed values remain unknown. This diagnostic does not admit the
proof or change the original categorical refusal or its deadlines.
Each diagnostic carrier is opened nonblocking without following its final
symlink, then checked through that same descriptor for regular-file type and a
bounded body. FIFOs, devices, directories, oversized bodies and malformed JSON
remain unavailable diagnostics; none can block receipt recovery or admit a proof.
Malformed conversion or excessive JSON/canonical-projection recursion also stays
unavailable inside that descriptor lifetime. A byte limit alone does not make a
nested diagnostic safe to decode or hash.
Summary publication pins the original physical output directory before invoking
that child and exclusively creates a new regular output through its descriptor.
An occupied name (including a symlink, hardlink, FIFO or directory) is left
untouched; the same closed summary still prints beside the primary refusal.
Publication never truncates a preexisting file or cleans up a pathname that the
child can replace. A failed or partial output remains unavailable diagnostic
custody, never proof admission or a replacement for the original refusal.
When the existing hosted `TEST_EVIDENCE_DIR` is configured, the same fixed closed
summary is also published there with the tracked request-record and closed-summary
hashes as its name. Distinct execution nonces retain distinct diagnostics without
replacing an earlier request carrier.
The publication fault tests use a separate owned evidence namespace: their
deliberately replaced outputs and write faults never enter the shared shard
upload namespace. The direct-producer assertion likewise consumes only its
invocation's namespace. Its unrelated-malformed-carrier companion remains
untrusted; isolation does not turn malformed diagnostic output into proof.
The unit/affected evidence upload retains these JSON summaries because sanitized
JUnit deliberately omits failure and stdout bodies. The extra carrier contains
no child output, paths, operand text or new admission authority; unavailable or
occupied evidence destinations preserve the original refusal. This enables a
future source/request/nonce-bound diagnosis and does not identify the cause of
an earlier hosted failure.
Direct producer refusals use this same closed projection before returning or
raising, including the original run nonce and available source head/tree/program
hashes. Unknown source fields stay null. Diagnostic publication does not add CLI
stdout or change the proof result, cleanup/restoration admission, signal-handler
restoration, lock closure or original caller assertion. Successful proofs do not
publish a refusal. No child traceback, operand or raw output enters that carrier.

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
