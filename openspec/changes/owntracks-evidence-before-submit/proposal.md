# OwnTracks location evidence is committed before the submit

## Why

The connector submitted to Switchboard, saved the checkpoint and only then wrote
`owntracks_points`, swallowing write errors. Fixes survived only as inbox text.
Owner decision run24-inbox-not-archive (2026-11-22) makes the inbox a routing buffer.

## What Changes

- Commit the evidence row first; a write failure acknowledges nothing.

## Capabilities

### Modified Capabilities

- `connector-owntracks`: Durable Location Evidence Fidelity (write ordering and failure).

## Impact

A connectors database outage now answers 503 to the phone, which keeps the point.
