# Original issue obligations

## Original description (verbatim)

Excluded from the chart: executor attests Docker cgroup ids, host boot_id, DOCKER-USER iptables via sudo wrapper. Needs a NetworkPolicy/attestation redesign for k8s; until then run it via compose --with-restore-drill only.

## Original design (verbatim)

DESIGN-FIRST bead: do not port code yet. Deliverable is an openspec change proposal / RFC addendum for a k8s restore-drill executor. Problem: the compose executor attests Docker cgroup ids, host boot_id and DOCKER-USER iptables via a sudo wrapper, none of which exist in a pod. Proposal must map each attestation to a k8s equivalent (Job pod UID/namespace, node boot_id via downward API or node object, NetworkPolicy for isolation instead of iptables) or state which guarantees weaken, with an explicit owner call on any weakening. Until accepted, restore drill stays compose-only (--with-restore-drill). Blockers: shaping done; implementation waits on the accepted proposal.

## Structured source-authoring acceptance (verbatim)

Structured acceptance for the original DESIGN-FIRST source proposal deliverable. These checks restate the original proposal outcome and its already-governing protected restore/security contracts. Proposal semantics remain UNADOPTED; no Kubernetes implementation or operational authority follows from authoring.

AC1. Deliver a DESIGN-FIRST OpenSpec proposal/RFC addendum, retaining the original description/design verbatim. Do not port code. Until exact acceptance, keep the executor chart-excluded and Compose-only via --with-restore-drill; preserve current protected launcher, firewall, relay and privilege/result mechanisms.
AC2. Map Docker cgroup/container generation, host boot ID and root-prepared DOCKER-USER plus host-route fence to concrete authenticated Job/Pod/namespace/node/generation and network-enforcement equivalents. Correct the proposed Downward-API boot-ID assumption. Name every unmapped or weakened guarantee, its risk and the precise explicit owner call required; mark alternatives NOT ADOPTED.
AC3. Carry the full protected outcome: isolated purpose-bound credential and result authority; separate uncredentialed relay; exact remote endpoint/port; hostname/TLS/CA identity; attestation before secret use; no listener/socket/ordinary runtime network; read-only backups; scratch-only bounded precleanup/create/restore/verify/postcleanup; cleanup failure is failure; single executor or proven cross-process guard; no stale-generation restart bypass; truthful missing/failed/passed/recovered and persistence-unavailable evidence; rollback preserves protected history and never mutates live data. No .14 ephemeral replacement or public-audit authority is silently adopted.
AC4. Include the complete failure/replay/concurrency/caller/rollback matrix listed in candidate design, with each row assigned a concrete authority, finite refusal/recovery transition and future executing seam. Generic NetworkPolicy or Pod UID alone is not claimed equivalent to host/pre-secret fencing.
AC5. Reconcile already-adopted isolated-executor and exact backup-recovery contracts and actual active ownership; retain pending unapproved siblings, BYPASSRLS/production facts, .14/bu-i4khaz, u03/r6wzr, secrets and privilege owners without re-asking recorded choices or taking their source surfaces.
AC6. Exact proposal strict validation and source-bound requirement/scenario coverage pass; the proposal contains an explicit adoption state and future real PostgreSQL/ACL plus authorized Kubernetes enforcement evidence plan. Tests: +0 ~0 -0. A source/spec pass closes proposal delivery only; it cannot claim implementation, deployment, privilege provisioning, restore proof or production recovery.


These are delivery checks, not adoption of the proposed Kubernetes contract.
