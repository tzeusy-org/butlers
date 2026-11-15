## Implementation

- [x] Add the six tools, the owner-private guard, sanitisation and inert URLs.
- [x] Add the receipts migration and the single post-write seam.
- [x] Declare the D4 catalog metadata and store writers.
- [x] Document the tools (docs/modules/google-drive.md).

## Verification

- [x] HTTP-seam tests with an `httpx.MockTransport` fake of Drive and Sheets.
- [x] Real-catalog completeness and authorization contract tests with the switch on.
- [ ] Real-API check with the dev Google account (see the PR body).
