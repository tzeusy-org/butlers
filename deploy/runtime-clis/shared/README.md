# Selected JavaScript transport

`egress.ts` is copied identically into the patched Gemini SDK, Gemini CLI and
OpenCode sources. Its selected mode accepts the existing paired
`BUTLERS_EGRESS_MODE=request-v1` and per-workload gateway address. It retains
logical URLs, application headers, streamed bodies, abort signals and provider
status/retry headers. Every 300..399 and typed gateway refusal fails closed.
Explicit WSS mapping leaves the native vendor's handshake and frame machinery
in place; native vendor integration still requires its own tests.

The sole HTTP wire client is the pinned public Undici 7.24.5 package, installed
under the isolated `butlers-egress-undici` npm alias. The original vendor Undici
versions and off-mode dispatchers remain in their existing dependency graphs.
The exact constructor/request entry points avoid package-root initialization of
the global dispatcher, and avoid Bun's built-in shim for the bare `undici` name.
`undici-private.d.ts` describes only the immutable entry points actually used;
it does not advertise the convenience methods installed by the package root.

The private HTTP/1.1 parser has a 64 KiB header limit, a 16 KiB readable high-water
mark, 10 second connection/header budgets, a 300 second body-idle budget and a
3600 second total deadline. A fixed native TCP slab may overshoot the high-water
mark once. The paused-consumer controls enforce a 1 MiB unread-body ceiling
across both 8 MiB and 32 MiB responses. Bun 1.3.14 uses a 512 KiB slab. Its
`node:http` facade instead buffered almost the entire 8 MiB response; the same
control fails against that discarded implementation.

Body completion, cancellation and error all await the same dispatcher teardown.
Gzip/deflate/Brotli coding names are case insensitive. Supported coding chains
decode in reverse application order, with at most five codings; an unsupported
chain retains the complete wire entity. Explicit `compress: false` retains raw
bytes. Decoder errors and cancellation settle the complete pipeline. Clone URLs
retain their logical identity.
The optional Gaxios 6 view preserves its Node-readable body and node-fetch
controls. Gaxios 7 consumers use the full WHATWG response path. Actual nested
package resolution determines the adapter; a root lock entry alone does not.

Install the declared test dependency with ordinary `npm ci` in this directory.
The declared archive integrity and exact toolchain archives are retained in the
lock and [toolchain manifest](../toolchains.json). These commands exercise the
same source on the declared Node 22 and Bun 1.3.14 runtimes:

```sh
node --experimental-strip-types --test tests/runtime_clis/egress.test.ts tests/runtime_clis/import-state.test.ts
bun test --timeout 5000 tests/runtime_clis
```

Run those commands from the repository root. Their scope is this executor and
its explicit test seams, including fresh-process import sensitivity controls.
They do not claim full native CLI auth/tool parity, a production build, or an
offline installed image. Vendor source series and artifact receipts bind those
separate stages.
