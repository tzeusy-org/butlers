## ADDED Requirements

### Requirement: Helm chart directory structure

The chart SHALL live at `/home/tze/gt/homelab/mayor/rig/helm/butlers_local/` and follow the existing homelab convention:

```
helm/butlers_local/
├── Chart.yaml           # name: butlers, type: application
├── makefile             # includes ../base.mk; NAMESPACE=butlers, REPOSITORY=.
├── values.yaml          # all configurable values
├── values.dev.yaml      # dev-namespace overrides (optional)
└── templates/
    ├── _helpers.tpl
    ├── namespace.yaml
    ├── configmap-roster.yaml       # per-butler roster ConfigMaps
    ├── deployment-butler.yaml      # templated across enabled butlers
    ├── deployment-dashboard.yaml
    ├── deployment-connector.yaml   # templated across enabled connectors
    ├── service-butler.yaml
    ├── service-dashboard.yaml
    ├── job-migrate.yaml            # pre-install/pre-upgrade hook
    ├── externalsecret.yaml
    ├── ingress-dashboard.yaml      # Tailscale Ingress
    └── ingress-api.yaml            # Tailscale Ingress
```

#### Scenario: Chart follows homelab makefile convention
- **WHEN** a user runs `make template` in `helm/butlers_local/`
- **THEN** Helm renders all templates to `_templates/` without errors using the default `values.yaml`

#### Scenario: Chart installs to dedicated namespace
- **WHEN** a user runs `make deploy`
- **THEN** all resources are created in the `butlers` namespace (configurable via makefile `NAMESPACE`)

### Requirement: Per-butler Deployment templating

The chart SHALL use a single Deployment template that iterates over a `butlers` map in `values.yaml`. Each entry specifies: `enabled`, `port`, `image`, `resources`, `env`, and `rosterConfigMap`.

#### Scenario: Enable a subset of butlers
- **WHEN** `values.yaml` has `butlers.switchboard.enabled: true`, `butlers.general.enabled: true`, and `butlers.health.enabled: false`
- **THEN** Helm renders Deployment and Service resources for switchboard and general, but NOT for health

#### Scenario: Butler Deployment uses correct command
- **WHEN** a butler Deployment is rendered
- **THEN** the container command SHALL be `["run", "--config", "/etc/butler"]` with the entrypoint from the Docker image (`uv run butlers`)

#### Scenario: Roster ConfigMap mounted read-only
- **WHEN** a butler Deployment is rendered
- **THEN** the roster ConfigMap is mounted at `/etc/butler` with `readOnly: true`

### Requirement: values.yaml schema

The `values.yaml` SHALL expose the following top-level sections:

- `global.image` — Docker image repository and tag (default: `ghcr.io/your-org/butlers:latest`)
- `global.imagePullPolicy` — default `IfNotPresent`
- `postgres` — connection parameters (`host`, `port`, `database`, `sslmode`, `secretName` referencing the k8s Secret with `username`/`password` keys)
- `s3` — blob storage parameters (`endpoint`, `bucket`, `region`, `secretName`)
- `otel.endpoint` — OTLP collector URL (default: `http://alloy.lgtm:4318`)
- `oauth` — Google OAuth bootstrap (`clientId`, `clientSecret` from ExternalSecret, `redirectUri`)
- `butlers.<name>` — per-butler config (`enabled`, `port`, `resources`, `env`)
- `connectors.<name>` — per-connector config (`enabled`, `resources`, `env`, `secretName`)
- `dashboard` — dashboard config (`enabled`, `port`, `staticDir`, `apiKey`, `ingress`)
- `migration` — migration Job config (`enabled`, `backoffLimit`, `activeDeadlineSeconds`)
- `ingress` — Tailscale Ingress config (`enabled`, `hostname`, `dashboardPath`, `apiPath`)

#### Scenario: Default values produce a valid chart
- **WHEN** `helm template` is run with only default values
- **THEN** rendering succeeds and produces valid YAML for all enabled resources

#### Scenario: Per-butler environment overrides
- **WHEN** a butler has custom `env` entries in values
- **THEN** those environment variables appear in the Deployment's container spec in addition to the global infrastructure env vars

### Requirement: makefile targets

The makefile SHALL include at minimum: `template`, `deploy` (`helm_install`), `undeploy` (`helm_uninstall`), `redeploy`. It SHALL include `../base.mk` and set `CONTEXT`, `NAMESPACE`, `RELEASE_NAME`, `REPOSITORY`.

#### Scenario: make deploy installs the chart
- **WHEN** a user runs `make deploy`
- **THEN** `helm upgrade --install` is executed with `--create-namespace` against the configured namespace
