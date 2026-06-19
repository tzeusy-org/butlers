## Why

The same source commit does not currently produce the same runtime. The Python
dependency graph is re-resolved fresh on every build (`uv.lock` is gitignored, CI
runs unpinned `uv sync --dev`, and a clean checkout cannot honor the Dockerfile's
`COPY ... uv.lock` / `uv run --frozen`), and the LLM CLI toolchain baked into the
runtime image is installed at floating `latest` versions. A silent dependency or
CLI bump can change daemon behavior with no commit to point at — directly
violating the doctrine that the daemon is deterministic, testable, debuggable
infrastructure. This change makes builds reproducible: one commit, one runtime.

## What Changes

- **BREAKING (build process):** Commit `uv.lock` and switch every install path
  to frozen/locked sync. The Python dependency graph becomes pinned at the commit
  rather than re-resolved per build.
  - Remove `uv.lock` from `.gitignore`; git-track it.
  - Change CI's `uv sync --dev` to a frozen/locked install so CI installs the
    committed graph instead of re-resolving.
  - Ensure the Docker build's `COPY ... uv.lock` and `uv run --frozen` are honored
    by a clean checkout (the committed lock is now present).
  - Correct the stale `[tool.uv]` comment in `pyproject.toml` that asserts "NO
    committed lockfile"; preserve the `required-environments` resolver hint
    (x86_64-linux cryptography-wheel workaround), which remains a valid lock-time
    constraint.
- **Pin the LLM CLI runtime supply chain.** The global npm CLIs in the runtime
  base image (`@anthropic-ai/claude-code`, `@google/gemini-cli`, `@openai/codex`,
  `opencode-ai`) and the Node major version SHALL be installed at explicit pinned
  versions. The image SHALL emit an auditable manifest of the installed CLI
  versions so a session's toolchain is recorded. CLI upgrades become deliberate,
  reviewed commits.
- **Pin floating service images.** Service container images that currently float
  (e.g. `minio/minio:latest`, `minio/mc:latest`, `node:22-slim`, the
  `butlers-app:*` family) SHALL be pinned to specific tags (or digests), with a
  documented update process. (Lighter-weight requirement than the two above.)

## Capabilities

### New Capabilities

- `build-reproducibility`: Defines the invariant that a given source commit
  produces the same resolved Python dependency graph, the same pinned LLM CLI
  toolchain, and the same service images — covering the committed lockfile,
  frozen installs across CI and Docker, pinned/auditable runtime CLIs, and pinned
  service images.

### Modified Capabilities

<!-- None: no existing capability's requirements change. -->

## Impact

- **Build / CI:** `.gitignore`, `.github/workflows/ci.yml` (`uv sync` invocation),
  `pyproject.toml` (`[tool.uv]` comment), and a newly committed `uv.lock`.
- **Runtime images:** `Dockerfile.base` (Node + npm global CLI pins, version
  manifest), `Dockerfile` (frozen install path already present).
- **Service topology:** `docker-compose.yml`, `docker-compose.observability.yml`
  (floating image tags).
- **Runtime behavior:** the daemon's LLM CLI spawner (`src/butlers/core/spawner.py`)
  depends on the pinned CLIs; pinning removes a class of silent behavior drift.
- **No application source or schema changes.** This is build/runtime determinism
  hardening; the daemon's logic and data model are untouched.
