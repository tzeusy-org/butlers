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
inventory names a site that no longer calls `route`. A new caller is
classified there first.

| Disposition | Meaning |
| --- | --- |
| `named_relay` | Moves to a named relay (below). |
| `messenger_delivery` | Switchboard `deliver()` to Messenger. Messenger authenticates Switchboard as the caller. |
| `dashboard_call` | An owner action. It goes through the service listener, authenticated by the signed dashboard control client. |
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
their own ServiceAccounts, sets `BUTLERS_SERVICE_PLANE_AUTH=1`, and mounts the
delivery token into `butlers-control` only. Butlers holds no private key for
this: the API server signs and the kubelet rotates.

## The switch

`BUTLERS_SERVICE_PLANE_AUTH` (Tier 0, exactly `"1"` to enable, default off)
gates every check on this page. While it is off nothing reads a token header, a
token file or the key set, and the legacy paths run unchanged.
