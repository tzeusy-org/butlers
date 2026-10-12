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
separate full current-source copy. The default route requires identical production bytes
and modes. Changed implementations or Python dependencies need the explicit
paired route below. Changed installed environments or links still refuse. The proof-tool files
are current-bound execution machinery; their introduction grants no production
dependency exemption. Ordinary tests retain their coverage core,
markers and parallel defaults; only the isolated proof uses ctrace and `-n 0`.
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

Prompt sibling cancellation preserves the original refusal and deadlines. It
signals only currently live tracees whose original start identity and owning
process group still match, allowing the observer to record their actual exits
within the existing deadline. The existing whole-group finally still settles
all owned processes. Missing or uncertain actor settlement remains incomplete;
a completed diagnostic is not a successful proof.

A closed incomplete observation is published before launch and merged onto an
already-published actual worker result even when observation or cancellation
raises. Absence remains UNKNOWN, and no diagnostic defaults authorize admission.
Raw syscall/path/argv/child text stays in the observer's RAM. Durable data is
limited to fixed status/counts, public request body/mode bindings, opaque
nonce-hashed actor relations, executable hashes/modes and version/options hashes.
No software observation establishes SQL, whole-cluster or timing outcomes.
Observer finalization preserves the identical pending primary exception through
join, descriptor close, owned FIFO unlink, stream completion, input/process
readback and diagnostic publication. Each failed stage records only its fixed
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
