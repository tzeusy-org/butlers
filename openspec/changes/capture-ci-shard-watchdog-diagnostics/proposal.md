## Why

A cancelled watchdog job can lose its only terminal execution observation. Bounded live diagnostic checkpoints preserve actual masked observations while leaving missing execution boundaries UNKNOWN.

## What Changes

- Add a separately named manual workflow with a frozen original unit-1 subject and separate capture source.
- Add a locked Node20 artifact action and a distinct pytest observer/strict diagnostic reader.
- Preserve the original runner, collector, partition, deadlines, finalizers and success-only consumers.

## Capabilities

### New Capabilities

### Modified Capabilities

- `testing`: add REQ-testing-060 for diagnostic-only live observations and their privacy, identity, cleanup and publication boundaries.

## Impact

New isolated workflow/action, Python observer, software controls and operating guide. The public artifact SDK is pinned at 2.3.2 and bundled; CI installs no npm dependencies. Required main CI is unchanged. Hosted retention, original-profile investigation and delivery remain separate ROOT-owned proof.

## Adopted allocation and bounded scope

ROOT allocation: complete immutable current-main and all136localheads/58nativeTrees/3059fullnativeBlobs plus whole8296canonicalrecords; no prior060claim. This is an approved additive draft for supported authoring, not a baseline edit or runtime qualification.

Scope: v1-mandatory for explicitly released named diagnostic investigations; no standing required-main-CI or provider-authority expansion.
