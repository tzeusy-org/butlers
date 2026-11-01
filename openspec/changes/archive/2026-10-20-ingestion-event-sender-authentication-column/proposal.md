## Why

Every accepted ingest now also records a sender-authentication verdict (bu-s11n0s.20), so the row
created for an accepted ingest gains one column, `sender_authentication`. The Ingestion Event Table
requirement lists the row's fields and did not name it.

## What Changes

- Ingestion Event Table: the "Row created on new ingest accept" field list names
  `sender_authentication`. The verdict semantics stay in the Sender Authentication Verdict
  requirement.

## Impact

- Spec: `ingestion-event-registry` (MODIFIED "Ingestion Event Table", one field name added).
- Code: `alembic/versions/core/core_271_ingestion_events_sender_authentication.py` adds the column.
- Retained cases: the hand-built `public.ingestion_events` fixtures in
  `roster/switchboard/tests/test_ingest_api.py` and `test_ingest_tier.py` gain the nullable
  column so the ingest insert can run; no assertion or test body changes. They move under
  contract evolution.
