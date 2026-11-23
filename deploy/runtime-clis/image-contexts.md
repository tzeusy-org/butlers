# Source-owned complete image build contexts

`scripts/runtime_clis/produce_image_contexts.py` runs the existing pinned public
producer recipes serially, with one heavy producer at a time (add `--cache DIR`
to reuse unchanged producers; see below):

```sh
python3 scripts/runtime_clis/produce_image_contexts.py \
  --project "$PWD" --work /absolute/new/work \
  --output /absolute/new/closed-contexts
```

The three paths must be real, absolute and disjoint. Work and output must not
exist. The caller needs ordinary Docker/Buildx access; the script neither
escalates privileges nor changes the daemon. It uses the default local builder,
Linux/amd64, an empty task-owned Docker configuration and no ambient credentials,
proxy or remote-daemon variables. HOME retains its caller value. Builder recipes
keep their existing empty private HOME mounts and actual networkless final
layers. No runtime egress or deployment option is enabled.

A finite copy of tracked current source bytes excludes Git metadata, untracked
files/caches and dotenv files. Tracked actual environment files refuse before
copying; the harmless tracked `.env.example` and `.envrc` are excluded. The
Python source snapshot is captured by its existing strict producer API. Both
copies are checked against source changes before further production. The
frozen Docker source remains checked through final publication.

The pipeline fetches both exact Debian profiles, then runs every existing
complete Docker producer: Node/uv; full Codex; Google Auth and GenAI SDKs;
complete default plugin; Gemini; OpenCode; original native packages; all four
Python profiles; and the full Go module closure. Named-context dependencies
are explicit and ordered. Normal lifecycle scripts, native/platform packages,
embedded UI, models, plugins, authentication and tool behavior remain owned by
those recipes. No graph is reduced to satisfy a build.

The six final runtime components go through the unchanged `runtime_artifacts.py
close` API and its exact committed tree anchors. A directory or successful
Docker exit alone cannot publish a runtime context: its closed manifest must
equal the source-owned manifest. Mismatched actual artifacts refuse; expected
digests are never calculated and silently substituted by this pipeline.

Only all-complete output is atomically published. It contains named context
directories `system`, `compiler`, `toolchains`, `codex`, `google-auth`, `genai`,
`plugin`, `gemini`, `opencode`, `native`, `python-source`, `python`, `go`, `runtime`,
plus content-only `source-inputs.json` and `verification.json`. Python contains
both default and live-listener variants and their actual four-profile
verification. The final app consumes its selected variant, plus the complete
Go context; the base consumes system and runtime contexts.

Commands are bounded and use the existing producer owner-signal/process-group
settlement implementation. Each command prints its argv before it starts and its
exit code and wall-clock seconds after it ends. Buildx output streams to the
caller's stdout as it happens; other command logs and outcomes remain in the
work directory. Each producer then prints one `{"producer", "cache",
"fingerprint", "seconds"}` line. Logs are execution evidence, never reproducible
artifact inputs.

### Reusing unchanged producers (`--cache DIR`)

With `--cache DIR` (real, absolute, disjoint from the other three paths), each
producer's extracted context is kept across runs, one entry per producer, and is
reused only when its input fingerprint matches. The fingerprint is a SHA-256 over:

- the producer's Dockerfile and every checkout path that Dockerfile `COPY`s (mode
  and content digest, taken from the frozen-source identity);
- its build arguments, platform and target;
- the full tree digest (paths, types, permission bits and bytes) of every named
  context it consumes.

Base images, toolchains and network fetches are pinned by digest inside those
files, so equal fingerprints mean the build would read the same inputs. A
recipe whose reads cannot be enumerated is never cached: `ADD`, a bind mount
from the build context, a cache mount, or a glob, JSON-form or heredoc `COPY`.
A rebuilt producer changes its context bytes, so every consumer misses too.
Since local builds are not bit-reproducible, a reused context has the same
inputs as a fresh build, not necessarily the same bytes.

A fresh build is cached only after the frozen source re-verifies against the
identity the fingerprint used. Reuse hard-links the entry and re-digests it
against the digest recorded at store time; a mismatch evicts the entry, and the
producer runs. The runtime closure, frozen-source verification and atomic
publication run unchanged on every run. A failed run keeps the producers that
finished, so a retry resumes from the failure. Producers still run serially:
the owner-signal boundary installs main-thread signal handlers, and the
nightly `--prune-build-cache` would race concurrent builds. Nightly does not
pass `--cache`.
The three-hour per-producer watchdog accommodates the measured full Codex
release build (about 85 minutes); it is a bound, not a timing-success claim.

Source-shaped orchestration tests use synthetic Docker exports and establish
ordering, refusal and publication behavior only. Actual Docker producer,
assembled images, exact-image sandbox/nightly execution and remaining native
conformance must still earn their own terminal evidence. Building contexts
does not publish or deploy an application image.

## Immutable local base and application chain

After contexts close, build the base and all four application variants:

```sh
python3 -I -B scripts/runtime_clis/build_image_chain.py \
  --project "$PWD" --contexts /absolute/closed-contexts \
  --work /absolute/new/image-work --output /absolute/new/image-receipt \
  --revision "$(git rev-parse HEAD)"
```

Every path must be real and disjoint. The source inventory must equal the
inventory that produced the contexts; a later checkout or changed runtime
manifest refuses before starting a registry. Revision must name the actual
checkout HEAD. The builder freezes the same tracked bytes again, retains the
existing seven-file base fingerprint helper and labels, and passes both system
and full runtime named contexts into the base build.

An owned, anonymous, loopback-only registry uses the exact official
Linux/amd64 registry image in `image-registry-inputs.json`. Only this disposable
registry receives the base image. Buildx returns a distribution/OCI manifest
digest, and the builder independently reads that exact manifest from the owned
loopback port, with proxies and redirects disabled. Both response bytes and
`Docker-Content-Digest` must match. A Docker configuration/image ID is never
substituted for a manifest digest. The resulting full registry/repository@sha256
reference becomes the application's required `BUTLERS_BASE_IMAGE` argument.

Production and development each consume the default and live-listener Python
contexts separately, plus the full closed Go context. The ordinary production
image keeps `butlers-app:<revision>`; the other three local tags carry the
profile and target suffix. Each loaded image ID and Linux/amd64 platform must
agree with its Buildx metadata. No image is published to an external registry,
and no deployment, egress activation or credential move occurs.

The registry is removed by its unique name, exact container ID and owner label,
including its anonymous storage, before a successful receipt publishes. Cleanup
settles its own command groups and defers repeat owner signals through the
inspect/remove transaction. Foreign ownership refuses removal. Ordinary build
commands use the existing signal/child settlement implementation. Local images
may remain after a partial failure, but no complete image-chain receipt exists.

Nightly's `exact-image-sandbox` now calls the complete context producer and this
chain before its existing installed-file exporter and real-kernel sandbox
checks. Its 330-minute watchdog is explicitly provisional: the full Codex
builder alone measured 5,111 seconds on the conformance host. The timeout
registry preserves the earlier calibration provenance and records additional
serial producer/image and evidence reserves; no hosted whole-job timing or p95
is claimed. A context or image command exiting zero does not establish cold
native/auth/tool/WebSocket behavior, direct-443 trapping or CNI enforcement.

The source-owned runtime tree anchors remain mandatory. This caller deliberately
refuses a newly produced artifact that differs from them; actual final producer
identity reconciliation and complete Docker/nightly execution remain separate
delivery evidence, rather than a missing-input condition counted as completion.
