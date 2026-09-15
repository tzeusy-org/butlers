## 1. Backend catalog contract

- [x] 1.1 Remove `supports_backfill` from the static available-connector
      catalog and its Pydantic profile model.
- [x] 1.2 Make the available-connector API regression test assert the exact
      four-field response shape and retain representative catalog identities.

## 2. Frontend contract alignment

- [x] 2.1 Remove `supports_backfill` from the `ConnectorProfile` TypeScript
      interface.
- [x] 2.2 Remove the obsolete field from the two typed roster fixtures.

## 3. Verification

- [x] 3.1 Run focused backend and frontend tests, frontend type/build and
      lint checks, and strict OpenSpec validation.
- [x] 3.2 Review the final diff to confirm no internal backfill protocol,
      heartbeat capability, or archived provenance changed.
