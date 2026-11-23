# S1a framework evidence (bu-rvgzmf.4)

This report supports review of the first framework draft. It does not complete
AC1–AC4, the source-scan migration, or the parent. S1b/S1c/S1d/S2/S5 remain
separate slices; S3/S4 still require bu-ly3lv5.12/.13. No existing test was
deleted, weakened, moved or renamed. No product, OpenSpec, API fixture, generated
route, migration test, CI/workflow, Makefile or foreign checker was edited.

## Source receipts

| Input | Commit | Git tree | SHA256 of `git archive COMMIT tests roster` |
|---|---|---|---|
| Adopted historical baseline | `1751d8a0cfb248e85ce4c01dcb5d507909527b3d` | `fa282c7afdb907acb83f91c85e5064207dfb8420` | `96daf98eff6c5fd3b6fae7464f295ea9766e98791698f264dad8cbfffaa5b7f7` |
| Exact assigned base | `82826664d4edba5b725cb84665518b514e26d8ee` | `4f6fe204607f01309e830f345e503a4549ba90d5` | `18b65c130794ebb9418c49d27e3f3cfba9a9ae353501b95f317f2aa9a6017328` |

The full assigned-tree archive used for baseline collection has SHA256
`4f1535064da26b1cb2039ca7a348239c22d8fed9d670007580a199222c192568`.
Archives were extracted separately. Baseline collection used the full assigned
archive and its own real venv, with imports resolving to that archive's `src/`.
The worker branch starts at the exact assigned commit, without a rebase.

## Reconstructed census, not an adopted replacement baseline

The October scratch Python and TSV are unavailable. The new executable
`butlers.testing.pin_census` implements the packet's structured section-7
definition, labeled `section-7-reconstruction-v1`. It is new reconstruction
material, not the missing historical prototype or a historical sidecar edit.

| Population | Full definitions / body lines | In-scope definitions / body lines |
|---|---:|---:|
| Adopted packet at historical commit | 500 / 8,283 | 326 / 5,899 |
| Historical reconstruction | 507 / 8,457 | 336 / 6,120 |
| Historical discrepancy from packet | +7 / +174 | +10 / +221 |
| Exact assigned-tree reconstruction | 775 / 12,170 | 572 / 9,371 |
| S1a working-tree reconstruction | 780 / 12,225 | 577 / 9,426 |

**AC3 remains open.** Neither 336/6,120 nor 572/9,371 replaces the adopted
326/5,899 denominator. The unavailable prototype and its exact row assignments
prevent a precise historical equivalence claim. The assigned tree has also
changed substantially since October. No full reduction or performance gain is
claimed. S1a adds framework and self-tests; it removes no existing body lines.
The working-tree metric adds five static definitions / 55 body lines. This
static census is distinct from pytest's 45 added parameter-expanded cases.

The tool walks synchronous `test_*` definitions at any nesting in `tests/**`
and `roster/*/tests/**`. It requires an assertion in the test and a read or
AST/getsource call in the same-module helper/module-assignment closure. Body
span is exactly `end_lineno - lineno + 1`, excluding decorators. Helper name
collisions conservatively include every matching local definition. It does not
perform imported-helper or arbitrary alias/dataflow analysis.

The explicit fixture exclusion matches pool/db/database/client name segments
and tmp_path/tmpdir families. Await, tempfile and subprocess usage in the local
closure is excluded. Categories prioritize doc path hints, then config path
hints, then AST/getsource, then other source reads. These choices and the
packet's unspecified "and similar" deployment exclusions can differ from the
missing prototype; totals were not tuned to the desired answer.

| Historical owner exclusion | Packet definitions / lines | Reconstruction definitions / lines |
|---|---:|---:|
| Migrations (bu-dss7he.11) | 80 / 656 | 80 / 643 |
| Deployment (bu-dss7he.6/.14, bu-viat6h.4) | 85 / 1,578 | 81 / 1,472 |
| CI (bu-29wkwj.3/.8) | 8 / 114 | 9 / 186 |
| Stand-in parity (bu-rvgzmf.1) | 1 / 36 | 1 / 36 |

The exact exclusion prefixes live in `pin_census._bucket`; every generated row
retains its owner bucket and helper closure. Read the retained
[historical summary](historical-summary.json),
[assigned summary](assigned-summary.json), and compressed generated TSVs
([historical](historical-census.tsv.gz), [assigned](assigned-census.tsv.gz)).
Gzip preserves the executable's native TSV bytes, including empty helper cells;
these generated artifacts are not the absent October TSV.

Reproduce from an extracted archive using this branch's real venv:

```sh
uv run --no-sync python -m butlers.testing.pin_census --root /path/to/archive
uv run --no-sync python -m butlers.testing.pin_census --root /path/to/archive --tsv
```

The decompressed TSV SHA256 values are
`0db62e5dfd5782a9bb1f4a3e5a4d57fd3c10d73c1b89010b9b3be770b7b682d9`
(historical) and
`bc8465475bd3c3103e7b990664c588282d5316f640947018c0dc602735081f49`
(assigned). Later AC3 accounting must include all replacement artifacts, not
only census-selected test bodies: shared helpers, architecture code, ratchet
data, self-tests and future TOML rows. S1a's architecture directory alone adds
1,228 raw lines, including 785 lines of temporary offender data; the census
module adds 237 and primitive/census self-tests add 212. Those are overhead,
not evidence of reduction. The existing helper module has a net +163 lines.

