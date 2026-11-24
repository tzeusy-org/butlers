## Implementation

- [x] Move the evidence write ahead of the submit and let its failure propagate.

## Verification

- [x] `tests/connectors/test_owntracks_ingest_failure.py`; trace lines and `openspec validate --strict`.
