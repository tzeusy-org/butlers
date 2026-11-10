# Service plane: named relays and authenticated callers

RFC 0038 D7 retires the generic `route` proxy. Traffic between trusted daemons
that does not belong to a goal stays on the **service plane**: the control
plane's service listener (`butlers-control`, port 41100), never the session
listener (41199). This page describes what replaces `route` and how callers are
authenticated. Everything here is dark until the cutover switch is turned on.

Requirement: `REQ-butler-switchboard-008`
(`openspec/changes/generalist-lead-focused-delegation/specs/butler-switchboard/spec.md`).

## What replaces each route caller

`src/butlers/core/service_plane/inventory.py` lists every production call
site that reaches another butler through `route`, each with one disposition.
`tests/contracts/test_service_plane_inventory.py` scans production code with
the AST and fails if a call site is missing from the inventory, or if the
inventory names a site that no longer calls `route`. The scan follows aliases:
an `import ... as`, an assignment, `partial(route, ...)`, `call_fn=route`, and
`call_tool(name="route")` all count. Files that the deletion inventory's
section 1.1 removes whole are skipped. A new caller is classified there first.

| Disposition | Meaning |
| --- | --- |
| `named_relay` | Moves to a named relay (below). |
| `messenger_delivery` | Switchboard `deliver()` to Messenger. Messenger authenticates Switchboard as the caller. |
| `dashboard_call` | An owner action. It goes through the service listener, authenticated by the signed dashboard control client. |
| `control_plane_internal` | Switchboard's own deterministic code calling one fixed domain tool in process, under that call's own authentication. There is no daemon-to-daemon hop to authenticate, and it must not become a generic proxy. |
| `removed` | Dies at the switch with the lane or module that owns it. |

## Named relays

A relay (`src/butlers/core/service_plane/relays.py`) forwards to exactly one
`(butler, tool)` target with a fixed argument schema that forbids extra
fields. `relay_call` refuses, before contacting the target, when:

- the caller is not authenticated (`caller_unauthenticated`);
- the authenticated caller is not on the relay's allowlist (`caller_not_allowed`);
- the target tool is LLM-presentable, or is missing from the presentation
  inventory (`target_llm_presentable`);
- the arguments fail the schema (`args_invalid`).

The caller is a `ServiceCaller(workload, daemon)`. The workload is the
authenticated sending process. The daemon name narrows the allowlist inside
that workload but is never authority on its own. A body field such as
`source_butler` is never read as the caller. A refusal never falls back to the
generic proxy.

### Served relays

Switchboard serves `POST /internal/service-plane/relay/{name}` (body
`{"args": {...}}`) beside `/health` on its service listener
(`src/butlers/core/service_plane/endpoint.py`, wired in
`service_plane/wiring.py`). It is not an MCP tool, and the control plane's
session listener does not serve it. It dispatches through Switchboard's own
`route()`, so registry, staleness, quarantine and routing-log behaviour are
unchanged; the relay decides only who may reach which target with what.
Callers use `service_plane/client.py`, presenting their workload's
relay-audience token and their daemon name. While the switch is off the route
answers 404 to everything and callers keep the legacy route path.

| Relay | Target | Allowed callers | Notes |
| --- | --- | --- | --- |
| `qa.report_finding` | QA `report_finding` | every roster daemon on `butlers-up` except Switchboard | `source_butler` is set from the caller; stale QA still receives (`allow_stale`). |
| `domain_event.receive` | a subscriber's `receive_domain_event` | publishers of a domain-event contract on `butlers-up` | The caller must publish the event type's contract and the target must be one of its `permitted_subscribers`; `source_butler` is set from the caller. |

Still to come (they need a service-only, non-presentable target first):
Travel's read of Health's medication snapshot, fleet-case writes, the memory
catalog fetch, the calendar response fence and the contacts owner lookup.

## How callers are authenticated

Decision (bu-2c1g7v, RFC 0038 "Details settled during the dark build"): two
mechanisms, and no new private key.

| Hop | Mechanism |
| --- | --- |
| Dashboard API to the control plane | The signed dashboard control client (`RUNTIME_PROBE_CONTROL_SIGNING_KEY`, already approved), under its own audience. Lands with the dashboard rerouting. |
| butlers-up daemons to a named relay; Switchboard to Messenger delivery | A projected Kubernetes ServiceAccount token, verified offline against the cluster issuer's JWKS (`src/butlers/core/service_plane/workload_identity.py`). |

**The authenticated principal is the workload, not the daemon.** The domain
daemons share the `butlers-up` process (RFC 0038, "Switchboard moves out of the
shared `butlers-up` process"), so no credential inside it can tell one daemon
from another. A projected token names the pod's ServiceAccount; the daemon name
travels beside it in `x-butlers-workload-daemon` and only narrows an allowlist
of `(workload, daemon)` pairs. `REQ-butler-switchboard-008` still says "the
calling daemon's service identity". The code implements the workload reading,
dark, pending owner approval of the matching amendment in PR #5713 (it narrows
the spec text, so it does not adopt by conformance review).

A token verifies only when all of these hold:

- RS256 signature under a `kid` from the issuer JWKS (no `none`, no HMAC);
- `iss` is the configured issuer and `aud` is exactly `[<this audience>]`;
- `exp` is in the future, `iat`/`nbf` are not ahead by more than 30 s, and the
  validity window is at most one hour (the chart requests 600 s);
