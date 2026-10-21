# Design

`ifb1:<uuid>:<u|n|x>:<hmac16>` signs its prefix, candidate id, verdict and server-created candidate timestamp using the RFC 0021 signer and existing Tier-1 callback secret. Tokens fit Telegram's 64-byte limit. The production job preserves broker feedback bindings in a typed notify.v1 envelope; Messenger resolves the existing callback secret and mints the keyboard only for Telegram insight deliveries. Other deliveries retain their existing path.

The connector resolves both sender and message chat to the primary owner channel before loading the content-blind callback state. It verifies the token, then sends the original token, verdict and bounded Telegram query id to the exact telegram-feedback route with its scoped connector credential. The API repeats HMAC verification against server state, requires the fixed owner@telegram provenance header and supplies that actor itself. No body actor or snooze is admitted. Repeated verdict detection occurs inside record_insight_feedback's candidate row lock; different verdicts remain reversible.

A tap edits the original reply markup, marking the chosen verdict while preserving every other insight row and all three actions. Transient failures keep the keyboard. A purged insight removes only its own row, preserving other digest insights. No raw token or credential is logged.

## D19 check
On 2026-10-16, bu-k2z6iw is blocked and its proposed dashboard-one-mind-goal-surface change is absent on the baseline. Its draft describes one assistant with helpful region attribution but supplies no adopted insight-rendering rule. RFC 0038 D19 remains the direction; this change adds feedback controls without adopting draft dashboard behavior or changing channel authorship.

## Ownership
Only notify-feedback functions in shared _routing.py are changed. Ingestion target acceptance owned by PR4496 is outside scope.
