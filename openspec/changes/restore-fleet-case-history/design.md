# Design

## Authority and snapshot

Existing ordinary pg_dump keeps row_security=off and full fenced-object exclusions. Exactly cases/links join the three existing cost tables in the permissive PUBLIC SELECT proof. Unfenced evidence has its own source shape/owner/validated-FK proof and data-only exclusion. All five forced datasets, evidence and Confirm are exported under the same retained snapshot. No sampled output or live application connection supplies authority.

## Offline restore

Core288 leaves original runtime policies intact and adds owner INSERT policies to two named relations. The SECURITY DEFINER importer has fixed search_path and row_security, an explicit existing common table owner, no public/runtime execute grant and exactly two literal relation selectors. It inserts whole historical records, never UPDATE/DELETE or conflict suppression. Evidence follows its existing unfenced path. Restore checks membership only if history is present, grants the fixed temporary staging read to the source owner, and replays cases/evidence/links in order under ON_ERROR_STOP and one transaction. Failed inserts roll back the whole history stage.

## Certification and old artifacts

The generic loader deliberately remains tolerant for existing ownership recovery semantics. A separate Fleet audit independently checks supported dump framing, declared three-family population, both named case FKs (correct columns/parent, validated, immediate, cascading, four enabled constraint triggers), and actual orphan absence. It refuses old nonempty evidence-only dumps without inventing missing parents or deleting evidence. Authentic old zero-history artifacts remain a legacy compatibility path, not full Fleet recovery. Restore is not certification of omitted control-plane ledgers.

## Verification and limits

Existing assertions, parameters, snapshot barriers, cost/Confirm history and sentinel outcomes survive. Meaningful software controls omit the Fleet stage or proof reply and must fail the actual published artifact consumer before restoration. Native carriers exercise real scripts, runtime writers, complete row/security equality, policy drift, legacy/staged orphan and invalid FK refusal, owner-only execution, bootstrap and ordinary/privileged migration convergence. Native bodies require hosted PostgreSQL evidence and are not implied by source or collection. No private Capture/c6 source is imported; ROOT serializes their later immutable semantic union.
