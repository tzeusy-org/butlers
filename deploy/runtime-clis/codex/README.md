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
   connection and TLS classifications retain their original behavior. Connector
   deadlines (including DNS stalls) are connection failures too; an accepted-hop
   response timeout keeps its original timeout category.

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

`Dockerfile.codex-artifacts` is the fixed-path public producer for that same
release command. It uses the pinned Python and Rust child images, the original
Debian compiler profile, and the separate `compiler-inputs.json` OpenSSL
development supplement. The supplement pins its predecessor manifest and
starting/finished package inventories; it does not change the runtime profile.
Prepare its three public archives with the existing system fetcher:

```sh
python3 scripts/runtime_clis/fetch_system_artifacts.py \
  --manifest deploy/runtime-clis/codex/compiler-inputs.json \
  --output /path/to/new/codex-compiler-artifacts
docker buildx build --platform linux/amd64 -f Dockerfile.codex-artifacts \
  --build-context system-artifacts=/path/to/complete/system-artifacts \
  --build-context codex-compiler-artifacts=/path/to/new/codex-compiler-artifacts \
  --target artifacts --output type=local,dest=/path/to/new/codex-artifact .
```

The public seed downloads the exact source archive and complete CMake wheel,
applies the unchanged four-patch series, and vendors the full locked Cargo graph,
including its non-target package sources. Hashes, modes, links and file population
are recorded without task paths or timestamps. Every redirect is refused. The
subsequent actual networkless layer checks the entire seeded graph, compiler
inventory, fixed identity flags and original source before compiling. Compiler
children receive an explicit environment and an empty builder-owned home;
ambient proxy, credential, registry and loader configuration is not forwarded.

Only successful compilation atomically publishes the GNU executable and
content-only provenance. Path and timestamp normalization preserve the original
release profile, debug information, default features and native dependency graph.
The bare artifact must still be assembled with the complete original published
wrapper, platform assets, code-mode host, voice resources, licenses and launchers.
An executed fixed-builder reproduction, installed native operation checks and
final image evidence remain required separately. Neither this recipe nor its
bounded archive tests establish those results.

Focused native tests cover original logical URL/cookie identity, streaming 429
and retry headers, every selected redirect class, hop outage, response drop,
early 401 upload cancellation and the native WebSocket handshake/frame path.
The owning upstream HTTP suite also has six literal TLS classification/fallback
failures in this host configuration. The pristine pinned upstream source with
the same lock normalization reproduces those same six failures; they remain
failures and are neither removed nor treated as a passing native suite.

The compiler supplement includes pinned Debian `patch` 2.8-2 alongside the
three OpenSSL development/runtime archives. The unchanged source preparer
executes this tool; it is a build dependency, not a replacement patch engine.
The complete four-archive install is bound to the declared original compiler
profile and measured output dpkg status. A contained independent-root control
runs that local installer and the actual four-patch preparer; absence of the
tool fails rather than selecting a different source or toolchain.
