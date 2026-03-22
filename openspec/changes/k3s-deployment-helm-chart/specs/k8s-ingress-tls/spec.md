## ADDED Requirements

### Requirement: Tailscale Ingress for dashboard

The chart SHALL include a Tailscale Ingress resource that exposes the dashboard (frontend + API) on the tailnet with automatic HTTPS.

#### Scenario: Dashboard accessible via Tailscale hostname
- **WHEN** the chart is deployed with `ingress.enabled: true` and `ingress.hostname: butlers`
- **THEN** the dashboard is accessible at `https://butlers.<tailnet-name>/` via Tailscale

#### Scenario: API accessible at path prefix
- **WHEN** `ingress.apiPath: /butlers-api` is configured
- **THEN** API requests to `https://<hostname>/butlers-api/api/*` are proxied to the dashboard-api Service

### Requirement: Tailscale Ingress annotations

The Ingress resources SHALL use the `tailscale.com/expose: "true"` annotation and the Tailscale Ingress class, matching the pattern used by the CNPG PostgreSQL chart's managed services.

#### Scenario: Tailscale Operator picks up the Ingress
- **WHEN** the Ingress resource is created
- **THEN** the Tailscale Operator creates a tailnet device and provisions a TLS certificate

### Requirement: OAuth callback URL as Helm value

The Google OAuth redirect URI SHALL be configurable via `oauth.redirectUri` in `values.yaml`, defaulting to `https://<ingress.hostname>.<tailnet>/butlers-api/api/oauth/google/callback`. This value is passed as `GOOGLE_OAUTH_REDIRECT_URI` env var to the dashboard-api Deployment.

#### Scenario: OAuth callback matches ingress hostname
- **WHEN** `ingress.hostname: butlers` and the tailnet is `parrot-hen.ts.net`
- **THEN** `GOOGLE_OAUTH_REDIRECT_URI` is set to `https://butlers.parrot-hen.ts.net/butlers-api/api/oauth/google/callback`
