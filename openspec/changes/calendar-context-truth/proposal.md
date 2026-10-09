## Why
The General calendar producer can assert a meeting for declined or transparent events, and Google status events lose their declared type before reaching context and day-load analysis.

## What Changes
- Preserve bounded read-side event type and declared working-location fields through provider normalization and projection.
- Select owner-eligible context atomically, with typed out-of-office precedence, structural focus, and independent day-bounded working location.
- Exclude status-only events from meeting preparation and starting-soon notifications, and from day-load hours while preserving opaque conflict visibility.
- Extend existing controls and update RFC 0009, capability contracts and owning docs.

## Capabilities
### New Capabilities
None.

### Modified Capabilities
- `module-calendar`
- `context-bus`
- `connector-google-calendar`
- `calendar-conflict-overcommitment-radar`
- `calendar-overlay-aggregation`
- `proactive-insight-engine`

## Impact
Core 262 adds projection columns without changing grants or provider write APIs. General remains the sole calendar context producer. Broker suppression, workspace response models, and unrelated writers retain their contracts. Recorded provider fixture provenance and real PostgreSQL/hosted evidence remain separately required; synthetic fixtures cannot satisfy them.
