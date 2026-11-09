# Complete fixed default-plugin producer

`Dockerfile.default-plugin-artifacts` reproduces the installed default-plugin
seed consumed by OpenCode's selected, fresh default configuration. It keeps
the original plugin 1.17.7 and its public tool/TUI exports. The four exact root
dependencies are `@opencode-ai/plugin@1.17.7` and OpenTUI core, keymap and solid
at 0.3.4. This seed's resolved `solid-js@1.9.12` is distinct from OpenCode's
unchanged upstream source lock, which resolves 1.9.10.

The checked-in package and complete package-lock are immutable inputs. Public
seeding runs ordinary `npm ci` with all lifecycle scripts and retains its
complete public npm cache. The observed ordinary Linux/glibc install contains
130 packages. Twelve separately declared, complete official npm archives are
retained with their lock SHA512, archive SHA256, size and original metadata
identities. The exact `@opentui/core-linux-x64-musl` package is already present
in this ordinary install. Its entire canonical mode/content/link inventory
must match an independent extraction of the original SRI-bound archive; it
is preserved without overwriting or executing it. Only the eleven precisely
declared absent packages are appended as data. No optional lock entry,
foreign-platform asset, lifecycle
hook or public export is removed. The complete seed contains all 141 locked
packages, each with the original package name and version. The earlier task
prototype used `npm install --save-exact`, reported 129 installed packages,
and lacked the musl archive in both its public and offline caches. Node/npm
bytes and the final lock are identical. The lock omits that record's libc
field although its pinned public metadata says musl. A cache/lock selection
difference explains the observed staging change plausibly; npm internals
were not independently traced. Final graph identity remains unchanged.

The public layer is NONFINAL. The separate `RUN --network=none` verifies the
actual route state and closed input inventory before any package operation.
It independently copies the cache, repeats fresh `npm ci --offline` with all
hooks, verifies the exact already-present musl payload and appends the other
eleven archives as data. All twelve original archives remain closed inputs.
It uses the complete
pinned Node 22.23.4/npm 10.9.9 component and full original Bun 1.3.14 ZIP.
Bun acquisition allows only the explicit GitHub release to HTTPS
`release-assets.githubusercontent.com` transition, once. Every npm archive
and metadata response refuses redirects. Signed asset URLs are never stored
in provenance or propagated through acquisition exceptions.

All ten Bun and nine Node plugin/SDK export families must import successfully
under network isolation, with exact runtime versions and positive terminal
markers. The only Node exclusion is the Bun-specific plugin TUI export,
which remains required under Bun. These imports do not execute vendor account,
authentication or network tool operations.

```sh
docker buildx build -f Dockerfile.default-plugin-artifacts \
  --build-context system-artifacts=/path/to/system-artifacts \
  --build-context toolchain-artifacts=/path/to/toolchain-components \
  --output type=local,dest=/path/to/new/default-plugin .
```

The final directory is published atomically without replacing an existing
output. Its `seed.json` has exactly the existing eight fields: schema,
package, version, node, npm, package_lock_sha256, entries and provenance.
The complete mode-inclusive payload inventory excludes `seed.json` and sorts
by literal path globally, including nested names that a DFS walk misorders.
Relative links and executable data remain intact. Provenance contains only
public content, recipe, graph and actual import identities; cache clocks,
task receipts, host paths, timestamps and checkout HEAD are excluded.

SIGINT/SIGTERM and command failures settle the owned command session before
the producer leaves. Strict population, hash, mode, route, input-drift and
positive import checks precede publication. Synthetic source tests establish
these orchestration boundaries; actual public/offline builds, the selected
OpenCode consumer, final component identity, installed native behavior and
whole image delivery remain separate requirements.
