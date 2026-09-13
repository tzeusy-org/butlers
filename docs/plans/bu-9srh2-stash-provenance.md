# Historical Butlers Stash Provenance Audit

**Evidence captured:** 2026-09-12T00:48:56+08:00

**Issue:** `bu-9srh2`

**Status:** Unresolved provenance; historical cleanup is not complete

**Reader:** The maintainer deciding whether any historical stash object may be preserved or
proposed for later removal

## Outcome

The historical checkout at `/home/tze/gt/butlers` is absent, and no surviving Git common
directory was identified for it within the bounded searches below. The current checkout's common
directory has no worktree, configuration, or object-alternate record that ties it to the historical
path. Consequently, no historical stash commit can be identified by immutable object ID, no
historical object can be classified as a retained unique change or verified committed equivalent,
and no removal is proposed.

The current Git common directory does contain stash objects. They are recorded below only as a
negative control: their presence does not prove that the historical repository was moved, copied,
or lost. Ordinal names such as `stash@{0}` are deliberately excluded because reflog positions move.

## Authority and safety boundary

This was a content-blind, read-only audit. It examined paths, Git repository relationships, object
IDs, parent topology, tree IDs, and digests produced without displaying file names or patch bodies.
It did not apply, pop, drop, clear, create, move, or otherwise mutate any stash, `refs/stash`, stash
reflog, or existing Git object. It did not inspect patch contents or infer abandonment from
timestamps. The sole tracked write is this report on the isolated worker branch; no source,
runtime, or fleet-policy file changed. The proposal grants no authority to take a destructive or
outward-publication action.

## Bounded evidence

| Probe | Result | What it establishes |
|---|---|---|
| Exact historical path | `/home/tze/gt/butlers` was absent | The expected checkout cannot currently resolve its own Git directory. |
| Historical source-root scan | Zero directories named exactly `butlers` within five levels of `/home/tze/gt` | No renamed child at the expected source root was found within the stated bound. |
| Exact local trash targets | Zero matches at the two conventional per-user trash locations checked | The checkout was not found at those exact recovery locations; this is not a backup-system search. |
| Current common-directory worktree registry | Zero `gitdir` or `commondir` records containing the exact historical path | The current repository does not register the historical checkout as one of its linked worktrees. |
| Current object-store alternates | No `objects/info/alternates` file | The current object store has no configured alternate from which historical objects are being borrowed. |
| Current worktree provenance configuration | No local `core.worktree` or `extensions.worktreeConfig` value | Local Git configuration supplies no old-to-current path mapping. |
| Known checkout roots | The only exact `butlers/.git` found under `/home/tze/gt` and `/home/tze/GitHub` within the bounded scan was `/home/tze/GitHub/butlers/.git` | A current repository exists, but the scan does not prove it is the historical repository. |
| Repository text references | Tracked documents contain old `/home/tze/gt/butlers` paths | These establish prior use of the path, not Git common-directory or stash-object continuity. |

The bounded search intentionally did not read shell history, terminal transcripts, command
arguments, patch bodies, or unrelated user files. It also did not query an unspecified backup,
snapshot, or remote archive service. Such sources could disclose sensitive content and, absent a
named recovery source, would not provide a controlled repository-identity chain.

## Current common directory: negative control

The current checkout resolves to `/home/tze/GitHub/butlers/.git`, uses SHA-1 Git object IDs, and had
the following content-blind state at capture time:

| Evidence | Immutable value |
|---|---|
| Hashed `origin` identity | SHA-256 `6acfb3b15f14c358b66083c1d8709afee7d33772c74611ece63c2a2ad54ef67f` |
| `refs/stash` tip and sole reflog object | `e1a0078473a9d08acb5defd404bcbdc7a44ee0d7` |
| Stash tree | `29be9f2da37c3c7abb2e04061cc80a04943a812b` |
| First parent, the base commit | `0308189f89cae8aa9c512426a8e42b1ebf50159e` |
| Second parent, the index snapshot | `1c686ed70f488200156cc78ad073eb429902f7ba` |
| Third parent, the untracked snapshot | `ccadf9fa48dc0b2d088edd95665f4b7c4f92b556` |
| Stable tracked worktree-delta patch ID | `3e689ab52af9f68f908e9e88ab831b351331f79c` |

The tip and all three parent objects exist, and a connectivity-only object-store check passed. The
three-parent topology is consistent with a stash that recorded untracked material. It does not
identify the object as historical.

