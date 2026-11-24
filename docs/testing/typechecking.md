# Python type checking

Run `make typecheck` after the worktree's normal `uv sync --frozen --dev` setup.
The checker is Pyright **1.1.414**, in basic mode, over **all `src/` and `roster/`**.
There are no whole-file exclusions. The sixteen historical mixed RFC0038 paths
remain covered through their current source: PR4900 moved `routing/transport.py`
to `roster/switchboard/tools/transport.py` and extracted retained wire contracts to
`roster/switchboard/tools/contracts.py`. The deleted path is not recreated;
all fifteen remaining historical files and both current successors are checked.
This gate does not rewrite legacy typing, replace dynamic loaders, suppress imports,
or substitute Basedpyright.

The existing pinned Node **24.22.0** must be on `PATH`. Pyright's locked Python
wheel includes its JavaScript bundle; the check refuses an absent/wrong bundle,
checker or Node rather than fetching one. `make prepare-typecheck` is an explicit
setup operation for the first public baseline. It archives immutable public source
and restores its own real frozen environment. CI performs that setup before its
existing named `Lint` step, which still runs `make lint`; `make lint` retains Ruff,
import-linter, gitleaks, actionlint and their self-test, then calls `make typecheck`.
Ordinary checks and baseline updates do not install dependencies or access the network.
The lint target exports `UV_NO_SYNC=1` to its unchanged uv commands; dependency
restoration remains an explicit setup step.

## Baseline and updates

`pyright-baseline.json` is an exact multiset of current errors. Each key encodes
`[relative POSIX source path, optional rule or null, SHA-256 of exact message UTF-8]`;
its positive integer value counts repeated identical diagnostics. Warning and
information counts are validated but do not become baseline allowances. Line and
column changes do not create a new allowance. A replacement with exactly the same
key and multiplicity is indistinguishable: this is a ratchet, not proof of semantic
identity or anonymity.

Every check requires **candidate errors C = committed baseline B**, and **B ≤ trusted
public baseline T**, per key and multiplicity. New errors, duplicate growth,
count-neutral substitutions, adding a new error to both source and baseline, and
stale allowances after removing an error all refuse. To remove errors, fix the
source and run `make update-type-baseline`; the update still requires C ≤ T and
publishes exactly C with an atomic replacement. An existing local account is also
an upper bound for an update: it cannot regrow an allowance removed by an earlier
local shrink. Commit source and baseline together.
Do not edit allowances by hand. Concurrent updates or relevant source, checker,
configuration, baseline or public-reference drift refuse; repeat the comparison.

Before public main has its first baseline, both check and update independently run
the same actual installed checker/configuration on immutable public source and the
candidate. Setup accepts only the checker/configuration addition to the public
locked dependency context. It verifies the public Git blob/mode vector before and
after checking. The input closure also binds visible project-root namespace
membership, default `typings/` stubs, root modules, native/compiled import files
(`.so`, `.pyd`, `.dylib` and out-of-cache `.pyc`) and `py.typed` markers, including
absence, content and Git file modes. Native module/stub links and directory links
refuse instead of importing an unbound external tree. Only hidden non-importable
paths are omitted; importable cache namespaces are included. The owned environment
binds directory membership/kind, file content and Git-style executable modes;
directory links must resolve inside the captured inputs. Literal `.pth` search paths
must remain inside this captured input closure.
Missing public history or an account deleted from public history
refuses rather than becoming a candidate-only bootstrap. Local public authority is
`origin/main`; CI uses its frozen event/base/checkout identities. Fetch through the
ordinary repository workflow when needed, never supply an arbitrary trusted JSON
file. Compatible unrelated movement does not change the identity of a dated receipt.

## Refusals, privacy and recovery

The native CLI has no baseline flags. The readable fallback lives entirely in
`scripts/check_pyright_baseline.py`; the separate runtime owns source binding,
finite process supervision and atomic file publication, not another comparison
engine. Strict JSON/schema validation rejects duplicate keys, non-finite or invalid
counts, malformed ranges/paths/versions and inconsistent exit/summary data. Valid
error exit 1 reaches comparison; exit 0 requires zero errors. Fatal exits, signals,
timeouts, output overflow, missing tools and unfinished owned child groups refuse.
The checker has a 300-second bound and a 32 MiB output bound, followed by bounded
cleanup of only its own process group.

Raw diagnostics and exception text are captured privately and never forwarded to
normal output. The committed baseline retains relative filenames, rule identities,
message digests and counts. These can disclose repository structure and permit
message guessing; hashing is minimization, not secrecy. Review the generated file
before committing. Synthetic poison diagnostics must not escape a refusal.

A failed check never changes source or baseline. A failed update leaves the prior
baseline intact and removes its temporary publication file. Repair the named
prerequisite or source error and rerun; do not disable the gate, expand exclusions,
restore stale allowances or bypass hooks. Revert this change through an ordinary
reviewed PR if the toolchain itself must be rolled back. Hosted current-head CI,
independent review and the protected merge-group remain separate acceptance gates.
