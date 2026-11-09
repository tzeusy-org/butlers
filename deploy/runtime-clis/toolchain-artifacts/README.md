# Node and uv component producer

Build the data-only producer from the repository root:

```sh
docker buildx build --file Dockerfile.toolchain-artifacts --target artifacts \
  --output type=local,dest=/tmp/butlers-toolchain-components .
```

The first layer fetches the exact public Node archive and uv wheel declared in
`toolchains.json`. Proxies and redirects are refused. It preserves the complete
Node distribution, including npm, corepack, headers, documentation and licenses.
The unchanged uv acquisition helper retains the whole original wheel, its twelve
payload files and provenance. Independent copies of uv and uvx form the existing
two-file runtime component; neither executable is run by this producer.

Archive extraction bounds population and expanded bytes, preflights parent
paths and entry types, preserves bounded relative links, and materializes valid
hardlinks as independent regular files. Canonical modes match the unchanged
runtime consumer. Final publication uses no-replace atomic rename and removes
only owned temporary output on refusal.

The second layer runs `verify` with `RUN --network=none`. Verification performs
no acquisition, installation or tool execution. It rechecks complete payloads,
canonical metadata bytes and modes, link containment, independent inodes, source
archive hashes, executable hashes and the two reviewed component tree digests.
The scratch output has `components/{node,uv}`, complete `inputs/{node,uv}`, and
normalized `provenance.json` using `butlers.toolchain-components/v1`.

The provenance binds the full toolchains manifest and only the relevant
Node/npm/uv versions, target and component expectations from `runtime-inputs.json`.
Unrelated CLI component or historical receipt changes do not change these two
components. Expected tree drift refuses publication; it does not update the
consumer manifest. Paths, timestamps and task receipts are excluded from output
metadata.

The stdlib entrypoint also accepts `--phase produce|verify --toolchains PATH
--runtime-inputs PATH --output PATH`. Its helper siblings are explicit immutable
source inputs. Source tests and retained-archive data checks establish component
closure only. Actual installed versions, full six-component assembly, image
builds, nightly delivery and native CLI operations require their separate checks.
