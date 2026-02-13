# Memory Role (Deprecated)

Status: Deprecated
Last updated: 2026-02-13
Primary owner: Platform/Core

The platform no longer defines memory as a dedicated role-level butler.

Memory is now a reusable module that relevant butlers load locally, with:
- memory tables stored in each hosting butler's own database,
- memory tools registered on each hosting butler MCP server,
- consolidation/decay/cleanup jobs executed by the hosting butler runtime.

Use `docs/modules/memory.md` as the authoritative memory contract.

This file is retained as a compatibility pointer for older references only.
