# Offline WhatsApp bridge build inputs

`Dockerfile.go-artifacts` uses the same digest-pinned Go 1.25.11 builder as the
application. Its public phase downloads the complete original module graph
from `proxy.golang.org`, checked against `sum.golang.org`. The download runs on
a separate copy of `go.mod` and `go.sum`: checksum-only additions never replace
the original source locks.

```bash
docker buildx build --file Dockerfile.go-artifacts --target artifacts \
  --output type=local,dest=/absolute/new/go-artifacts .
```

The subsequent `RUN --network=none` phase uses only the stdlib producer,
original bridge source, and that module cache. It checks Go 1.25.11/Linux amd64
and an actual loopback-only network with no external route before closing the
context. It copies every cache file and directory into an independent context,
normalizes directory modes to 0755 and regular files to 0644 or 0755, and refuses
links, special files, and privileged regular files. Executable semantics are
preserved. Original source directory setgid is not copied.

Before publishing atomically, the producer verifies the entire context, runs
`go mod verify`, `go mod tidy -diff`, and compiles the unchanged bridge with
`-mod=readonly -trimpath -buildvcs=false` and its original `-s -w` linker flags.
The bridge is never executed. Source and dependency locks remain unchanged.

The published context contains only `modules/` and `provenance.json`. The latter
uses `butlers.go-artifacts/v1`: exact builder digest/Go/platform, original
`go.mod` and `go.sum` SHA256, and a globally sorted complete inventory of relative
paths, types, canonical modes and file SHA256. The metadata is a consistency
check under the trusted immutable build-input boundary, not a signature or an
independent supply-chain attestation. Actual offline compilation establishes
cache completeness for the original bridge.

Supply this complete output as `--build-context go-artifacts=/absolute/path` to
the application build. The application validates it before its own offline
bridge build. `scripts/compose.sh` requires `BUTLERS_GO_ARTIFACTS`; the explicit
CLI equivalent is `butlers deploy --go-artifacts /absolute/path`, alongside its
required immutable base and Python context. No launcher creates or downloads
missing build inputs. Unit controls and a real contained source-producer run
do not by themselves establish a completed Docker or application image.