- `kubernetes.io.namespace` is this namespace, the token is bound to a pod, and
  `sub` names the same ServiceAccount as the claim;
- the ServiceAccount is a known workload (`butlers-control`, `butlers-up`,
  `dashboard-api`).

Each use has its own audience: `butlers.service-relay.v1` for relays and
`butlers.messenger-delivery.v1` for Messenger delivery. Neither is a broker call
assertion (Ed25519 JWS under `X-Butlers-Assertion`, `aud` equal to a domain):
the two verifiers share no header, key, algorithm or audience, so one can never
be replayed as the other.

**Key set.** Each verifier reads `https://kubernetes.default.svc/openid/v1/jwks`
with its pod's own API token. Kubernetes binds the
`system:service-account-issuer-discovery` ClusterRole to every authenticated
ServiceAccount by default; on the dev k3s cluster, an unprivileged
ServiceAccount read the discovery document (issuer
`https://kubernetes.default.svc.cluster.local`) and a one-key RS256 JWKS. The set
is cached for 5 minutes; a failed refresh serves the last set for up to an hour,
then every verification refuses. TokenReview is not used: it needs a
cluster-scoped `create` grant and a round trip per call, and offline
verification needs neither.

**Messenger delivery.** notify.v1 delivery shares the `route.execute` tool name.
Today Messenger checks `request_context.source_endpoint_identity`, a string in
the request body. With the switch on, Messenger admits a delivery only from
`(butlers-control, switchboard)` with the delivery audience
(`src/butlers/core/service_plane/messenger_delivery.py`), and Switchboard's
`route()` attaches the token mounted at
`/var/run/secrets/butlers/workload/butlers.messenger-delivery.v1`.

**Deployment.** `servicePlaneAuth.enabled` in the Helm chart (default `false`,
requires `butlersControl.enabled`) gives `butlers-control` and `butlers-up`
their own ServiceAccounts, sets `BUTLERS_SERVICE_PLANE_AUTH=1`, and mounts one
token per workload: the delivery token into `butlers-control`, the relay token
into `butlers-up`. Each workload holds only the audience it presents. Butlers holds no private key for
this: the API server signs and the kubelet rotates.

**Reachability.** This adds one site edge per verifying workload and no new chart-peer edge:

| Hop | Under egress profiles |
| --- | --- |
| `butlers-control` to Messenger in `butlers-up` (delivery, token in a header) | Unchanged: the existing `butlers-control` → `butlers-up` 41101-41112 peer. |
| `butlers-up` domain daemons to Switchboard's relay endpoint (`/internal/service-plane/relay/<name>` on 41100) | Unchanged: the existing `butlers-up` → `butlers-control` 41100 peer. The domain-event relay targets the daemon's configured `switchboard_url` (its Switchboard client's URL), the same as the QA relay; the reconciliation sweep inside Switchboard stays in process. |
| `butlers-up` (Messenger) and `butlers-control` (Switchboard, verifying relay callers) to the API server, for the JWKS | New `kube-apiserver` site on both profiles, port 6443, optional. The API server is not a chart peer (on k3s it listens on the node), so the deployment locates it with `egressProfiles.sites.kube-apiserver` (a /32). NetworkPolicy matches the endpoint after the `kubernetes.default` Service is translated, hence 6443, not 443. The chart refuses `servicePlaneAuth.enabled` under egress profiles without that site, because every verification would refuse. |

The issuer string (`servicePlaneAuth.issuer`, `DEFAULT_ISSUER`) is compared
against the token's `iss`; it is never dialed. Both are reviewed as references
in `scripts/egress-profile-coverage.json`.

## Limits

- **No per-daemon separation inside `butlers-up`.** Any daemon in that process
  can present any daemon name. A relay allowlist narrows by name, but only the
  workload is authenticated, until domain daemons are split into separate
  processes.
- **Domain LLM sessions in `butlers-up` can act as any domain daemon.** The
  domain daemons spawn their LLM CLIs inside the `butlers-up` container, which
  mounts the relay token. A session that can read files or run commands can
  read it and call any relay as any daemon name in the allowlist. That is still narrower than today: the service listener (41100)
  now accepts any caller that can reach it, with no authentication at all.
  The owner question in #5713 weighs this.
- **Switchboard's own LLM sessions can read the delivery token.** Today they
  run inside `butlers-control`, in the same container that mounts it. Until
  sessions move to the session host (`bu-kiuh8v`), a Switchboard session that
  can read files or run commands can present the token as Switchboard to
  Messenger.
- **Namespace admins can mint these tokens.** Anyone who can create pods,
  create ServiceAccount tokens (`serviceaccounts/token`), or exec into a pod in
  the namespace can obtain a valid token for these workloads. These permissions
  are already equivalent to reading the workloads' secrets, and they stay with
  the owner.
- **Replay inside the token's window.** A token is a bearer credential for up
  to its 600 s lifetime and is not bound to a request. The service listener is
  plain HTTP inside the cluster, so a token seen on the pod network can be
  replayed until it expires. This code never logs a token.

## The switch

`BUTLERS_SERVICE_PLANE_AUTH` (Tier 0, exactly `"1"` to enable, default off)
gates every check on this page. While it is off nothing reads a token header, a
token file or the key set, and the legacy paths run unchanged.
