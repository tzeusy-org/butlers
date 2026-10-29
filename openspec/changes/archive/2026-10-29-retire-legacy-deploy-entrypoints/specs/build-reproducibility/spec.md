## MODIFIED Requirements

### Requirement: Service container images are pinned

ID: REQ-build-reproducibility-001
Source: bu-dss7he.14
Scope: v1-mandatory

Service container images referenced by the Compose stacks SHALL be pinned to a
specific tag (or digest) rather than a floating `latest`, so the same source
commit brings up the same service versions. A documented process SHALL govern how
these pins are updated.

#### Scenario: No floating service image tags

- **WHEN** the Compose files (`docker-compose.yml` and its retained development overlays)
  are inspected for service `image:` references
- **THEN** no service image uses a floating `latest` tag
- **AND** each external service image is pinned to a specific version tag or digest

#### Scenario: Image-pin updates are documented

- **WHEN** a maintainer needs to update a pinned service image
- **THEN** a documented procedure describes how to bump the pin and validate it
