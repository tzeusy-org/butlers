# Google Drive

The module reads, writes and organises files in the owner's Drive under `butlers/{butler_name}/`.
It registers seven tools today and, after the RFC 0038 switch, seven native tools: six Docs and Sheets tools and a PDF export.

## Mutation approval

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

## Native Docs and Sheets (dark until the RFC 0038 switch)

Registered only when the domain verification switch is on (`goal_lineage.enforcement_enabled()`);
with it off the tool set is the seven above. In the generalist world the lead reaches them through
the capability catalog from whichever domain loads this module (Home and Health today).

| Tool | Effect | Replay |
| --- | --- | --- |
| `drive_create_doc(title, content, content_format, folder_id)` | record | `effect_id` receipt |
| `drive_update_doc(file_id, content, content_format)` | record | by content |
| `drive_create_sheet(title, rows, folder_id)` | record | `effect_id` receipt |
| `drive_read_sheet(file_id, a1_range)` | read | none |
| `drive_append_sheet_rows(file_id, rows, a1_range)` | record | `effect_id` receipt |
| `drive_update_sheet_range(file_id, a1_range, rows)` | record | by content |
| `drive_export_file(file_id, format)` | record | `effect_id` receipt |

### The owner-private rule

Before every write the module reads the target (`ownedByMe`, `driveId`, `permissions`): the parent
folder for a create, the file for an update or append. It writes only when the connected account
owns the target, it is not in a Shared Drive, and every live permission is that account's own user
grant. Otherwise the tool returns `target_shared` with counts by permission type and role (no
address) and sends no write request. There is no share tool.

This is why these tools are effect class `record` while `drive_write_file` and
`drive_create_folder` stay `communicate`: those can write into folders the owner has shared. A
change that loosens this refusal must reclassify the new tools.

### Sanitisation

- Markdown images (inline and reference) become ordinary links before upload, so Google's import
  never fetches a URL; every `<` is escaped so HTML stays literal.
- Sheet values are always written with `valueInputOption=RAW`, so `=`, `+`, `-` and `@` never
  start a formula (no `IMPORTXML`, `IMAGE` or `HYPERLINK` exfiltration).
- Under exposed or missing goal lineage every URL in doc content and string cells is written in
  the inert text form (`https[:]//example[.]test`); unexposed calls keep links. Bare hosts with no
  scheme or `www.` are not recognised.
- `drive_read_sheet` output is untrusted: collaborators, forms and import functions write cells.

### Scope of destructive writes

New files carry `appProperties {"butlers_created_by": "<butler_name>"}` (private to the OAuth
client). `drive_update_doc` replaces only Docs with this butler's mark (`not_created_by_butlers`
otherwise). Sheet writes may target any owner-private sheet; every native file keeps Drive revision
history and each write returns the pre-write `version`.

### PDF export

`drive_export_file(file_id, format="pdf")` exports a Doc or Sheet **this butler created** (same
`butlers_created_by` mark as `drive_update_doc`) and that is owner-private, so it never reads an
arbitrary Drive file into a blob. The PDF is stored through the butler's blob store and the tool
returns `storage_ref`, `filename`, `media_type`, `size_bytes` and `sha256`. An export over 10 MB
(or one Drive refuses as too large) returns `too_large` and stores nothing; a replayed `effect_id`
returns the first `storage_ref`. The tool does not send anything: to deliver, call
`notify(attachments=[{storage_ref, filename, media_type, sha256, size_bytes}])`, which sends it to
the owner on Telegram as a document. Only PDF today; XLSX, DOCX and CSV are a later slice.

### Limits and prerequisites

Docs are capped at 1 MB and a call at 50,000 cells (`too_large`, no Google call). The Google Sheets
API must be enabled on the OAuth client's Cloud project; if it is not, sheet tools return an error
naming it and do not retry. The already-granted `drive` scope covers the Sheets API.

### Sibling seam

`set_post_write_hook(hook)` is the module's single post-write seam, a no-op until a hook is set.
The sheet-watch bead uses it to record a self-write snapshot.
