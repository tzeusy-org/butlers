## 1. Spec and verdict (this change, slice S1)

- [x] 1.1 OpenSpec change `add-organizer-verdict` validates.
- [x] 1.2 `organizer_verdict` pure function and closed lure badges; no free-text input.
- [x] 1.3 Migration: SECURITY DEFINER identity-class and connector-block functions.
- [x] 1.4 Projection stamps verdict, badges and `verdict_version` into event metadata.
- [x] 1.5 Idempotent bounded re-stamp sweep; a pre-existing blocked organizer's invite is stamped
      `blocked`.
- [x] 1.6 docs/modules/calendar.md.

## 2. Readers (slice S2)

- [ ] 2.1 Invitation read model and route: quarantine section, verdict and badges.
- [ ] 2.2 Day load, conflict radar, prep, meeting card, debrief and household context exclude
      quarantined invitations; an unstamped needsAction non-owner invite reads as `unknown`.

## 3. Owner verbs (slice S3)

- [ ] 3.1 Vouch and Block verbs with an idempotent re-stamp.

## 4. Connector hold (slice S4)

- [ ] 4.1 Connector holds the sync token and submits nothing when policy evaluation raises.
