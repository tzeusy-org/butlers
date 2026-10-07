# Supersede Gmail thread identity

## Why

Gmail now participates in the public split between stable conversation identity
and reply targeting, so the legacy field-mapping requirement needs explicit
supersession provenance.

## What Changes

- Replace the legacy Gmail ingest field-mapping requirement with its stable-identity successor.

## Impact

- Affected spec: `connector-gmail`
- Affected code: Gmail ingest envelope construction
