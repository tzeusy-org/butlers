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

The pinned system package set is `debian-system-inputs.json`: one complete public
Debian archive population, its signed-index provenance, the original Python base
inventory, and separate runtime/compiler selections. Supply the complete set as
regular files under the named `system-artifacts` context's `debs/` directory.
Prepare that context from the exact public archive records without installing them:

```sh
python scripts/runtime_clis/fetch_system_artifacts.py \
  --manifest deploy/runtime-clis/debian-system-inputs.json \
  --output /path/to/new/system-artifacts
docker build --build-context system-artifacts=/path/to/new/system-artifacts \
  -f Dockerfile.base -t butlers-base .
```

The fetcher uses only the manifest's public Debian locations over HTTPS, with no
proxy or credential discovery, and publishes only the complete verified set.
The offline installer
refuses missing, extra, changed or symlinked archives and a different starting
package inventory before invoking APT. It passes only pinned local archive paths,
uses `--no-download` with repository sources disabled, and verifies the installed
package versions. Run it inside the pinned base with the builder network disabled:

```sh
python scripts/runtime_clis/install_system_artifacts.py \
  --manifest deploy/runtime-clis/debian-system-inputs.json \
  --artifacts /opt/system-artifacts/debs --profile runtime
```

Use `--profile compiler` in the separate PID1 helper builder. Archive validation
alone is not an installation or final image receipt. Retain the public input set,
actual offline install result and final image attestation separately.

The pinned uv distribution is recorded in `toolchains.json`. Build separate
production and development Python environments from the frozen lock with the
requested extras, retaining their editable install of the intended `/app/src`.
An owned public seed can be converted to the application's named
`python-artifacts` context with:

```sh
python scripts/runtime_clis/close_python_artifacts.py \
  --project /path/to/project --wheelhouse /path/to/verified/wheelhouse \
  --cache /path/to/owned/uv-cache --constraints /path/to/build-constraints.txt \
  --output /path/to/new/context --extra live-listener
```

Omit `--extra` for the ordinary application profile. The producer copies independent
regular bytes, preserves executable payloads as `0755` and other files as `0644`,
dereferences cache links only within their declared input root,
and refuses escaping/cyclic links or a preexisting output. It inventories every
input and records the exact project/lock and production/development groups/extras.
It performs no network operation or install. The constraints file records the
seeded build backends; it is not an unsupported `uv sync` flag or an environment
variable enforcement claim. The image validates the complete context and then
installs with offline/no-index access to that closed cache and wheelhouse. Each
profile still requires its actual fresh offline installation and seal evidence.

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
