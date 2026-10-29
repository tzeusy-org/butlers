# Engineering tracker product bridge retirement

Owner decision bu-g2zuv2 = A (2026-10-21), implemented by bu-99t5s3.6,
removes engineering tracker tooling from the personal-assistant product.

The Helm exporter/applier, dashboard pages, API operations, backend snapshot
reader, decision jobs and schedules, Telegram decision transport, deployment
export materialization and Compose export mounts are retired in source.
The former bridge and projection contracts are withdrawn in RFC 0025 and
RFC 0007 Amendment 2; historical designs remain in Git history.

Engineering choices remain in local `bd` or an owner session. The Dolt workflow,
`make lint-decision-beads` and the engineering decision-bead convention remain.
No optional engineering digest replaces the retired product feature.

Runtime tracker connectivity and credentials remain prohibited. The effective
all-pod tracker deny in the Helm chart is retained, including its existing
fail-closed CIDR and environment posture. Source changes do not attest a live
network probe or authorize production operations.

## Deployment and retained storage

Source retirement is not proof that old processes have stopped. The existing
intent and prompt tables and their migration ancestry remain intact. Deploy
code removal and stop every old reader and writer before a separate reviewed
drop migration. Owner approval explicitly accepts eventual irreversible intent
history loss, but does not waive that ordering. This source slice contains no
DDL, migration revision or live deployment. Rolling source back before the
later drop is possible; data recovery after that drop is not promised.

## Implementation Notes

Approval callbacks retain their independent token prefix, HMAC, verified-owner,
credential, recipient and API route checks. Stale decision buttons have no
mutation authority and receive only the connector's generic acknowledgement.
Audit events for unmatched API routes retain request/effect metadata while
omitting raw request bodies, including malformed, scalar and truncated JSON.
Shared approval, notification, privacy, QA and deployment behavior remains.
