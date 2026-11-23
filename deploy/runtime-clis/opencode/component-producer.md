# Complete OpenCode component producer

`Dockerfile.opencode-artifacts` uses the pinned Python/compiler base, complete
Node input and the separately produced `default-plugin-artifacts` context. That
context exports its seed directly at its root. Compiler inputs include the
original pinned patch utility. No operator credentials or runtime egress mode
are supplied to the build.

Both producer phases mount the complete declared Node component read-only at
`/opt/node`. Ordinary dependency lifecycles write their outputs in the build
workspace; they cannot mutate the pinned toolchain input. The complete Node
identity check remains unchanged.

The public-seed phase verifies the original source/eleven-patch series, complete
published wrapper and Linux/x64 package archives, official Bun ZIP, full model
catalogue and every provider logo. It runs ordinary `bun install
--frozen-lockfile` and the original `bun run packages/opencode/script/build.ts
--single`. The stock script retains its normal platform installs, native assets,
embedded web UI, workers, compile settings and version smoke. No lifecycle,
plugin, model, authentication or tool feature is removed. `OPENCODE_RELEASE` is
absent. This preparation is explicitly NONFINAL. It also closes every installed
`node_modules` tree at the root and original workspaces as an explicit,
independent dependency artifact. The record binds every original workspace,
complete source inventory, normalized payload mode, literal relative link and
Linux/amd64, glibc 2.41, Bun 1.3.14 target. Directory modes become `0755`; regular
payloads become `0755` when executable or `0644` otherwise, preserving native
executables while removing inherited host permission bits. Every copied payload
is compared with its producing installed tree before preparation completes.
Only dependency directories cross this boundary: source files, compiled UI and
CLI build outputs are not carried by the dependency artifact.

Native dependency lifecycles which spawn the bare `node-gyp` command use a
builder-owned symlink to the original executable bundled in the verified full
Node/npm input. Its private tools directory precedes the ordinary builder PATH;
missing, replaced or extra tools refuse. The existing pinned Node headers and
Python remain the build inputs. No lifecycle is skipped or dependency replaced.

The complete model catalogue and logos are delivered by the source-owned data
snapshot described in `model-inputs.md`. The producer verifies the archive,
finite member set, nonexecutable modes and every original public payload hash
before atomic staging. It makes no live model request and accepts no replacement
from the mutable upstream API. The full original catalogue remains embedded.

The build phase requires a real networkless namespace/layer, verifies every
closed preparation input, recreates the exact patched source, restores only the
verified complete dependency directories, and repeats the unchanged full
`bun install --frozen-lockfile` and stock build commands. Every destination is
preflighted before restoration; ambient installed directories and symlinked
workspace parents refuse. The root and dependency lifecycles retain Bun's normal
semantics: the public phase proves their original cold installation, while the
offline phase may naturally reuse unchanged installed dependencies. This is not
cold Bun installation evidence. UI and CLI compilation run afresh from original
source. Python's four cold profiles and final cold runtime startup remain
separate requirements. Bun 1.3.14 has no install `--offline` flag; containment is
the actual `RUN --network=none` boundary. Its stock installers may rewrite JSONC
spelling, so every semantic field of the full original lock is compared using
the pinned Bun parser; its original regular-file kind and mode remain bound.
Pinned Bun also changes the installed workspace launcher at
`packages/opencode/bin/opencode` from its archive mode `0755` to `0777`.
After verifying that exact path's complete original payload and every other
original path, the producer restores only that known mode change to `0755`.
Unexpected permissions, changed launcher bytes, links and any other original
source drift refuse before restoration. All original source paths remain
byte/mode bound after this declared normalization.

Assembly retains the entire original published package and target assets,
replaces only the declared target executable, and executes the original
postinstall against that complete target. It independently copies the complete
installed workspace and actual 141-package default-plugin seed beside the
original `bin/opencode.exe`. Full graph, metadata, canonical modes, relative
links, producing source identities and plugin lock are checked before output.
Only a complete verified component is atomically published. Provenance contains
content identities; external timestamped conformance receipts are not inputs.

The plugin producer's provenance binds its exact Node/toolchain declaration and
full Node tree rather than the enclosing runtime manifest. This avoids making
an installed seed depend on the OpenCode output hash which contains that same
seed. It grants no alternate Node tree, dependency or destination.

Source controls and task-builder conformance are separate from an actual Docker
build. Final compiled native/auth/tool behavior, whole-runtime installation,
images, nightly delivery, default-port direct traps and CNI conformance remain
required. This source does not deploy or activate egress.
