## Boundary and proposed execution

One scheduled CronJob is installed per backup catalog after separate approval.
A Job starts an ordinary restricted Pod with a PostgreSQL sidecar. The database
listens only on a Unix socket shared with the verifier. Its data resides in
per-Pod ephemeral storage. The admission boundary binds the Job to a catalog
before launching it. Only the verifier mounts that catalog's backup PVC,
read-only. The verifier selects one published artifact by stable catalog
identity, checks its bytes and manifest binding, and binds the verified digest
to the admitted attempt before restoring. It restores to the Pod-local scratch
database, runs the required scoped checks and explicitly drops scratch.
The verifier then submits the bounded evidence to the protected writer. Scratch
storage disappears when the Pod is removed. The immutable row records SQL
scratch cleanup. A separate control-plane receipt proves workload and volume
retirement before the next admission.

The workload receives neither a live PostgreSQL credential nor permissions to
alter Kubernetes admission, networking, deployment or secrets. The evidence
writer is a separately scoped trust boundary. The writer authenticates an
approved Job generation, checks its pre-issued attempt identity and scope,
validates the bounded verdict and atomically persists one row. Its interface
permits result submission only and exposes no arbitrary live SQL operation.
The deployment review must choose and verify the workload identity and writer
transport before either is enabled. Its use of authenticated transport, denied
alternative routes, bounded retries and no ambient credential inheritance are
required acceptance checks.

The writer binds the authoritative record to the exact artifact digest, verified
byte size and completion time, manifest digest where applicable, scoped checks,
policy digest, cleanup, and fixed result. Existing result projections continue
to read authoritative rows and expose content-blind evidence. Published proofs
from the old ledger retain their original provenance. This proposal changes the
authoritative writer, while preserving every required proof. The separately
unapproved filtered-event manifest supplies no inherited authority.

Use `concurrencyPolicy: Forbid` as a first guard for a single CronJob. The
controller may create duplicate jobs, and it applies the policy only to Jobs
from that CronJob. The protected admission boundary must serialize scheduled,
manual, retried and cross-release attempts against the same backup catalog.
It must prove the prior generation stopped before granting another attempt.
A per-attempt retry after termination receives a new identity. See
[Kubernetes CronJob job creation and concurrency](https://kubernetes.io/docs/concepts/workloads/controllers/cron-jobs/#job-creation)
and [ephemeral volume lifecycle](https://kubernetes.io/docs/concepts/storage/volumes/#emptydir).

## Alternatives

The comparison uses #4500 at `9abdad8909f70856585a167b69e91d708140008d`.
Its preserved draft replacement #4862 is closed without merge. The dispositions
below are alternatives for the recorded choice on `bu-viat6h.4`.

| Candidate | Scratch and proof | Authority and consequence |
| --- | --- | --- |
| Keep Compose | Named scratch on the configured PostgreSQL cluster, existing executor-owner ledger. | Retains the existing dedicated role, root-prepared generation fence, credentialed process and uncredentialed relay. The Helm chart stays outside the restore path. |
| Node-fenced Kubernetes | Same live-cluster scratch and existing result authority. | Preserves the intent of the Compose pre-secret and network fence by introducing a separately reviewed node-level protocol. This requires new node authority and implementation evidence. |
| Plain Kubernetes from #4500 | Same live-cluster scratch and result authority. | Uses ordinary Kubernetes policy and declares weakenings to pre-secret, generation and host-route guarantees. Those weakenings require individual owner acceptance. |
| Throwaway PostgreSQL CronJob | Pod-local scratch, writer-attested evidence row. | Removes live `CREATEDB`, the live restore credential and its dedicated relay. Foregoes proving execution against the live cluster's endpoint and topology. Promotion-ready ownership/ACL proof requires a representative bootstrap and positive scoped checks in the sidecar. This changes result authority and requires separate adoption of that boundary. |

The CronJob cannot attest that the live cluster recovered or that production
network, roles, credentials or data match the scratch environment. Missing or
incompatible bootstrap yields scoped or unproven evidence. The existing
production proof gate remains independent.

## Failure, replay, concurrency and rollback

| Event | Refusal or recovery | Future evidence |
| --- | --- | --- |
| Admission identity missing, stale, replayed or for another catalog | Refuse launch. A stale generation cannot create a result. | Admission and writer reject wrong generation, expired authority and changed store/catalog. |
| PVC cannot mount read-only or candidate bytes change after selection | Abort without a recovery pass. Bind the selected digest before restore and verify it against the bytes used. | Exercise a writable mount rejection, artifact substitution and mismatched manifest. |
| Database container exits, restore fails, or a scope is unavailable | Stop verification. Report only reached and completed stages. An unverified scope stays unproven. | Real PostgreSQL fixture exercises bootstrap, restore, ownership/ACL, coverage and scoped failure. |
| Scratch cleanup fails or Pod/storage retirement is uncertain | SQL cleanup failure overrides a pass. The control plane refuses a new generation until it independently observes retirement. | Fault-inject SQL drop and Pod cleanup separately, verify stored verdict and exclusion. |
| Writer unavailable or the transaction aborts | Return persistence unavailable, with no proof advance. Retry the same submission only within the same identity and content. | Disconnect writer before commit and confirm no authoritative row. |
| Writer commits but reply is lost | Return the existing receipt to the same authenticated attempt for the same payload. After retirement, only the protected control plane can look up that exact receipt, with no write authority. | Simulate lost acknowledgement before and after retirement, duplicate delivery and changed content. |
| Duplicate scheduled, manual, rollover or retried Job | Deny a second admission while prior generation is active or retirement is uncertain. | Drive competing triggers across Job and release boundaries, including forced cancellation. |
| Retry after a failed terminal attempt | Use a fresh attempt identity and verify the artifact from scratch. Keep prior terminal rows. | Retry same artifact and verify no borrowed scope or duplicate row. |
| Rollback is ordered | Suspend new admission, await confirmed quiescence, revoke workload/writer binding and preserve both histories. Restore the old role only via separate managed-bootstrap authorization if reverting to Compose. Never discard or relabel recorded proof. | Exercise cutover failure and reverse transition with saved data and test roles in an authorized environment. |

## Delivery gates

First land and cite the adopted baseline referenced by `bu-h40h2b.3`. The third
delta cannot be applied against main before that baseline exists. Use a reviewed
semantic update after a choice, including all requirement owners, so this
proposal does not overwrite intervening restore, coverage, or identity changes.

An implementation would require independent scope and security review, then
spec-to-chart rendering for PostgreSQL and the read-only PVC. A real PostgreSQL
role catalog check must show no live `CREATEDB` apart from the bootstrap
superuser. Source inventory must show legacy `restore_drill_executor` runtime
code absent from `src`, `scripts` and `alembic` at the end of the separately
reviewed retirement, with immutable migration history treated under the
repository's migration policy. The owner must authorize any enforcement or
operational proof separately.
