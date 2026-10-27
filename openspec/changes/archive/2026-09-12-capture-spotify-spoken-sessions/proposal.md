## Why

The Spotify connector deliberately ignores `episode` playback, leaving a
time-bearing portion of the owner's listening history uncaptured. Capture
minimal, durable spoken-playback evidence now so a future approved projection
can use it without treating podcasts or audiobooks as music tracks.

## What Changes

- Request Spotify episode playback in the current-playback poll while
  preserving the existing track polling and music session state machine.
- Normalize podcast episodes, audiobook chapters, and unknown episode parents
  into a distinct passive spoken-session envelope that uses the existing
  connector policy and replay submission path, plus a narrow pre-resolved
  `metadata_only` policy rule for its stable event-id prefix.
- Store idempotent, connector-owned spoken-session evidence with bounded
  metadata and explicit read/write ACLs; do not retain transcripts or raw
  Spotify API payloads.
- Declare the source's future Chronicler compatibility and explicitly defer any
  adapter, projection, routing target, dashboard, Education, OAuth-scope, or
  LLM work.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `connector-spotify`: current-playback acquisition and capture-only spoken
  session evidence for episode items.
- `chronicler-source-compatibility`: a deferred, deterministic compatibility
  declaration for Spotify spoken-session evidence.

## Impact

Affected systems are the Spotify connector's current-playback request and
boundary tracking, guarded core and Switchboard policy migrations, focused
connector/migration tests, and the two OpenSpec capability contracts. The
change adds no credential, OAuth scope, API, dashboard, routing target, module,
or projection dependency.
