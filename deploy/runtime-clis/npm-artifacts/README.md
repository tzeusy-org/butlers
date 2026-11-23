# Patched SDK package producer

`Dockerfile.npm-artifacts` produces the two SDK archives consumed by the Gemini
materializer: `google-auth-library` 10.9.0 and `@google/genai` 1.30.0. Select the
former with `VENDOR=google-auth-library`, the latter with `VENDOR=js-genai`.
The public source archive, finite patch series, complete source tree, Node
executable and bundled npm version are checked against the existing repository
inputs. The full official Node image supplies npm and its original toolchain
files.

The public-seed layer runs the original locked `npm ci` and lifecycle scripts,
retaining the entire npm and browser caches. It is explicitly nonfinal. A second
layer recreates the complete pristine patched source at the same fixed path,
runs `npm ci --offline` and the original prepare/pack hooks with
`RUN --network=none`, and checks all original source entries. No dependencies,
browser assets, package exports, optional graph entries or lifecycle hooks are
removed. Cache symlinks must resolve inside their complete cache root; source
test-fixture symlinks remain bound literally by the existing patch series.

The build requires the same verified `system-artifacts` and
`codex-compiler-artifacts` named contexts as the Codex recipe. These contexts
must include the public patch tool used by the unchanged preparer. They are
build tools, with no grant to run credentials or vendor account operations.
Each layer retains its existing HOME value with an empty owned tmpfs backing;
child commands receive explicit npm configuration and no ambient proxy, token
or Node options.

The scratch output contains `<vendor>/<archive>.tgz` and content-only
`provenance.json`. Publication requires the archive to match the exact digest
already declared in `gemini/artifact-inputs.json`. A reproducibility mismatch
fails before publication; changing the expected digest requires an actual
artifact comparison and affected materializer review. Acquisition timestamps
and npm log/cache index bytes are verified within the seed/build boundary but
are excluded from the reproducible output metadata.

The owning tests exercise synthetic source, cache, lifecycle and publication
boundaries. They do not execute npm or establish real builder reproduction,
native CLI parity, authentication coverage, final image closure or nightly
delivery. Those checks require the actual complete pinned input contexts.

The declared SDK build closure is in `npm-artifacts/build-inputs.json`. npm
children use umask `0027`, reproducing the generated archive members' original
`0640` modes without changing the parent or published source modes. Both SDK
archive digests remain the existing materializer anchors.

GenAI's locked development dependency `test-server-sdk` 0.2.9 normally downloads
its binary on every install. The producer retains its six original members,
checksums, version, extraction, executable permissions, cleanup and lifecycle.
A declared patch adds only an explicit `BUTLERS_TEST_SERVER_ARCHIVE` branch;
without that input the original Axios download branch remains. The producer
fetches the exact original release archive, verifies the upstream checksum and
creates a deterministic six-member derivative tar. Its full digest, patch and
original/derived lock identities are pinned. Only that dependency's lock
`resolved` and `integrity` fields change; root specs and the full dependency
population remain intact. Ordinary `npm ci` installs the derivative and runs
all hooks. No installed dependency files are rewritten after installation.

The public seed retains all three dependency archives and binds their complete
inventory. The networkless build verifies and reproduces the derivative,
repeats the exact two-field lock overlay, and runs the full install and pack.
Content-only provenance includes these declared inputs. All SDK source entries
are checked against the explicit prepared source plus lock overlay; final
packed bytes must still match the unchanged Gemini materializer input.
The release fetch admits one bounded HTTPS transition from the exact GitHub
release URL to `release-assets.githubusercontent.com`; npm archive redirects
remain refused. Transient signed URLs and urllib exception text are never
persisted. These are build-input acquisition rules, with no runtime gateway
policy or account-operation grant.
