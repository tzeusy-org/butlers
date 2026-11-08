## 1. DND coverage (this change)

- [x] 1.1 Install the fixed export/restore definers and the two inert roles from the DND finalizer.
- [x] 1.2 Stage the DND populations in `pg_dump.sh` in the dump's snapshot; log the omission until activation.
- [x] 1.3 Round-trip, dump-identity-unchanged and owner-fence tests on a migrated database.
- [x] 1.4 Document the mechanism, activation step and 14-day retention consequence.

## 2. Perception coverage (gated on `bu-dhloi1.9`)

- [ ] 2.1 The change that creates the ten perception relations adds a fixed exporter/restorer for them and flips every `private_history_manifest.json` entry to `exported`; the manifest guard test fails otherwise.
