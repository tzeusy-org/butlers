# Runtime CLI transport sources

These inputs build explicit `request-v1` executors for the existing runtime CLIs.
Logical origin URLs remain the inputs to auth, cookies, model/provider selection,
network permits, and application error handling. Only the selected wire request
uses the per-workload `egress-gateway` listener. The protocol and its authority
boundary are specified in [egress profiles](../../docs/operations/egress-profiles.md).

A patch series binds the public source archive, every touched original and patched
file, ordered patch bytes, and the complete patched source tree. Prepare it in a
new output directory before building:

```sh
python scripts/runtime_clis/prepare_vendor.py \
  --series deploy/runtime-clis/codex/series.json \
  --archive /path/to/verified-codex-source.tgz \
  --output /path/to/new/codex-source \
  --receipt /path/to/preparation.json
```

The command performs no download or build. It refuses archive/context/patch/output
drift, extraction outside its temporary tree, and an existing output. Preparation
receipts explicitly report `build_performed: false`. Retain source archives and
original locks alongside the separately attested build inputs and artifact set.
A build requires the declared toolchain, target, full locked dependencies and
native assets. Neither a prepared source tree nor a version smoke check proves
that a native CLI operation uses the selected executor.

The shared TypeScript executor uses a private pinned Undici dispatcher for the internal hop and
bounded streaming interfaces for WHATWG fetch and Gaxios. It is copied verbatim
into each patched vendor source and its digest belongs in that vendor's series.
It never replaces global fetch, modifies an auth store, or selects a provider.
Off mode delegates to the existing terminal implementation. An isolated npm alias
keeps the vendors' existing Undici versions unchanged. Exact internal entry points
avoid both Bun's built-in Undici shim and the upstream package root's global
dispatcher initialization. Fresh-process controls verify that import boundary. Vendor integration,
native operation coverage, immutable builder/assets, final sandbox manifests and
offline image closure require their own artifact evidence.
