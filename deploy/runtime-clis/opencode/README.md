# OpenCode request-v1 source overlay

This finite series targets upstream OpenCode 1.17.7 at
`4ed4f749e644ffb5b279fb30b7b915e743d80142`. Apply it with the repository's
`scripts/runtime_clis/prepare_vendor.py` and the digest-bound public source
archive in `series.json`. Zero-context hunks follow the existing repository series
convention; exact complete-file preimage hashes bind their context. The tool checks each preimage, patch, output and the
entire resulting source tree, including modes and symlinks. `LICENSE.upstream`
retains the upstream MIT notice; the complete replay also retains upstream's
other notices and native/UI inputs.

The first three patches contain the adopted shared318 request executor, explicit
OpenAI device/OAuth/refresh and provider fetch integration, Effect HTTP injection,
selected WebSocket transport, and complete local UI catalogue/logo inputs.
Auth URLs, audience, account selection, refresh singleflight, original tools,
provider catalogue and version/channel remain upstream interfaces. The known
OpenAI plugin's custom fetch still performs its existing auth/URL rewriting
before the terminal request executor. No global fetch, dispatcher or WebSocket
constructor is replaced by production code.

Selected WebSockets use the exact pinned `ws@8.21.0` source under a private alias.
Bun otherwise substitutes its global WebSocket facade for the bare `ws` import.
The real ws `finishRequest` hook sends the handshake exclusively through the
private Undici dispatcher: it never ends the dormant node:http request. This
also avoids Bun's HTTP upgrade facade while retaining ws's accept/subprotocol/
extension validation, client masking, frame and close machinery. Off-mode keeps
the original caller constructor and environment proxy behavior. A failed
handshake preserves logical URL, status, headers and up to 64 KiB of provider
error body; redirects and gateway refusals expose no redirect entity or target.
An absolute opening deadline, bounded body read and cancellation close owned
request/dispatcher/socket state. Core Effect errors and legacy APICallError retain
logical request identity; native full-caller execution remains required.

The fourth patch constructs real Effect HTTP-context schema instances and reuses
upstream HTTP diagnostic sanitizers before attaching selected WebSocket errors.
Sensitive header/query/structured fields and literal request-secret echoes are
redacted from the returned context/cause. Configured redacted header names also
protect echoed values. Benign logical paths/query values, status, Retry-After,
harmless headers/body and the connector's truncation signal remain available.
It does not change off-mode transports, auth selection or the bounded handshake.

The fifth patch pins the app's `ghostty-web` declaration to the original locked
commit `20bd3613f59dfc0f088a9aec498db2fa1a08b768`. The upstream `#main` declaration
otherwise caused pinned Bun to propose a different source/checksum and reject a
frozen install. The resolved package record, archive integrity and every other
locked package remain unchanged. No newer dependency source is adopted.

The sixth patch completes selected-mode typed diagnostics for constructor and
JSON preparation failures, absent/late handshakes, closed or closing sockets,
post-open events and rejected sends. Each uses the HTTP request-secret redactors
for logical URLs and error messages; native off-mode errors retain their original
identity. Sanitized provider bodies are additionally capped at 64 KiB of UTF-8
bytes after redaction, preserving the benign prefix without an incomplete final
code point. `bodyTruncated` records either the input or sanitized-output limit.
The exact Effect/schema fixture covers these error families and selected healthy
frames alongside native off-mode, HTTP, redirect, deadline and cancellation
survivors. This is bounded source/runtime-fixture evidence, not full installed
caller or stock CLI/build conformance.

The seventh patch removes refused URL userinfo and fragments from selected
structured diagnostic identity. The shared HTTP redactors also mask their raw
and decoded echoes and explicitly named username/password fields, retaining safe
origin/path/model and benign metadata. Native off-mode behavior is unchanged.
The genuine pinned Effect/schema fixture now has 36 passing cases across the two
owning files, including encoded/malformed userinfo, fragments, short named
credentials, echoed values, off-mode identity and the existing UTF-8 byte cap.
These are isolated source-bound fixture controls; full installed callers, stock
build and operational conformance remain separate requirements.

The eighth patch completes selected diagnostic JSON-string handling. Whole
JSON string tokens are decoded only to mask known request-secret echoes, then
re-encoded without retaining escaped quotes, backslashes, controls or Unicode.
Complete sensitive string fields also handle escaped field names. Standard
known serializer spellings remain masked in incomplete diagnostic bodies; short
known values redact as whole strings without broad substring scrubbing. Native
off-mode behavior and the post-redaction UTF-8 byte cap remain unchanged.
The genuine Effect/schema and WebSocket owning files now pass 48 cases, with all
36 prior cases retained. This is bounded source/runtime-fixture evidence, not
stock built-artifact, installed caller or operational conformance.

The lockfile retains all 3,122 original package records. The two added aliases
are the pinned shared Undici and the already-existing exact ws source/SRI. No
plugin, model, optional native asset or lifecycle script is removed.

## Subsequent installed build and conformance

A full frozen install has completed on the pinned Bun/Node host fixture, including
the original lifecycle scripts and platform-selected native packages. Native
addon compilation used npm's bundled node-gyp and the pinned Node archive's
headers; host compiler execution is not a builder/image attestation. The full
stock CLI/UI build and installed native caller coverage remain pending. The integrator must
first verify/materialize every input in `build-inputs.json`, including the full
235-provider / 8,939-model catalogue and 235 exact SVG logos. A missing local
catalogue/logo pair, missing logo, empty logo or malformed provider key refuses
instead of fetching a replacement. The local build inputs contain no credentials.

