# S1 boundary

This implements the accepted first two steps only. The standalone tracker deny
uses trackerEgress.enabled/deniedCidrs, selecting every pod without a bridge
exception. Existing dev confinement remains enabled; missing CIDRs refuse
rendering. Other environments keep their existing enablement posture.

Frontend route, nav, palette, chord, prefetch, page-context and badge wiring are
removed together. Shared QA/approvals and generic HTTP error handling stay.
Helm cannot re-enable the bridge with legacy beadsExport values. Compose export
plumbing and backend readers remain until their own removal slice.

Retirement-only tests are removed with their feature. Mixed chart tests preserve
fleet capacity, security and secret-store assertions; shared frontend tests retain
every non-retired route and authorization population. Backend data is unchanged.
