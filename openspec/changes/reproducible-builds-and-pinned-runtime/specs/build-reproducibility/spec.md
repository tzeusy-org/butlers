## ADDED Requirements

### Requirement: Committed lockfile is the single source of the Python dependency graph

A committed `uv.lock` SHALL be the single source of truth for the resolved Python
dependency graph. The lockfile SHALL be git-tracked (not gitignored). The same
source commit SHALL resolve to the same dependency graph regardless of when or
where the build runs, because installs read the committed lock rather than
re-resolving against upstream indexes.

The `[tool.uv]` `required-environments` resolver hint (the x86_64-linux
cryptography-wheel constraint) SHALL be preserved; it remains a valid lock-time
constraint even though a committed lock fixes the resolution at install time. Any
in-repo documentation that asserts builds run with "NO committed lockfile" SHALL
be corrected to match the committed-lock reality.

#### Scenario: Lockfile is tracked in git

- **WHEN** `git ls-files uv.lock` is run at the repository root
- **THEN** it lists `uv.lock` (the lockfile is tracked)
- **AND** `uv.lock` does not appear as an ignored entry in `.gitignore`

#### Scenario: Same commit yields the same resolved graph

- **WHEN** a frozen/locked install is performed twice from the same commit
- **THEN** both installs produce identical resolved package versions for the
  Python dependency graph
- **AND** neither install re-resolves dependencies against upstream indexes

#### Scenario: Documentation matches committed-lock reality

- **WHEN** the `[tool.uv]` configuration block in `pyproject.toml` is inspected
- **THEN** no comment asserts that builds run with no committed lockfile
- **AND** the `required-environments` resolver hint is still present

### Requirement: CI and Docker install with frozen/locked sync

Every Python install path that produces a runtime or test environment SHALL
install from the committed lockfile in frozen/locked mode and SHALL fail if the
lockfile is missing or out of sync with `pyproject.toml`. This applies to the CI
workflow and to the Docker image build.

#### Scenario: CI installs from the frozen lockfile

- **WHEN** the CI workflow installs dependencies
- **THEN** it performs a frozen/locked `uv sync` (it does not re-resolve fresh)
- **AND** the job fails if `uv.lock` is absent or inconsistent with `pyproject.toml`

#### Scenario: Clean checkout builds the Docker image

- **WHEN** the Docker image is built from a fresh clone with no pre-existing local
  `uv.lock`
- **THEN** the `COPY ... uv.lock` step succeeds because the lockfile is committed
- **AND** `uv run --frozen` has a committed lock to honor at runtime

#### Scenario: Stale lockfile is rejected

- **WHEN** `pyproject.toml` declares a dependency not reflected in `uv.lock`
- **THEN** the frozen/locked install fails rather than silently re-resolving

### Requirement: LLM CLI runtime toolchain is pinned and auditable

The runtime base image SHALL install the LLM CLI tools the spawner depends on
(`@anthropic-ai/claude-code`, `@google/gemini-cli`, `@openai/codex`,
`opencode-ai`) and the Node.js major version at explicit pinned versions, never at
a floating `latest`. The runtime image SHALL expose an auditable manifest of the
installed CLI versions so a session's toolchain is recorded and a CLI upgrade is a
deliberate, reviewed change.

#### Scenario: Global CLIs are version-pinned

- **WHEN** the runtime base image's npm global-install step is inspected
- **THEN** every installed LLM CLI package specifies an explicit version (e.g.
  `package@x.y.z`), with no unpinned `latest` install
- **AND** the Node.js major version used to build the image is explicitly pinned

#### Scenario: Installed CLI versions are auditable

- **WHEN** the built runtime image is queried for its toolchain manifest
- **THEN** the recorded version of each installed LLM CLI is reported
- **AND** the reported versions match those declared in the build configuration

#### Scenario: CLI upgrade is a reviewed change

- **WHEN** an LLM CLI version is bumped
- **THEN** the change appears as an explicit edit to the pinned version in the
  build configuration (reviewable in a commit), not as an implicit drift between
  two builds of the same commit

### Requirement: Service container images are pinned

Service container images referenced by the Compose stacks SHALL be pinned to a
specific tag (or digest) rather than a floating `latest`, so the same source
commit brings up the same service versions. A documented process SHALL govern how
these pins are updated.

#### Scenario: No floating service image tags

- **WHEN** the Compose files (`docker-compose.yml`, `docker-compose.observability.yml`)
  are inspected for service `image:` references
- **THEN** no service image uses a floating `latest` tag
- **AND** each external service image is pinned to a specific version tag or digest

#### Scenario: Image-pin updates are documented

- **WHEN** a maintainer needs to update a pinned service image
- **THEN** a documented procedure describes how to bump the pin and validate it

## Source References

- Non-Negotiable Rule 4 (the daemon is deterministic infrastructure; it must be
  testable, debuggable, and predictable) — reproducible builds and a pinned
  runtime are a direct corollary: the same commit must yield the same dependency
  graph, toolchain, and services, so behavior is attributable to a commit rather
  than to silent upstream drift.
- `about/craft-and-care/engineering-bar.md` — engineering hygiene and the
  definition of "done" for non-trivial work; build/runtime reproducibility is a
  baseline expectation of that bar.
