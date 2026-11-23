# Owner-private native Google Docs and Sheets tools for Drive

## Why

Butlers' Drive upload never produces a native Google Doc or Sheet. The owner asked (2026-10-15,
epic bu-kqme7f) to close the "Drive saved as native Docs and Sheets" gap on the RFC 0038 path.
Converting through the Drive API adds no module, no local renderer and no dependency, so it is
inside the 2026-10-04 retirement decision (bu-lsxqb0.5): it extends the in-scope Google Drive
module along its specified purpose.

## What Changes

- Six tools, registered only when the RFC 0038 domain switch is on: `drive_create_doc`,
  `drive_update_doc`, `drive_create_sheet`, `drive_read_sheet`, `drive_append_sheet_rows`,
  `drive_update_sheet_range`. With the switch off the tool set stays at today's seven.
- An owner-private target guard before every write; a shared target is refused with
  `target_shared` and no write request is sent.
- Sanitisation (no image fetch, literal HTML, raw sheet values) and inert URLs under exposed or
  missing lineage.
- `google_drive_effect_receipts` (additive) for RFC 0038 D12 replay.
- One post-write seam for the sheet-watch sibling (bu-kqme7f.2).

## Capabilities

### Modified Capabilities

- `module-google-drive`: ADDED requirements for the six tools and the trust rules; MODIFIED
  "Tool Registration", "Tool Metadata for Approval Sensitivity" and "Database Schema Migration".

## Impact

`src/butlers/modules/google_drive/`, the central tool presentation inventory, three contract
tests, docs. No change to the existing seven tools, the Drive connector, the OAuth scope registry
or any roster `butler.toml`. Prerequisite: the Google Sheets API enabled on the OAuth client's
Cloud project.

## Reconciling rule with the existing Drive tools

The new tools are effect class `record` only because the domain refuses any target that carries a
non-owner permission. `drive_write_file` and `drive_create_folder` stay `communicate` because they
can write into folders the owner has shared. Loosening the refusal reclassifies these tools.
