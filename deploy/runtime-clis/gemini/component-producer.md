# Complete Gemini component producer

`Dockerfile.gemini-artifacts` uses the pinned Python/Debian compiler, complete
official Node component and both independently reproduced SDK package outputs.
It preserves the original patched workspace, every installed development and
runtime dependency, original package-lock, lifecycle scripts and full build.
It neither prunes the graph nor substitutes an npm-published Gemini binary.

The public seed executes ordinary `npm ci` and `npm run build` with a private
empty HOME and explicit public npm configuration. That stage is NONFINAL.
The separate `RUN --network=none` verifies all closed inputs, freshly prepares
the same source and SDK artifacts, and repeats the full install/build offline.
The pinned source commit supplies `GIT_COMMIT`; builder paths and epoch are
fixed. The full package graph and assets remain in the resulting component.

Only two generated CONTRIBUTING documentation symlinks are relocated from the
fixed builder's absolute source path to their equivalent relative targets.
Their target bytes and exact source/target identities must match first. This
preserves their behavior under the installed runtime prefix. No source file,
dependency declaration, lock node, auth factory, tool or runtime mode is removed.

```sh
docker buildx build -f Dockerfile.gemini-artifacts \
  --build-context system-artifacts=/path/to/system-artifacts \
  --build-context codex-compiler-artifacts=/path/to/compiler-supplement \
  --build-context toolchain-artifacts=/path/to/toolchain-components \
  --build-context genai-sdk-artifacts=/path/to/genai-sdk-output \
  --build-context google-auth-artifacts=/path/to/auth-sdk-output \
  --output type=local,dest=/path/to/new/gemini-component .
```

The output contains `components/gemini` and content-only provenance, not npm
cache timestamps or task receipts. Its computed whole-component digest must
still be reproduced and committed to the final six-component installer input
before image delivery can be accepted. Source tests do not certify a full
build, native auth/tool behavior, final images or network enforcement.

The target-native optional fallback is part of this complete graph. Every child
uses `npm_config_nodedir=/opt/node`, whose entire official distribution and
headers are already pinned and verified. The unchanged node-gyp lifecycle uses
those headers instead of downloading another header archive. After each normal
install/build, the producer verifies the original locked `node-pty` and `nan`
versions and their native/header payloads, then loads `node-pty` with the pinned
Node executable and verifies its native interface. An npm success that dropped
this optional target package cannot publish a complete component.

Both Docker phases mount the whole declared Node component read-only at
`/opt/node`, including npm and its bundled build helpers. Lifecycle scripts can
use those tools and headers but cannot add generated files to the verified
toolchain. The component identity checks remain strict; generated toolchain
files are neither excluded nor accepted as a new Node distribution.

The first retained producer run exited successfully after node-gyp's separate
header extraction failed and npm removed that optional package. That run proves
its recorded commands and copied payload only; it does not prove preservation
of the complete target-native fallback. The corrected recipe must earn a fresh
actual lifecycle/native-load result before its whole-tree anchor is adopted.
