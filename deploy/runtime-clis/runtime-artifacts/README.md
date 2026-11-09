# Offline runtime artifact context

`scripts/runtime_clis/runtime_artifacts.py` closes and installs reviewed, complete,
prebuilt components. It does not fetch, resolve, prune, compile, execute a runtime,
run a package manager or lifecycle hook, or create a replacement executable wrapper.
The existing V3 runtime sandbox manifest generator remains unchanged and authoritative.
Hashes and synthetic tests do not establish actual runtime/image compatibility.

The named build context is `runtime-artifacts`, with this exact shape:

```text
inputs.json
provenance.json
components/
  node/
  uv/
  claude/
  codex/
  gemini/
  opencode/
```

`inputs.json` is a separately reviewed `butlers.runtime-inputs/v1` JSON object with
exactly `schema`, `target`, `versions`, `components` and `provenance`. The target is
Python3.12.13, linux-amd64, glibc2.41. The versions are Nodev22.23.4, npm10.9.9,
uv0.12.22, Claude2.1.309, Codex0.159.2, Gemini0.66.0 and OpenCode1.17.7. Node/npm/uv
also have their public input identities in `deploy/runtime-clis/toolchains.json`.
Version declarations are not executable version checks.

`components` has exactly the six names, each with exactly `tree_sha256`. Producers
use `tree_digest(inventory(component_root))` from this helper on the complete
installed public input. `inventory` includes every descendant, with sorted paths,
file SHA256 and canonical mode, directory mode0755, and exact relative symlink
text. Files normalize to0755 if executable, otherwise0644. Set-id/sticky regular
files, special objects, unsafe paths, absolute/dangling/cyclic/escaping links and
empty components refuse. Internal relative links, including links to directories,
are retained. No dependency, auth, plugin, model, UI or asset subtree is removed.

The canonical directory-mode records are part of this final format; the original
untested handoff candidate omitted them. Prepare reviewed digests with the final
helper. Source non-executable access-bit differences are normalized, not asserted
byte-for-byte permission identity. Closed artifacts must already have exact
canonical directory/file modes, including component roots and metadata files.

`provenance` in the input manifest is a nonempty reviewed object supplying the
producer's source/build receipts. The helper treats it as bound metadata, not
executable instructions or a signature. Top-level shape, duplicate keys, nonfinite
JSON, target/version population and component digests fail closed. The generated
`provenance.json` has exactly `schema: butlers.runtime-artifacts/v1`, the SHA256 of
the **exact reviewed input bytes**, and the same six component digests.

## Close

```sh
python3 scripts/runtime_clis/runtime_artifacts.py close \
  --manifest /owned/inputs.json --output /owned/runtime-artifacts \
  --node /owned/node --uv /owned/uv --claude /owned/claude \
  --codex /owned/codex --gemini /owned/gemini --opencode /owned/opencode
```

The output must be new and must not overlap the inputs. All component identities,
required entrypoints and composed installation collisions are checked first.
The helper snapshots the reviewed manifest bytes before copying, makes independent
copies in a temporary sibling, verifies both original and copied complete trees,
rechecks the exact manifest bytes, then publishes the context by directory rename.
Failed staging is removed. There must be one owner of stable input/output paths;
the helper is not a concurrent filesystem transaction service.

## Install

```sh
python3 scripts/runtime_clis/runtime_artifacts.py install \
  --manifest /owned/inputs.json --artifacts /owned/runtime-artifacts \
  --root /owned/disposable-image-prefix
```

`--root` is mandatory. Tests always use an owned disposable prefix. An explicit
image-build root is a separate caller responsibility; these commands authorize no
host install, deployment or operational activation.

All context metadata/bytes/modes/links, required original executable layouts,
component destinations, parents and **all** launcher conflicts are validated before
any destination writes. Compatible existing real directories are reused with
inode, ownership, permissions and ACLs unchanged. Their modification/change times
naturally change when children are added. Existing files or links at any planned
output, and symlinks in parents, refuse; nothing is overwritten or deleted. Newly
created explicit and implicit directories are0755 independent of umask/inherited
setgid. Only newly created objects are chmodded. Files are independent copies,
not shared inodes; component-relative symlinks retain their original text.

| Component | Destination under root | Required original entrypoints |
| --- | --- | --- |
| node | `usr/local` | `bin/node`, `bin/npm`, `bin/npx` |
| uv | `usr/local/bin` | `uv`, `uvx` |
| claude | `usr/local/lib/node_modules/@anthropic-ai/claude-code` | `bin/claude.exe` |
| codex | `usr/local/lib/node_modules/@openai/codex` | `bin/codex.js` |
| gemini | `usr/local/lib/node_modules/@google/gemini-cli` | `bundle/gemini.js` |
| opencode | `usr/local/lib/node_modules/opencode-ai` | `bin/opencode.exe` |

The four CLI bin names are relative symlinks from `usr/local/bin` to those original
entrypoints. Node's original npm/npx links and the full trees remain intact. No
replacement script or package installation selects a different implementation.

Use an exclusive, disposable installation prefix. Planning failures perform no
prefix mutation; unexpected I/O failure/interruption while copying is not rolled
back. Discard that failed prefix/image layer rather than treating it as installed.
Complete public input assembly, actual versions/CLI operations, full graph/runtime
conformance, offline image build and all original source15 acceptance/operational
gates remain separate responsibilities of the canonical package owner.
