# Route A offline recipe proof

The three sealed recipes use global cache-image `ARG`s, named `FROM` cache
stages and literal `COPY --from=<stage>` sources. Docker does not expand build
arguments inside `COPY --from`; see the official [Docker multistage guidance](https://www.docker.com/blog/advanced-dockerfiles-faster-builds-and-smaller-images-using-buildkit-and-multistage-builds/).
The launcher's `validate_sealed_build_inputs` checks this representation as well
as its existing offline/no-external-frontend tokens. Its digest-pinned RepoDigest,
lock-bound cache labels, clean linked worktree, credential refusal, Compose,
context and teardown contracts remain unchanged.

The `Migration Chain Integrity (main)` workflow has one optional manual input,
`offline-route-a-build-proof`, default false. A true input runs only the bounded
recipe proof, while push and default manual dispatch keep the existing migration
job and commands. The coordinator dispatches the proof against the published
exact source SHA; source authors do not dispatch it or claim future results.
If an image-size diagnostic input is present in a later serialized workflow,
selecting both manual build modes fails before checkout or any build. The
coordinator must preserve this refusal and make that diagnostic job skip when
the offline proof input is true.

`scripts/prove_route_a_offline_builds.py` performs:

1. Hosted-source, clean-checkout, ambient-input and current recipe validation.
2. A real tiny OCI-cache build, then valid alias success as the environment/input
   health prerequisite, old variable-COPY refusal and missing/invalid input or
   blank-stage failures with the same
   immutable cache identity and `--network=none`.
3. Online preparation of the existing application base, pinned Go input and
   Node/lock-version Playwright bases, and real Go/uv/npm dependency caches.
   Native BuildKit OCI export is read back: every manifest/config/layer blob
   digest and size is checked, and cache labels match the actual checked-out
   `go.mod`/`go.sum`, `uv.lock` and `package-lock.json` contracts.
4. Actual builds of all three unmodified historical recipe bodies retained at
   `tests/fixtures/route_a_9ff_offline_recipes/`, expecting the variable-COPY
   failure. They use the same materialized cache/base identities as the repaired
   builds and the current contexts/unchanged locks. This is an explicit controlled
   recipe comparison, not a historical full-application success claim.
5. Actual current application, frontend and browser image builds with the same
   sealed OCI inputs, `--network=none`, `--pull=false`, recipe/source hashes and
   independent actual image IDs. No image is published or run.
6. Removal of its own generated images and ephemeral builder. Cleanup failure
   makes the receipt non-passing.

[Docker documents local OCI build contexts](https://docs.docker.com/reference/cli/docker/buildx/build/#build-context).
The public base tags are materialized once before offline execution; OCI input
manifest/config identities are retained rather than inventing registry
RepoDigests. This proof **does not invoke or satisfy the full launcher's separate
RepoDigest admission**. It does not exercise a database, browser interactions,
trained models, providers, live Route A or application runtime. The immutable
historical run 37754481671 cause remains UNKNOWN: discarded stderr cannot be
recovered from a new controlled red.

Only `offline-build-receipt.json` is uploaded. Builder stdout/stderr remain in
process memory and are never printed or retained; failures expose fixed stage,
exit and allowlisted category fields. The receipt binds source, run/attempt,
current/historical recipe hashes, same sealed input digests, expected controls,
three image results and cleanup. Unreached images are absent, not successful.
Source/static or mocked controls cannot stand in for these actual builds.

Plain BuildKit progress is captured in memory to make failures observable.
The closed projection examines at most 64 KiB from each output stream, retaining
only fixed phrase indicators, byte counts and in-range line/instruction positions
within the known public recipe. Truncated diagnostics are explicit. Phrase
observations are not an underlying-cause diagnosis; unknown/context/registry
failures cannot satisfy the old-COPY negative. Each negative must match its own
expected reference or missing-stage class, and the valid-alias companion must
still build with the same input. No raw output, error arguments or child paths
are printed or uploaded.

Actual manual run 37764265658 at source `31a5195` prepared and verified the tiny
OCI input, then refused the first old-COPY control after exit 1 with an unknown
failure class. Cleanup passed. The valid alias and all three historical/current
recipes were unreached; this establishes no actual COPY cause or image success.

Run 37766229982 at `7718fb8` also refused that control: stdout was empty,
stderr was 689 bytes, all 13 initial indicators were false, and no public recipe
coordinate matched. Its physical cause stays UNKNOWN. The next projection adds
fixed CLI/exporter/network/named-context observations and membership in a finite
public builder vocabulary, without token order or arbitrary strings. This is
observation only: those observations cannot satisfy a negative class. The valid
alias runs first to establish actual same-input health before attributing any
old-COPY failure; it never replaces the old/malformed negatives or real recipes.

The script has a 30-minute overall process deadline, finite per-command bounds
and own-resource cleanup deadlines. The 45-minute manual job watchdog includes
the complete 640-second UV installer retry interval, 150 seconds cleanup and
110 seconds checkout/parser/artifact reserve. This is provisional, not measured
p95 or a routine-lane performance claim. Normal/protected CI, independent review
and sole merge-queue landing remain required; other .5/.6, full launcher and
held capture/h3 outcomes remain separate.