## Rule scope and exact existing offenders

The registry's real scope is `tests/**/*.py` and `roster/*/tests/**/*.py`, with
the explicit `source_rules.EXCLUDE` tuple preserving the packet's foreign CI
surfaces and #6048's duplicate-name checker test. The matched scope is 2,347
Python files; SHA256 of sorted relative paths joined with LF and a final LF is
`92028fb6afe7b443dd38c96e5ddaaee313ec5172dacc1b227628c37f9df3cf9a`.
This is the new ratchet scope; no existing consumer's scope was changed.

| Rule | Existing admitted sites | Distinct files |
|---|---:|---:|
| no-change-prose-reads | 22 | 12 |
| ast-tests-use-source-guard | 85 | 85 |
| no-private-walkers | 4 | 4 |

The [frozen offender data](../../../tests/architecture/source_offenders.json)
is the exact membership receipt: path, line, rule, AST-shape digest and required
reason for every site. All 94 distinct paths exist at the assigned base. No gate
regenerates this data. New sites, modified shapes, obsolete rows, duplicates
and missing reasons fail. Removed rules cannot leave silent extra buckets.

The four walker sites are `_StripDocstrings` in
`tests/api/test_secrets_v2_spec_anchors.py:62`, `Visitor` in
`tests/contracts/test_schema_reach.py:1675`, `_Scanner` in
`tests/contracts/test_user_context_census.py:111`, and `Visitor` in
`tests/daemon/test_non_messenger_approval_command_contracts.py:588`.
They remain intact for later slices.

The coordinator authorized read-only API offender recording and the packet's
Rule/SourceFile interface. No API file, fixture, route check or guard was
changed, and no concurrent API delta was imported. The initial change-path
ratchet conservatively flags path constants, including synthetic/recognizer
sites still awaiting reviewed disposition; it is not a dataflow proof of a
read. The new registry and its tests declare reasoned synthetic snippets.
Existing synthetic-tree tests were not edited to add pragmas. No claim is made
that the admitted change-path sites should all be deleted in S2.

## Executed verification

- `Tests: +45 ~0 -0`. Exact-base `tests/ roster/` collection: 38,890 selected /
  38,927 total; S1a collection: 38,935 selected / 38,972 total. Both deselect 37.
  [Collection membership receipt](collection.json) records every addition and
  no removal. The earlier overlapping baseline collection was discarded.
- All 45 primitive, census and registry cases passed in 70.24 seconds, using
  `uv run --no-sync pytest tests/architecture/test_source_rules.py
  tests/testing/test_source_guard.py tests/testing/test_pin_census.py -q
  --tb=short -n 0 --durations=5`. Real-scope rule calls took 40.21s, 26.97s and
  0.51s respectively on the loaded host. No old scanner was removed, so this is
  added cost, not a measured scanner-speed improvement.
- All 71 existing-consumer/supporting cases passed in 65.00 seconds:
  `tests/config/test_injected_clock_guard.py`, `test_migration_chain_head.py`,
  `tests/contracts/test_roster_module_identity.py`, `test_schema_reach.py`,
  `test_import_linter_contracts.py`, `test_agent_guidance.py`, and
  `tests/scripts/test_scripts_readme_index.py`. Existing pragma and scoped
  binding behavior remains covered. All original shared primitive bodies are
  preserved, including the existing type-baseline diagnostic in `scopes`.
- After restoring that original helper, the combined final scope (all 45 new
  and 71 retained cases listed above) passed: **116 passed in 53.10 seconds**
  with the repository's three-worker `loadfile` default. The three registry
  calls took 29.24s, 17.99s and less than the five slowest reported entries.
- Every rule rejects planted forbidden syntax in a scratch copy of a real
  clean source file, and passes again after restoration. Empty effective scope,
  escaped path literals, comments/docstrings, synthetic-tree declarations,
  canonical specs, bare `changes`, aliases and unused helper imports have
  positive/negative controls. No source file under test was edited in place.
- Four falsification runs neutralized the check or suppressed only new,
  obsolete, or missing-reason ratchet diagnostics. Each produced three real
  assertion failures with pytest exit 1. Those expected reds are independent
  of missing symbols; restoring normal behavior produced the green above.
- Both exact-base and `origin/main` test plans report shared-test-tooling
  escalation. The plans execute no tests. Focused local contract/API-loader/
  roster-loader checks and full collection are named evidence; broad unit and
  integration execution belongs to hosted CI. No DB, Docker or live service
  was started for this slice.
- Scoped Ruff check/format passed. Scoped Pyright reports only the unchanged
  recorded `scopes` return-type diagnostic; the new code adds none. The pinned lint tools
  were installed through `make install-lint-tools`; typecheck preparation used
  the already installed Node v24.22.0. Earlier missing-tool and wrong-Node
  guard attempts are not passes. An attempted annotation cleanup removed the
  recorded diagnostic and correctly failed the exact type-baseline guard; it
  was reverted without editing the foreign type baseline.

The full `check-guards` result and exact-head hosted status are reported with
the draft PR. A draft PR run is not the protected terminal merge-group verdict;
the coordinator owns review, tracker reconciliation and queue admission.
