# Gmail token-origin health classification

## Why

Gmail currently recognizes `invalid_grant` and `unauthorized_client` on any HTTP response as actionable reauthorization. A Gmail data GET carrying either literal must remain a visible degraded source failure. Existing original14 P3 governs the exact token-origin boundary; its behavior-preserving refactor does not authorize this separate bug implicitly.

## What Changes

- Require the actual response-bound POST to exactly `https://oauth2.googleapis.com/token` before using the installed Google credential error-code predicate.
- Preserve Gmail's diagnostic formatter, fallback, six caller functions, exception propagation, metrics, cache/cursor state, healthy reset and account isolation.
- Prepare the complete MODIFIED Aggregated Health Status requirement, preserving its old paragraph and Health model scenario and adding the three independently reviewed scenarios.

## Impact

Only `src/butlers/connectors/gmail.py`'s Boolean source-failure classification changes. No credential/account writer, retry policy, provider operation, database schema, shared-kit import, hook or required guard changes. Source delivery, genuine native preparation review and separately released supported administration remain required. Original14's seven outcomes, five folds and hard metrics remain open.
