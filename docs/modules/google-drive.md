# Google Drive mutation approval

## Implementation Notes

Write, create-folder and move calls read the concrete destination folder and
its complete paginated effective permissions before any mutation. Every user
permission must resolve uniquely to the owner through the existing email
identity resolver to remain automatic. Groups, domain/public access and
unresolved users require owner approval. Missing, malformed or incomplete ACL
reads refuse without effect, including during approved replay.

Parked calls bind the concrete parent and a server-computed
`destination_audience_digest`, covering permission IDs, roles, inherited access
and visibility. This optional argument is a snapshot selector, never authority:
knowing or supplying it does not skip ordinary classification or approval.
The executor must replay the exact stored call; changed destination or audience
refuses before a write. Permission ordering does not change the fingerprint.

Private default-folder bootstrap stays automatic after verification. If a
missing default path component would be created under a shared parent, the
module parks the existing `drive_create_folder` call for that exact parent/name
and returns `bootstrap_approval_required`, `operation_complete=false`. Approval
creates only that folder. The caller then retries its original operation;
content is checked and approved separately. Ambiguous default paths refuse.

The ACL read and provider mutation are separate requests; this is not an atomic
external ACL compare-and-swap. A concurrent permission change after the final
read remains a provider race, not a claimed transactional guarantee.

Provider contracts: [permissions list](https://developers.google.com/workspace/drive/api/reference/rest/v3/permissions/list)
and [inherited sharing](https://developers.google.com/workspace/drive/api/guides/manage-sharing).
