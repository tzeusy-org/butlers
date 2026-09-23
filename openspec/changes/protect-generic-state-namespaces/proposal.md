## Why

The per-butler state store intentionally supports arbitrary JSON keys, and the
dashboard State tab intentionally exposes generic CRUD. Central
`dashboard-owner-auth` now protects every generic dashboard state route, so the
older claim that these routes are unauthenticated is no longer true. The
remaining boundary is still material: generic dashboard writes and the
`state_get`/`state_set`/`state_list`/`state_delete` MCP tools apply no key-specific
validation or version-CAS. A butler's model session can therefore read, replace,
or delete managed state in its own schema even when a specialized surface has a
narrower contract.

The adopted Home-presence configuration change records this limitation for
`home:presence:owner_entities`; it does not accept the risk or close it. Its
dedicated route has not been implemented. Protecting that key before the
dedicated route exists would strand the only supported configuration workflow,
while shipping only the route would preserve the generic CAS bypass.

## Proposed outcome if adopted

- Add one code-owned registry keyed by exact `(butler, key)` or a bounded
  `(butler, prefix, optional suffix)` rule. It defines generic dashboard and MCP
  read, list, write, and delete decisions separately. Request bodies, database
  rows, mutable state, and caller arguments cannot declare or override policy.
- Preserve the current generic state behavior for ordinary unclassified keys.
  Every new code-owned state namespace must declare its classification before
  shipping so the legacy default cannot silently absorb another managed key.
- Reserve `home/home:presence:owner_entities` completely from generic dashboard
  and MCP access at the same time its already-adopted dedicated owner route is
  implemented. Deterministic internal code continues to use the low-level state
  helper.
- Protect existing module flags, general settings, and Home thresholds from raw
  generic mutation while keeping their specialized surfaces. Keep their values
  visible only to the authenticated owner through the generic dashboard during
  compatibility rollout; omit them from MCP reads and lists.
- Treat `chronicler/chronicler/owntracks/ssid_places` as private to MCP, but keep
  its current centrally authenticated generic dashboard CRUD until a dedicated
  validated owner surface exists. This explicit transitional exception avoids
  stranding the only documented editor.
- Return fixed, content-blind denials before generic writes reach MCP or state
  storage. A denial never changes a row version, emits a value, or implies that
  the key exists.

## Scope and authority

This change proposes an OpenSpec contract and owner decision only. It changes
no canonical spec, runtime, migration, state value, credential, deployment, or
live process. It does not implement the adopted Home route or authorize access
to real owner configuration. The generic-state implementation and Home route
must share one implementation owner and atomic rollout if Option A is adopted.

## Exact owner choice after independent review

- **A - Adopt (recommended):** adopt the protected-state registry, the exact
  initial operation matrix, ordinary-key compatibility, and atomic Home route
  sequencing proposed here. Adoption permits a separately dispatched and
  reviewed repository implementation; it does not itself authorize code,
  merge, deployment, state reads/writes, private identifier submission, or live
  activation.
- **B - Accept the current risk:** explicitly accept that authenticated owner
  State-tab calls and each butler's generic MCP state tools can bypass
  specialized validation and CAS, and that model tool results may expose
  same-schema state values to the configured LLM provider. Route-local
  guarantees must continue to disclose that limitation.

If unanswered, neither option is adopted: retain the current runtime, keep the
P1 open, and do not represent silence as accepted risk.

Tests: +0 ~0 -0.
