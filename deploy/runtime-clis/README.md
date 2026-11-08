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
A vendor can also declare separately built artifact inputs. For
[Gemini](gemini/README.md), first build the [Google GenAI SDK](js-genai/README.md),
then copy its exact archive into the prepared source:

```sh
python scripts/runtime_clis/materialize_inputs.py \
  --manifest deploy/runtime-clis/gemini/artifact-inputs.json \
  --root /path/to/prepared/gemini-source \
  --input genai-sdk=/path/to/google-genai-1.30.0.tgz \
  --receipt /path/to/materialized.json
```

Missing, changed, undeclared, escaping and symlinked artifacts are refused.
Existing differing files are preserved. Materialization performs no build and
does not replace the source preparation receipt.
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

The pinned uv distribution is recorded in `toolchains.json`. Build separate
production and development Python environments from the frozen lock with the
requested extras, retaining their editable install of the intended `/app/src`.
After the complete install, seal each environment with
`scripts/runtime_clis/python_environment.py seal --environment <venv> --project <project>
--manifest <output>`, adding its declared `--group` and `--extra` selections.
The seal records project/lock hashes, interpreter identity, installed distribution
metadata and `.pth` files. It records an already built environment; it does not
replace uv’s frozen installation or establish artifact/image provenance.

At startup, use `check` or `run` with the same environment, project and immutable
manifest. `run ... -- <butlers arguments>` validates first and executes the
installed CLI directly with isolated Python. It invokes neither uv nor an
installer. A changed mounted project/lock, dependency inventory, interpreter or
editable import target refuses startup; ordinary edits to the intended source
remain visible. Rebuild or explicitly revalidate the selected complete closure
offline before accepting changed dependency inputs. The trusted image digest
binds package bytes; this startup metadata check is not a replacement for that
image attestation. Production/dev image wiring and cold-cache startup each need
separate execution evidence.