A topology-only scan of commits unreachable from current refs when reflogs were excluded found 49
stash-shaped candidates: 43 with two parents and 6 with three parents. The SHA-256 digest of the
sorted `<object-id> <parent-count>` set was
`78b539b7820f48babcd2796130073eccd14696694d2a1d7db6f801d0456a73d9`. The scan required the second
parent to have the candidate's first parent as its sole parent and, for three-parent candidates,
the third parent to be a root commit. This is a structural heuristic, not repository provenance;
ordinary object retention and concurrent repository activity can also change this set.

Neither the reachable stash object nor any of the 49 topology candidates is a substitute for an
object recovered from the historical common directory. The current origin fingerprint is a useful
future comparison anchor, but a matching remote alone would prove only that two clones name the
same remote, not that their private stash objects share custody.

## Classification

| Object or set | Classification | Comparison basis |
|---|---|---|
| Historical stash objects | **Unresolved provenance** | No historical common directory, reflog, or immutable historical stash object ID is available. There are therefore zero identifiable historical objects to compare. |
| Current `refs/stash` object `e1a0078473a9d08acb5defd404bcbdc7a44ee0d7` | **Outside the historical classification** | Its object and parents are present in the current common directory, but no evidence links that directory or object to the historical checkout. |
| 49 unreachable stash-topology candidates in the current object store | **Unresolved candidates, not identified historical objects** | Parent shape is compatible with a stash but provides neither historical custody nor committed equivalence. |

No object is classified as a verified committed equivalent. Making that classification without a
historical stash ID, its base and snapshot trees, and an exact comparison against a reachable
commit would create a false basis for deletion. Age is not part of any classification.

## Exact evidence required to resume

Supply at least one of these provenance sources before repeating the audit:

1. A read-only filesystem snapshot or archive containing the historical checkout's `.git` file or
   Git common directory, including its object store and `logs/refs/stash` if retained.
2. A contemporaneous inventory of the historical stash commit IDs, paired with a repository
   identity anchor such as the recorded common-directory location and a hashed remote identity.
3. A filesystem move or backup-restore record that maps the historical Git common directory to a
   surviving directory, plus at least one object ID recorded before the move that is present after
   it.

A path with the same basename, a matching remote, a stash subject, a reflog ordinal, or a
stash-shaped commit topology is insufficient on its own. If only object IDs are recovered, each ID
must resolve as a commit in a provenance-qualified object store before its parents or contents are
compared.

## Content-blind comparison method after recovery

For every recovered historical stash commit, record the immutable stash commit ID, tree ID, and
ordered parents. Treat parent one as the base, parent two as the index snapshot, and an optional
third parent as the untracked snapshot only after validating the standard parent topology.

Generate comparison candidates with stable patch IDs, but do not use patch-ID equality as disposal
proof because stable patch IDs intentionally ignore some formatting differences. Verify a committed
equivalent with exact, non-displaying comparisons:

- compare tree IDs when a candidate commit claims to reproduce the complete snapshot;
- hash full-index, binary Git diffs for base-to-index, index-to-worktree, and base-to-worktree, and
  require matching hashes at the corresponding candidate transition;
- compare modes and blob object IDs for every tracked path involved; and
- when a third parent exists, require every untracked tree entry's mode and blob ID to exist at the
  same path in the claimed committed result.

All path and patch streams should flow directly into digests or quiet comparisons rather than a
terminal or report. A mismatch, missing layer, bundled commit that prevents an exact comparison, or
incomplete candidate search remains **unresolved provenance**. Only an exact match across every
present layer supports **verified committed equivalent**. A non-equivalent object remains
**retained unique changes** until an explicitly authorized preservation copy has been created and
verified.

## Preservation and disposition proposal

1. Keep the historical cleanup open and make no stash or ref mutation.
2. Do not run pruning or garbage collection as part of this task. If a provenance-qualified common
   directory is recovered, preserve it read-only before inspecting individual objects.
3. After the required evidence is supplied, inventory the historical stack by immutable object ID
   and apply the exact comparison method above to each object independently.
4. Propose **retention** for every unique or unresolved object. For a unique object, a future,
   separately authorized preservation action should make it reachable in a durable repository or
   object archive and verify that recovery copy before any cleanup is considered.
5. An object classified as a verified committed equivalent may become eligible for a later removal
   decision, but this report does not authorize that decision or action.

**Resume condition:** a provenance-qualified historical Git common directory or immutable
historical stash ID inventory becomes available. Until then, the only safe disposition is retention
and the historical cleanup remains unresolved.
