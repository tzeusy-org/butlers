# Public Python artifact producer

`scripts/runtime_clis/produce_python_artifacts.py` turns the current frozen Butlers
project and the reviewed nine-wheel `build-lock.toml` into two closed artifact
contexts. It does not build an application image, run services, or certify runtime
network policy. The canonical image producer must execute the real pipeline; the
owning source tests use synthetic installations and do not establish completeness.

## Source and target inputs

The `snapshot` phase runs with ordinary host Python, without uv or the target
runtime. Supply a genuine checkout root. It copies current tracked packaging
inputs, including tracked dirty bytes, all `src/butlers` package data, explicitly
referenced project metadata, and these three validators:

- `scripts/runtime_clis/close_python_artifacts.py`
- `scripts/runtime_clis/python_environment.py`
- `scripts/runtime_clis/python_entrypoint.py`

`source-inputs.json` binds the complete file population, modes, hashes, checkout
HEAD and content identity. No Git directory, untracked files, `.env`, `.venv`, or
ambient cache is copied. Unknown backends, Hatch hooks, dynamic metadata and source
symlinks refuse rather than silently omit build inputs. A snapshot must retain its
exact declaration and file/directory population; arbitrary directories are refused.

For example, using caller-owned paths with existing parent directories:

```sh
python3 scripts/runtime_clis/produce_python_artifacts.py \
  --phase snapshot --project /checkout --work /work/snapshot \
  --output /inputs/source \
  --backend-lock /checkout/deploy/runtime-clis/python-artifacts/build-lock.toml
```

The `--work` and `--backend-lock` arguments remain required for a uniform CLI but
snapshot does not create work, read the backend lock, install packages, or check
the target runtime. Work, output and project paths must be disjoint real paths.
The snapshot is the named source context for Docker. Copy the producer script and
backend lock explicitly as image build inputs as well; they are not package data.

Both remaining phases require CPython **3.12.13**, Linux x86-64, glibc **2.41**, and
caller-supplied official uv **0.12.22**. Tool version and binary hashes are recorded;
the caller remains responsible for the official binary acquisition/provenance.
Use an empty owned backing at the existing `HOME` path. The producer preserves
that value and constructs a minimal child environment; no ambient proxy, uv
configuration, credential, virtual environment or cache settings pass through.
The source may be the original checkout or its fully validated closed snapshot.

## Two-stage acquisition and verification

Run `public-seed` in the target image with a fresh work directory:

```sh
python3 /tools/produce_python_artifacts.py \
  --phase public-seed --project /inputs/source --work /work/python \
  --output /artifacts/python --backend-lock /inputs/build-lock.toml
```

This phase is explicitly **NONFINAL**. `/artifacts/python` must not exist and is
not created. The work directory retains the source snapshot, full wheel/sdist
payloads, fresh uv cache, exports, command logs and `seed.json`. No host cache is
used. Only the actual four frozen uv exports determine target runtime inputs;
marker/tag selection loads `packaging` 26.3 directly from its hash-verified wheel.
Complete locked CPU torch URLs and the pyaes source archive are retained. All nine
backend wheel records, including hashes, sizes and public URLs, are fixed by the
reviewed lock. HTTPS acquisition has a finite transfer bound, disables proxy
discovery and refuses redirects, unexpected authorities, name collisions and
hash/size mismatches. The allowed public artifact hosts are
`files.pythonhosted.org`, `download.pytorch.org`, and `download-r2.pytorch.org`.

The full development plus live-listener seed uses ordinary frozen uv sync with
`--offline --no-index --find-links` and the explicit fresh cache. The generated
`build-constraints.txt` documents the exact backend inventory. uv 0.12.22 sync has
no `--build-constraints` option: the producer never passes one and does not claim
that a constraints environment variable enforces it. The complete closed
wheelhouse/cache and no-index semantics provide the build inputs.

In a separate Docker layer, the caller must use **`RUN --network=none`** to invoke
the same producer with `--phase offline-verify`, the same source/work/backend lock,
and the same still-absent output. No privileged namespace operation is performed
inside the image. Each actual verification child checks its own `/proc` network
state for loopback only and no external IPv4/IPv6 gateway before target checks or
installation. Merely using uv's offline flag is not the containment proof.

The four independent checks are:

| Context | Profile | Groups | Extras |
| --- | --- | --- | --- |
| default | production | none | none |
| default | development | dev | none |
| live-listener | production | none | live-listener |
| live-listener | development | dev | live-listener |

Each child reuses the existing `verify_build` API, creates a fresh source copy,
cache and environment, performs normal frozen offline sync, then uses the existing
seal/check APIs and compares actual installed names/versions with its selected
frozen export plus Butlers. The child uses the exact parent-validated uv binary
and records matching runtime identities. UV/build subprocesses share the outer
supervised child session so cancellation reaches that session; command receipts
record waited parents and process-group settlement. Surviving identities refuse
success. Logs remain under the owned work directory on failure.

Only all four positive receipts, unchanged source/backend/producer bytes, and
unchanged complete seed material allow one atomic output-directory rename. The
final directory contains `default/`, `live-listener/`, and `verification.json`.
Each context uses the existing `butlers.python-artifacts/v1` format. Complete
regular-file copies dereference only in-root cache links and preserve executable
cache modes; contexts share no file inodes with the mutable seed. A closed hashed
superset is allowed. No runtime, backend, optional asset or source graph is cut.
A failure publishes no partial final context; a rerun needs a fresh work directory
and new seed rather than silently repairing an interrupted verification.

## Integration and evidence boundary

The canonical Docker caller owns source-context creation, exact target/uv supply,
empty HOME backing, public seed execution, real network-disabled verification,
image assembly and final runtime tests. Nightly wiring, script inventory and CI
hook changes belong to that integration; this four-file source slice does not
modify them. Preserve both default and audio production/development selections.

Run bounded source controls with:

```sh
python3 tests/scripts/test_produce_python_artifacts.py -v
```

These exercise the actual orchestration, source declarations, refusal/failure
ordering, existing context validators, seal API contract and publication atomicity.
Synthetic operations are explicitly confined to tests. Selector-only checks with
previously verified wheel/exports establish selection, not acquisition, cold
installation, Docker isolation, image startup or full source15 acceptance. Those
remain mandatory current-artifact gates. No activation, credential, deployment,
owner-smoke or deferred static-key authority is supplied by this producer.
