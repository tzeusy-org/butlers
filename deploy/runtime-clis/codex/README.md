# Codex 0.159.2

`series.json` binds upstream commit
`ff6aec96948b70d94983af2641a6b67c94faeff5` and its public source archive.
The four ordered patches have separate purposes:

1. Normalize the 159 internal lockfile package versions from the shipped `0.0.0`
   to the source workspace's `0.159.2`. External package versions, sources,
   checksums, dependency edges and lock metadata remain identical. Stock Cargo
   accepts the resulting lock with `--locked`.
2. Route the existing HTTP factories, route-aware pool and transport executor
   through the selected gateway after acquiring the original logical network
   permit. Preserve original cookie scope, streaming bodies, cancellation,
   error URLs and off-mode TLS fallback. Selected requests avoid ambient/PAC
   routing and request/body logging.
3. Route the native WebSocket dialer through the explicit WSS gateway handshake.
   Keep the original logical permit, key, auth headers, cookies, frame machinery,
   WebSocket configuration and cancellation/revocation lifecycle. Existing
   explicit internal-loopback connections retain their separate path.
4. Classify a failed selected gateway connection as a gateway service failure.
   Retain its underlying logical URL and use the standard bounded stream retry
   path. It must not enter the native unbounded network-wait branch. Off-mode
   connection and TLS classifications retain their original behavior.

Build from the prepared source with Rust 1.95.0, the locked graph, default features
and the target recorded in the series. The source commit is embedded explicitly:

```sh
STABLE_GIT_COMMIT=ff6aec96948b70d94983af2641a6b67c94faeff5 \
  cargo +1.95.0 build --locked --release --manifest-path codex-rs/Cargo.toml \
  -p codex-cli --bin codex --target x86_64-unknown-linux-gnu -j2
```

The GNU target is the declared local native compatibility build. Production
builder/libc compatibility and the installed artifact set require separate
closure evidence. The final binary must be hashed after the complete build;
this series does not advertise an artifact that has not been built.

Focused native tests cover original logical URL/cookie identity, streaming 429
and retry headers, every selected redirect class, hop outage, response drop,
early 401 upload cancellation and the native WebSocket handshake/frame path.
The owning upstream HTTP suite also has six literal TLS classification/fallback
failures in this host configuration. The pristine pinned upstream source with
the same lock normalization reproduces those same six failures; they remain
failures and are neither removed nor treated as a passing native suite.
