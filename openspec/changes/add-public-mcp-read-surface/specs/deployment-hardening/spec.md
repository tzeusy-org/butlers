## ADDED Requirements

### Requirement: Default-Deny Ingress For The Butlers Namespace
The Butlers Helm chart SHALL render a NetworkPolicy that selects every pod in `butlers-<env>` and denies all ingress, plus explicit allow policies for exactly the reviewed flows: the existing host-ingress and `butlers-control` policies, unchanged; in-namespace traffic to pods that have no narrower reviewed policy, including butler-to-butler and dashboard-to-daemon MCP; and scraping of declared metrics ports from the configured monitoring source. Because allowed ingress is the union of every policy that selects a pod, no new allow policy SHALL select a pod that already has a narrower reviewed policy. No policy SHALL admit ingress to any `butlers-<env>` pod from namespaces `mcp`, `mcp-gateway` or `butlers-public-<env>`. A rendered-chart contract test SHALL enforce this, including the additive union of every policy that selects each pod. This precondition SHALL land before any gateway-side allow rule points into Butlers.

ID: REQ-deployment-hardening-013
Source: RFC 0039 D8 (precondition) and D1 ([Observed] daemons serve MCP with no caller authentication); heart-and-soul/security.md deployment principles as amended by RFC 0039
Scope: v1-mandatory

#### Scenario: A gateway pod cannot reach a daemon
- **WHEN** a pod in namespace `mcp` connects to a butler daemon's MCP port in `butlers-<env>`
- **THEN** the connection SHALL be refused at the network layer

#### Scenario: Reviewed flows keep working
- **WHEN** the default-deny policy is applied
- **THEN** the dashboard SHALL still be reachable through the existing host ingress, and the dashboard API and butlers SHALL still reach each other's MCP endpoints inside the namespace
- **AND** the existing `butlers-control` session-reachability limits SHALL be unchanged

#### Scenario: A widening policy fails the chart test
- **WHEN** a chart change adds a policy whose union with existing policies admits ingress to a daemon pod from outside the namespace
- **THEN** the rendered-chart contract test SHALL fail and name the pod selector and the source

#### Scenario: The public namespace is not an allowed source
- **WHEN** the public MCP server's pod connects to any `butlers-<env>` pod
- **THEN** the connection SHALL be refused

## Source References

- Non-Negotiable Rule 3 (cross-domain access only through the control plane), `about/heart-and-soul/vision.md`.
- `about/heart-and-soul/security.md`, deployment principles (Kubernetes default-deny ingress, added by the RFC 0039 doctrine sweep).
- RFC 0008 (deployment binding), with the RFC 0039 Kubernetes-ingress addendum.
- RFC 0039 (`about/legends-and-lore/rfcs/0039-public-mcp-read-surface.md`), D8.
