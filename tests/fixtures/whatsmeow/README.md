# External Whatsmeow schema prerequisite

`08-lid-mapping.sql` is the complete unchanged upstream file
`store/sqlstore/upgrades/08-lid-mapping.sql` at the exact Whatsmeow version
`v0.0.0-20260722203353-e9a033b24933` pinned in `whatsapp-bridge/go.mod` and
`go.sum`. The helper checks those pins and the file digest before use.

These fixtures execute upgrade8's actual primary-key, unique and nullability
constraints. They do not pretend the table belongs to a Butlers migration or
claim a complete live bridge/device-store upgrade. Dependency updates must
refresh this full upstream body and its provenance together.
