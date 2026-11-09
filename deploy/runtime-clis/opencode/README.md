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

The lockfile retains all 3,122 original package records. The two added aliases
are the pinned shared Undici and the already-existing exact ws source/SRI. No
plugin, model, optional native asset or lifecycle script is removed.

## Subsequent installed build and conformance

A complete OpenCode install/build has not run. The integrator must
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
The unchanged build script retains its native asset installation steps; a full
frozen install/reproducible installed-output check is still pending. Validate
actual device/OAuth/refresh, legacy pool HTTP fallback and core Effect HTTP/WS,
selected/off-mode startup, frame/close/cancellation, native tools and outage/no
fallback on the compiled CLI. The new seven-case error-boundary file ran with actual pinned Effect/schema,
Bun and twelve lock-bound mandatory dependencies in an isolated network namespace.
It covers selected failure redaction, bounded/truncated bodies, redirect refusal,
native off-mode frames and the existing HTTP executor's diagnostics. This is a
partial runtime fixture, not the complete installed graph or native CLI. The
original Effect-layer fetch control and whole legacy/caller suites still require
the complete installed graph.

This is an off-by-default source foundation. Four-CLI, image, CNI, inventory,
operational credential custody and activation acceptance remain separate and
incomplete. It does not expose an alternate operator path or activate egress.
