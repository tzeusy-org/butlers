## 1. Commit and freeze the Python dependency graph

- [ ] 1.1 Remove `uv.lock` from `.gitignore` (line 17)
- [ ] 1.2 Regenerate `uv.lock` from `pyproject.toml` (`uv lock`) and git-add/commit it
- [ ] 1.3 Verify `git ls-files uv.lock` lists the file and it is not ignored
- [ ] 1.4 Correct the stale `[tool.uv]` comment in `pyproject.toml` that asserts builds run with "NO committed lockfile"; keep the `required-environments` x86_64-linux resolver hint intact

## 2. Switch install paths to frozen/locked sync

- [ ] 2.1 Change CI's `uv sync --dev` in `.github/workflows/ci.yml:39` to a frozen/locked install (e.g. `uv sync --frozen --dev`) so CI installs the committed graph
- [ ] 2.2 Confirm the Docker build's `COPY pyproject.toml uv.lock ./` (`Dockerfile:43`) succeeds on a clean clone now that `uv.lock` is committed
- [ ] 2.3 Confirm `uv run --frozen ...` entrypoint (`Dockerfile:68`) honors the committed lock; build the image from a fresh clone to verify
- [ ] 2.4 Add a check that a stale lock (a `pyproject.toml` dep missing from `uv.lock`) causes the frozen install to fail rather than re-resolve

## 3. Pin the LLM CLI runtime supply chain

- [ ] 3.1 Pin the Node.js major version used in `Dockerfile.base` (NodeSource setup) to an explicit version
- [ ] 3.2 Pin each global npm CLI in `Dockerfile.base:32-36` to an explicit version: `@anthropic-ai/claude-code`, `@google/gemini-cli`, `@openai/codex`, `opencode-ai`
- [ ] 3.3 Emit an auditable CLI version manifest in the runtime image (record/print installed versions, e.g. `--version` of each CLI captured at build time)
- [ ] 3.4 (Optional) Add a build smoke check that each adapter's CLI is invocable in the built image

## 4. Pin floating service images

- [ ] 4.1 Replace floating `latest` tags in `docker-compose.yml` (`minio/minio:latest`, `minio/mc:latest`, `node:22-slim`, `butlers-app*:latest` family) with specific pinned tags or digests
- [ ] 4.2 Audit `docker-compose.observability.yml` for any unpinned tags (currently pinned — confirm and keep)
- [ ] 4.3 Document the procedure for bumping a pinned service image and validating the bump

## 5. Verification

- [ ] 5.1 Build twice from the same commit and diff resolved package versions to confirm an identical dependency graph
- [ ] 5.2 Run `make check` (lint + tests) against the frozen environment to confirm no regression from pinning
- [ ] 5.3 Confirm no service `image:` reference in either Compose file uses a floating `latest` tag