Once the single heavy build lane is available, use the pinned Bun 1.3.14 and the
original install/build lifecycle, from the prepared source root:

```sh
export OPENCODE_VERSION=1.17.7 OPENCODE_CHANNEL=latest
export MODELS_DEV_API_JSON=/absolute/path/to/verified/api.json
export OPENCODE_MODELS_LOGOS=/absolute/path/to/verified/logos
bun install --frozen-lockfile
(cd packages/llm && bun test test/gateway-websocket.test.ts test/gateway-egress.test.ts test/gateway-websocket-errors.test.ts)
(cd packages/ui && bun test script/provider-icons.test.ts)
(cd packages/opencode && bun test test/plugin/openai-ws.test.ts)
bun run packages/opencode/script/build.ts --single
```

Use the original target's native dependencies and complete embedded UI. Do not
add `--skip-embed-web-ui`, substitute an empty model set, drop plugins/tools,
ignore lifecycle scripts or count the cheap helper probes as a native build.
The unchanged build script retains its native asset installation steps; a
reproducible installed-output check is still pending. Validate
actual device/OAuth/refresh, legacy pool HTTP fallback and core Effect HTTP/WS,
selected/off-mode startup, frame/close/cancellation, native tools and outage/no
fallback on the compiled CLI. The extended twenty-case error-boundary file and
nine retained WebSocket controls ran with actual pinned Effect/schema, Bun and
twelve lock-bound mandatory dependencies in an isolated network namespace.
They cover selected failure identity across error paths, post-sanitize encoded
byte limits, redirect refusal, native off-mode frames, cancellation and the
existing HTTP executor's diagnostics. This is a
partial runtime fixture, not the complete installed graph or native CLI. The
original Effect-layer fetch control and whole legacy/caller suites still require
the complete installed graph.

The ninth patch bounds the selected session retry schedule to three retries
following the initial operation. Each selected delay is clamped to 0–30 seconds,
including provider millisecond, second and HTTP-date hints. The original provider
error and headers remain intact, and the final failed operation is returned to
the caller. Native off-mode retry count, backoff and provider hints remain
unchanged. Eighteen owning Effect controls cover schedule termination, delay
limits, recovery, nonretryable authorization errors and the SDK connection-error
conversion seam. Their direct schedule-step checks use no watchdog to establish
the bound; compiled-CLI outage parity remains a separate required check.

The tenth patch supplies the exact default `@opencode-ai/plugin@1.17.7`
dependency without a registry request in selected request-v1 mode. It resolves
only `../default-plugin` beside the original installed `bin/opencode.exe`;
standalone platform binaries and off-mode retain their original path. Only the
unchanged single default dependency request and a fresh package layout (no
package.json, package-lock.json or node_modules) qualify. Existing/custom plugin
projects retain normal npm behavior, and other config files are preserved.

The eight-field `butlers.opencode-default-plugin/v1` metadata binds Node 22.23.4,
npm 10.9.9, the actual separate npm lock, complete sorted file/directory/link
inventory and reviewed producer provenance. The closed root dependency set is
plugin 1.17.7 plus `@opentui/core`, `@opentui/keymap` and `@opentui/solid`, each
exactly 0.3.4, supplying the published TUI export's peers. It admits no other
root dependency or version range. This separate graph's actual transitives are
not asserted equal to the unchanged upstream monorepo's 3124-package lock.

Core npm uses the existing validated gateway configuration without importing the
application layer. Under its existing install flock it validates the whole seed,
canonical modes, hashes, closed relative links, lock/catalog/package/export
coverage and complete destination plan before copying real bytes and links with
exclusive creation. The ordinary install completeness check then observes that
real graph. Applicable missing/malformed/drifted seeds refuse with no registry
fallback. No ambient seed override, parent search, global dispatcher or HOME
change is introduced. Caught copy failures clean only this attempt's newly
created paths; this does not certify crash-atomic multi-file installation.

Owning contained Bun/Effect controls exercise actual `Npm.install`, source-package
identity, copied plugin exports, independent files, privacy-neutral config
preservation, full-seed/destination refusals, concurrent flock ownership and
caught write failure. The four existing npm controls retain real local-package
reification and .npmrc omit behavior. Synthetic seed fixtures establish source
behavior only: the producer must still supply and validate the complete public
plugin/SDK/TUI graph, actual imports, native operation and final image closure.

This is an off-by-default source foundation. Four-CLI, image, CNI, inventory,
operational credential custody and activation acceptance remain separate and
incomplete. It does not expose an alternate operator path or activate egress.

The eleventh patch keeps the local default-seed validation/copy uninterruptible
inside the existing npm flock. Its filesystem Promise must settle, including
rollback on a failed copy, before interruption can release the lock; registry
reification and native off-mode behavior retain their original semantics. Two
positioned first-exclusive-write controls cover ordinary and interrupted copies,
lock ownership until settlement, no later writes, complete output and preserved
config. The 51 prior owning cases remain, for 53 passing source-bound cases.
The real separate 141-package seed also passes the actual Npm.install seam with
7,934 payload records checked and registry reification trapped. These are contained
source/graph controls; the corrected compiled CLI and final image remain separate.
