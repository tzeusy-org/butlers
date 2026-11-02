## Why

Ingest stores a content-hash key and a `hash:` or payload-hash dedupe key beside the scrubbed
message text. They are computed from the raw text, so any reader of `switchboard.message_inbox`
or `public.ingestion_events` (which the dashboard exposes) can brute-force a withheld 4-8 digit
code against the visible template. bu-y0cll7 chose to scrub before hashing.

## What Changes

- Content-hash and dedupe keys derive from bearer-scrubbed text. Text without bearer material hashes
  byte-identically to before, so ordinary dedupe and old rows are unchanged.
- When bearer material was withheld (by ingest or already by the connector), the secondary cross-connector lookup is skipped and
  `content_hash_key` is omitted, so a second code with its own event id is not dropped.
- Scrub failure fails closed to a constant; the pipeline scrubs before building its key.
- Historical rows are out of scope (owner decision bu-x5taqy, backfill bu-tnm8zn).

## Impact

- `roster/switchboard/tools/ingestion/ingest.py`, `src/butlers/modules/pipeline.py`, three docs.
- Spec: `connector-base-spec` Deduplication Strategy.
