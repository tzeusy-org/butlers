# Memory, Retrieval, And Domain Surfaces

Estimated smart-human study time: 8 hours

## Why This Module Matters

Beyond core runtime and storage, the repo contains domain butlers, memory, retrieval, blob storage, frontend/API contracts, and provider integrations. This module teaches the shared concepts needed to understand those surfaces without requiring mastery of every provider API.

## Learning Goals

- Understand structured memory as facts, episodes, rules, provenance, and embeddings.
- Explain vector search and retrieval budgeting at a practical level.
- Understand S3-compatible blob references and attachment/import policies.
- Recognize frontend/API and roster-domain contract patterns.

## Subsection: Memory Facts, Provenance, And Vector Retrieval

### Why This Matters Here

The memory module is not a text dump. It stores structured facts with entity anchors, source sessions, provenance episodes, audit events, embeddings, and retrieval rules.

### Technical Deep Dive

Memory systems need more than storage. They need provenance: where did this fact come from, when was it observed, which actor/session produced it, and has it been superseded or retracted? Facts may attach to entities, have temporal validity, and require audit trails.

Embeddings map text into vectors so semantic search can find related items without exact keyword match. Vector search is approximate relevance, not truth. Good retrieval combines similarity, filters, recency, provenance, and context budgets so the runtime receives useful memory without flooding the prompt.

### Where It Appears In The Repo

- `docs/modules/memory.md`
- `src/butlers/modules/memory/`
- `src/butlers/modules/memory/storage.py`
- `src/butlers/modules/memory/search.py`
- `openspec/specs/module-memory/spec.md`
- `tests/modules/memory/`

### Sample Q&A

- Q: Why is provenance necessary for memory facts?
  A: Without provenance, the system cannot audit, retract, supersede, or explain where a fact came from.
- Q: Why is vector similarity not enough to decide what memory is true?
  A: Similarity finds related text; truth and relevance need metadata, provenance, filters, and domain rules.

### Progress

- [ ] Exposed: I can define fact, episode, provenance, retraction, embedding, vector search, and context budget.
- [ ] Working: I can explain why memory writes need source context.
- [ ] Contribution-ready: I can identify a direct SQL memory write that would lose provenance.

### Mastery Check

Target level: `contribution-ready`

You should be able to review a memory write or search change and identify provenance, retrieval, and audit implications.

## Subsection: Blob Storage, Attachments, And Imported Data

### Why This Matters Here

Attachments, transaction imports, and media-like artifacts use object storage semantics rather than storing all content inline in PostgreSQL.

### Technical Deep Dive

Object storage systems such as S3 store blobs by bucket and key. The database stores references and metadata, while bytes live in blob storage. This design supports larger files, lazy fetch, MIME/type checks, and independent lifecycle policy.

Blob operations need explicit not-found behavior, idempotent-ish writes, size limits, and content-type validation. A data import path may need both a structured DB record and a durable pointer to the original source file for audit or reprocessing.

### Where It Appears In The Repo

- `docs/data_and_storage/blob-storage.md`
- `docs/connectors/attachment-handling.md`
- `src/butlers/storage/blobs.py`
- `src/butlers/tools/attachments.py`
- `tests/core/test_blob_storage.py`
- `roster/finance/tests/test_import_transactions_blobstore.py`

### Sample Q&A

- Q: Why store blob refs instead of raw bytes in a transaction row?
  A: Large or binary payloads are better handled by object storage while the DB tracks metadata and references.
- Q: Why should not-found be explicit?
  A: Missing blobs can indicate deleted data, bad refs, or lifecycle policy effects and should not masquerade as empty content.

### Progress

- [ ] Exposed: I can define bucket, key, blob ref, MIME type, lazy fetch, and object metadata.
- [ ] Working: I can explain when to store bytes in object storage versus PostgreSQL.
- [ ] Working: I can identify validation needed for an attachment or import path.

### Mastery Check

Target level: `working`

You should be able to trace an attachment/import from source bytes to blob storage reference to structured database record.

## Subsection: Domain Modules, Frontend/API Contracts, And Specs

### Why This Matters Here

The repository includes roster-specific butlers, dashboard APIs, a frontend, and OpenSpec/doctrine material. Safe changes require respecting contracts beyond one Python function.

### Technical Deep Dive

Domain modules encode business capability: finance, relationship, health, calendar, memory, messenger, and others. Their tools usually depend on the same shared concepts already taught: module lifecycle, DB schema, credentials, side effects, tests, and observability.

Frontend/API contracts are server-state contracts. A dashboard view often assumes response shapes, polling cadence, mutation payloads, and freshness metadata. Changing an API is therefore a UI change too.

Specs and doctrine are governance artifacts. They explain intended behavior, scope boundaries, and contract tests. They are not runtime mechanics, but they help decide whether a technically possible change fits the project.

### Where It Appears In The Repo

- `roster/*/MANIFESTO.md`
- `roster/*/tools/`
- `docs/frontend/backend-api-contract.md`
- `docs/frontend/data-access-and-refresh.md`
- `frontend/src/api/client.ts`
- `about/`
- `openspec/`
- `tests/contracts/`

### Sample Q&A

- Q: Why check a butler manifesto before adding a domain feature?
  A: It states the butler's public identity and value proposition, which should guide scope.
- Q: Why is an API response shape a frontend concern?
  A: The frontend's cache, polling, rendering, and mutation code may depend on the shape.

### Progress

- [ ] Exposed: I can define domain module, roster, manifesto, API contract, server state, and OpenSpec.
- [ ] Working: I can identify the docs/tests that constrain a domain feature.
- [ ] Working: I can explain why a backend response change may require frontend updates.

### Mastery Check

Target level: `working`

You should be able to plan a small domain feature by checking its manifesto, module docs, API contract, storage path, and tests.

## Module Mastery Gate

- [ ] I can explain memory facts, provenance, and vector retrieval.
- [ ] I can trace blob-backed data from source to reference.
- [ ] I can identify frontend/API and domain-contract impacts.
- [ ] I can name which provider-specific details are safe to defer until relevant work.
