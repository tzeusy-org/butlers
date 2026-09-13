## Context

The archived `connector-spotify` change is historical provenance. It named
`Spotify OAuth 2.0 PKCE Authorization Flow` and `Connection Status Card` in
its `dashboard-spotify-setup` delta. PR #3738, commit
`3239b332f38831da342ecf215ad3dea52809fdec`, early-synced the stricter
successors into the canonical spec: `Connector-Owned Spotify OAuth 2.0 PKCE
Authorization Flow` and `Content-Blind Connector Status Drawer`.

The successor bodies materially strengthen authority and privacy: Spotify
authorization and token lifecycle ownership remain with the connector, and
the Passport drawer exposes only a fixed content-blind projection. Those live
bodies are already the desired authority and must remain byte-identical.

## Decision

Use one `RENAMED`-only delta containing exactly the two predecessor-to-
successor mappings. OpenSpec 1.9.0 recognizes a rename whose source is absent
and target is already present as an already-synced no-op during normal archive.
That records the provenance without rewriting or duplicating either canonical
successor requirement.

Do not restore the archived bodies. Their settings-page, profile-content,
raw-error, and Tier 1 credential-storage clauses describe obsolete behavior
and would contradict the connector-owned OAuth authority and content-blind
status projection approved by PR #3738.

The ratchet cleanup is deliberately limited to the two named predecessor
keys. No baseline regeneration, overwrite-baseline edit, runtime action,
credential/data access, API/frontend change, or new test is part of this
maintenance.
