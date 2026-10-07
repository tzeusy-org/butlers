## MODIFIED Requirements

### Requirement: LLM CLI runtime toolchain is pinned and auditable
- The runtime base image SHALL install the LLM CLI tools the spawner depends on (`@anthropic-ai/claude-code`, `@google/gemini-cli`, `@openai/codex`, `opencode-ai`) and the Node.js major version at explicit pinned versions, never at a floating `latest`. The runtime image SHALL expose an auditable manifest of the installed CLI versions so a session's toolchain is recorded and a CLI upgrade is a deliberate, reviewed change.
- The current /opt/cli-versions.txt is the expected installed image-version authority; version-3 runtime-cli-sandbox-inputs.json binds paths but is not a package-version manifest. Serving evidence compares separately validated adapter-emitted CLI version with a safe bounded read of that actual asset. Dockerfile pins alone are build intent, not runtime observation. Missing either side gives unknown; a mismatch yields diagnostic cli_version_drift without automatic upgrade, provider --version execution, credential change, work rejection or deployed-fleet claim.

ID: REQ-build-reproducibility-001
Source: bu-s11n0s.6 original outcome; protected 6cfc4eeac9003c0321a892d02b9865113cdccb72; serving protocol P1-P9
Scope: v1-mandatory

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

