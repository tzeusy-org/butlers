# PID1 Shim Dependency Closure — Option B Implementation Handoff

## Authority and status

The owner selected Option B on 2026-09-11 and adopted the exact reviewed
contract plus authorized its bounded implementation on 2026-09-12: compute the
namespace-PID1 shim closure at image-build time and consume it from the
validated image-owned runtime-input manifest. This grants no deployment, live
credential/provider operation, prototype disposition, or reassignment.
`bu-9hkm5` remains the implementation owner. Its runtime-`ldd` Option A
prototype is not an input to this design and must be neither reused nor deleted.

The amendment is intentionally bounded to the image-owned shim dependency
closure. It does not change authentication policy, credential/provider
authority, runtime process behavior, the PID1 handshake, the spawn-kind set, or
the one-concrete-sandbox-spawn-boundary policy.

## Contract summary

- Bump `runtime-cli-sandbox-inputs.json` from schema version 2 to version 3.
- Add the exact top-level `shim` record defined in `design.md`; keep provider
  entries in their existing `binary`/`executable`/`readonly_inputs` shape.
- Generate the shim executable and recursive interpreter/shared-library closure
  after the final shim install. Never infer it from a provider closure.
- Preserve source-to-logical-destination mappings. Deduplicate only identical
  pairs, sort the result by destination, and reject two sources for one
  destination.
- Require every shim-closure source to be a regular file; reject directory
  bindings, including broad root-owned trees such as `/usr/lib`. Existing
  provider package-root directory bindings are unchanged.
- Load and validate the shim record at the existing trusted manifest I/O
  boundary before any spawn. Pass the validated closure explicitly to the pure
  launch planner.
- Reject missing, malformed, empty, unsafe, old-version, or image-mismatched
  shim data before spawn. Do not run `ldd` at runtime and do not fall back to a
  provider closure or direct child.

## Current source map

| Surface | Current fact | Required implementation |
|---|---|---|
| `scripts/generate_runtime_cli_sandbox_manifest.py::build_manifest` | Emits version 2 with `providers` only. | Emit the exact version-3 top-level shape and a separate shim record. |
| `scripts/generate_runtime_cli_sandbox_manifest.py::_runtime_closure` / `_ldd_dependencies` | Computes recursive provider closures; generic non-zero `ldd` is treated as no dependencies. | Reuse the traversal for the shim with strict unresolved/error handling. A positively identified static executable may have only its executable binding; an unexplained failure or `not found` fails the build. |
| `Dockerfile.base` | Installs/chmods the shim before npm providers, then runs the manifest generator. | Keep generation after provider installation and prove it occurs after the final shim copy/chmod; do not replace the shim later in the image. |
| `RuntimeCLIInputManifest._read_document` | Safely reads and caches version 2; validates the manifest file before JSON parsing. | Preserve all file checks and require exact version 3/top-level shim shape before identity allocation, stage creation/write, or `_launch_invocation`. |
| `RuntimeCLIInputManifest._immutable_input*` | Validates root-owned, non-symlink, non-writable file-or-directory provider sources and exact binding keys. | Add shim-specific validation requiring every shim source to be a regular file, with the executable binding additionally executable and identity/path-matched. Reject directory-valued shim sources before any launch boundary; do not weaken the provider package-root allowance. |
| `RuntimeCLIInputManifest._resolve` | Validates and deduplicates one provider entry. | Keep provider resolution and expose a separate validated shim resolver; no provider entry may stand in for the shim record. |
| `build_bubblewrap_launch_plan` | Purely builds one plan but appends only `shim_path`, so dependencies depend on caller inputs. | Require validated shim bindings, combine by destination, collapse identical pairs, reject conflicts, sort, and then build parents/`--ro-bind` arguments. Perform no manifest read or subprocess discovery here. |
| `BubblewrapDashboardCLIAuthSandbox.launch_device_auth` / `run_readonly_command` | Public launch entries run exact-image preflight and resolve provider inputs before entering `_launch_invocation`. | Invoke the injectable production shim resolver immediately after preflight and fail before identity acquisition or stage creation/write; pass its validated result into `_launch_invocation`. |
| `BubblewrapDashboardCLIAuthSandbox._launch_invocation` | Sole production planner caller and concrete spawn site; currently acquires identity and writes staged authority before planning/spawn. | Accept mandatory already-validated shim inputs and pass them to the planner. Do not resolve here, add a spawn kind, or add a production call site. |

## Complete planner-caller inventory

Every current call must move in the same implementation change:

1. `src/butlers/cli_auth/sandbox_platform.py::_launch_invocation` — production
   device-auth and read-only provider paths.
2. `tests/cli/runtime_cli_sandbox_exact_image_harness.py::_run` — provider-backed
   exact-image handshake; it must request the shim closure separately even
   though the Codex closure currently overlaps.
3. `tests/cli/runtime_cli_sandbox_exact_image_descendant_survival_harness.py::_run`
   — provider-independent static payload; delete its architecture-specific
   `_SHIM_RUNTIME_LIBRARIES` workaround when the version-3 resolver replaces it
   in this same implementation change.
4. `tests/cli/runtime_cli_sandbox_exact_image_signer_isolation_harness.py::_run`
   — provider-independent adversarial payload; delete its separate
   architecture-specific `_SHIM_RUNTIME_LIBRARIES` workaround in the same
   change.
5. `tests/cli/runtime_cli_sandbox_peer_isolation_harness.py::_launch` —
   provider-independent peer payload; delete `_shim_ldd_closure` and the
   synchronous `subprocess` import, and resolve the manifest once before
   launching either peer.
6. The two direct unit-test calls in
   `tests/cli/test_runtime_cli_sandbox.py` — pass explicit fixture shim bindings
   and assert deterministic deduplication/conflict rejection.

Re-run `rg -n 'build_bubblewrap_launch_plan\\(' --glob '*.py'` immediately
before the implementation commit. Any additional caller is in scope and must
receive the validated shim closure; do not leave a compatibility default.

## Cold-start implementation sequence

1. Refactor the generator closure walk so it can start from the fixed installed
   shim without adding CA/resolver/provider-package roots. Keep the shim binary
   itself in the resulting non-empty binding list. Require every emitted shim
   source to be a regular file; reject directories, unresolved libraries,
   ambiguous output, unsafe paths, and conflicting destinations. Emit stable
   destination order.
2. Emit manifest version 3 with the exact fixed shim identity and provider
   shapes. Keep atomic mode-`0444` output behavior and the 64 KiB reader bound.
3. Extend `RuntimeCLIInputManifest` with a shim-resolution result that is
   separate from provider resolution. Validate exact field sets, canonical
   configured path identity, regular-file-only shim sources, executable safety,
   non-empty mappings, identical duplicate collapse, and conflict rejection
   before returning it. A root-owned non-writable directory is still invalid
   for a shim binding; keep provider package-root directory handling unchanged.
4. Add an injectable shim-input resolver to
   `BubblewrapDashboardCLIAuthSandbox`. Call it at each public launch entry
   immediately after exact-image preflight and before identity acquisition,
   stage creation/write, or `_launch_invocation`; thread its immutable result
   into `_launch_invocation` and the mandatory planner argument. Test-only
   injected resolvers must return explicit bindings rather than enabling a
   missing-manifest fallback.
5. Make the planner merge caller and shim bindings by destination and sort the
   unique bindings. Keep the existing empty-root, forbidden-prefix, typed-FD,
   stage, PID1, and command validations unchanged.
6. Update all six caller groups above, including all provider-independent
   harnesses. Remove both temporary `_SHIM_RUNTIME_LIBRARIES` tuples and the
   peer harness's `_shim_ldd_closure`/synchronous `subprocess` path once all
   three consume the validated manifest. Re-run a focused search proving no
   hard-coded or caller-discovered shim closure remains. Do not touch the
   foreign prototype.
7. Update `Dockerfile.base` comments/order assertions and the operator/runtime
   documentation that describes the manifest version or shim provenance. Build
   and deployment remain outside this packet.

## Fail-closed matrix

| Condition | Required result before identity allocation, stage write, or `_spawn` |
|---|---|
| Manifest missing, symlinked, wrong owner/mode/link count, empty, oversized, changing during the same-descriptor read, invalid UTF-8/JSON | Existing typed manifest validation failure; no child. |
| Version 1, 2, or unknown future version | Reject; no dual reader, compatibility alias, or inferred upgrade. |
| Missing/extra/mistyped shim fields, wrong fixed name, or configured path mismatch | Reject as image/application mismatch. |
| Empty shim bindings or missing executable binding | Reject; provider inputs cannot satisfy it implicitly. |
| Unsafe/unavailable/non-executable shim source; unsafe dependency source/destination | Reject under immutable-source and forbidden-child-view rules. |
| Any directory-valued shim source, including root-owned non-writable `/usr/lib` | Reject before identity acquisition, stage creation/write, `_launch_invocation`, or `_spawn`; provider directory semantics do not apply to shim closure entries. |
| Identical provider/payload/shim binding | Mount once. |
| Same destination with different sources, within or across closures | Reject deterministically before spawn. |
| Build-time unresolved library, unexpected `ldd` failure, or ambiguous dependency output | Fail image generation; never emit a partial manifest. |
| Any runtime closure failure | No runtime `ldd`, broad mount, staged-authority write, provider execution, credential mutation/persistence, direct child, or retry fallback. Unrelated Dashboard health stays available. |

