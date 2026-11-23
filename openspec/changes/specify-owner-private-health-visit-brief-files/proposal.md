# Specify owner-private Health visit brief files

## Why

The existing Health visit brief gives the owner a source-attributed summary before an appointment. A document could make that same record easier to consult. Saving a Google Doc, exporting a PDF to blob storage, and sending a Telegram attachment create additional copies of confidential Health content. The shipped Drive and Telegram tools provide the mechanics for those stages. The owner still has to decide whether to allow those copies and how long each may remain.

## What Changes

- Propose one file generation on explicit owner request, pinned to the existing Health brief's source snapshot. The daily plain-text brief keeps its current eligibility, content, retry, and delivery contract.
- Reuse Health's deterministic brief builder, its existing source attribution, Drive's owner-private guard and creation mark, the existing PDF export and blob store, and the governed owner notification boundary.
- Define the private target checks, duplicate request behavior, content-free receipts, and honest partial outcomes across Drive, blob storage, and Telegram.
- Keep implementation and activation behind explicit owner adoption of the exact proposal and the copy policy in [the design](design.md).

## Scope and evidence

The Dots release and the independently reviewed packet repair authorize this contract draft through `bu-jv1moa.35`, `bu-4ap4ap`, and `bu-s4dkj6`. The existing text brief landed in #4758, confidential digest classification in #5319, and owner-private PDF export in #5949. The original `bu-jv1moa.35` acceptance is preserved. Clinic sharing remains in `bu-jv1moa.36`, the owner command in `bu-jv1moa.34`, and refreshed briefs after long reschedules in `bu-jv1moa.25`.

This draft uses synthetic scenarios. Runtime code, credentials, and provider state stay untouched. Tests: +0 ~0 -0.
