# Replace finance CSV script contract

## Why
RFC 0038 D2/D3 removes executable skill companions from reasoning sessions.
The authorized bu-svqpzo.5 amendment replaces the obsolete CSV script contract
with a daemon capability workflow and explicit owner confirmation.

## What Changes
- Replace the skill workflow requirements with storage_ref, bounded previews,
  parsing overrides, owner confirmation, and capability-only error handling.
- Remove obsolete script generation, API preflight and URL discovery requirements.

## Impact
This is the normative CSV contract amendment only. The associated implementation
is reviewed with bu-svqpzo.5 and remains blocked on valid test-retirement admission;
archiving this specification does not certify implementation or merge readiness.