## Test handoff

Implementation delta: `Tests: +3 ~5 -0`. The three additions are parametrized
failure/ordering matrices and the remaining changes extend existing seam tests;
no new test file is introduced.

- Extend
  `tests/scripts/test_generate_runtime_cli_sandbox_manifest.py::test_generator_declares_only_the_registered_dashboard_runtime_closures`
  so provider fixtures deliberately omit every shim library while the separate
  version-3 shim entry contains the executable and logical loader mapping.
- Add one parametrized case family in that file for unresolved shim dependency,
  unexplained `ldd` failure, and conflicting logical destinations. Preserve the
  existing logical-loader-path test.
- Extend
  `tests/cli/test_runtime_cli_sandbox.py::test_runtime_input_manifest_resolves_only_declared_provider_inputs`
  for the version-3 shim result, with provider inputs intentionally lacking
  shim libraries.
- Add one parametrized manifest rejection family in that file covering missing
  shim, version 2/future version, wrong identity/path, empty mappings, malformed
  binding, unsafe source, a directory-valued shim source, and cross-closure
  destination conflict. Exercise a root-owned/non-writable-equivalent directory
  fixture so rejection proves file type rather than only owner or mode checks.
- Add one parametrized public-entry ordering test covering both
  `launch_device_auth` and `run_readonly_command`: a shim-resolver failure must
  call neither identity-pool acquisition, stage factory/write,
  `_launch_invocation`, nor `_spawn`. This is the behavioral proof that
  “before spawn” has not been implemented after authority staging. Include the
  directory-valued shim-binding rejection in this ordering matrix so a broad
  root-owned tree cannot reach planner deduplication or a bind mount.
- Extend the existing logical-loader and minimal-plan tests to assert stable
  destination order, identical-pair deduplication, one `--ro-bind` per
  destination, and conflict rejection without planner I/O.
- Update the existing Dockerfile order assertion in
  `tests/scripts/test_dockerfile_base_runtime_tools.py` and all four exact-image
  harnesses. Assert each harness obtains the shim bindings only from the
  validated version-3 manifest; a passing deduplicated plan must not mask a
  surviving hard-coded tuple or caller-side `ldd`. The credential-free
  exact-image entry remains
  `tests/cli/runtime_cli_sandbox_exact_image_harness.py`; it proves the emitted
  image closure only when separately authorized, and this packet does not
  authorize an image build or deployment.
- Leave
  `tests/cli/test_runtime_cli_sandbox_completeness.py::test_dashboard_runtime_cli_paths_have_one_concrete_sandbox_spawn_boundary`
  unchanged in policy and expected result: exactly one production
  `asyncio.create_subprocess_exec` boundary in `sandbox_platform.py`.

Focused implementation verification should run the two named test files first,
then the Dockerfile source contract and completeness test, followed by the
dirty-worktree planner and its required escalation. Exact-image evidence is a
separate authorized lane, not a substitute for unit behavior or a deployment
instruction.

## Compatibility and rollback

There is no persisted-data migration. Version 3 is a matched image/application
boundary. Update and ship generator, image asset, reader, planner, callers, and
tests together. Existing version-2 app/image pairs remain valid until replaced;
new/old mixtures fail before spawn. Do not implement a v2 reader fallback,
synthetic default closure, provider-derived closure, or runtime compatibility
shim.

Roll forward by building the matched base and application images after separate
authorization. Roll back only to a matched prior pair, retaining the existing
sandbox while the runtime-probe signer mount exists or removing that mount
before an older image starts. The rollback changes no credential, provider,
schema, persistent runtime state, or retry policy.

## Review boundary

Independent review should verify that the amendment closes the shim dependency
gap without widening the child view, weakening immutable reads, introducing a
second spawn path, or turning approach selection into implementation/deployment
authority. Review must also compare the complete
`Asymmetric Runtime-Probe Control Capability` body and scan all active changes
for the same requirement name before approving the amendment.
