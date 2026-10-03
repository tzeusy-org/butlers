## 1. Classifier and event

- [x] 1.1 `butlers.account_security` classifier and trust rules.
- [x] 1.2 `switchboard.security_event` contract and ingest publish.

## 2. Promotion carve-out

- [x] 2.1 Evaluator demotes `skip` for classified alerts; promotion refuses allowlisted senders.

## 3. Answer door

- [x] 3.1 `record_security_answer` (yes no-op, no opens a fleet case with the recovery door).

## 4. Deferred

- [ ] 4.1 Interactive "was this you?" notify prompt calling the answer door, gated on `authenticated`.
- [ ] 4.2 Unclassified-allowlisted-sender counter; Gmail Tier 2 carrying Authentication-Results.
- [ ] 4.3 Estate registry feed; manifesto amendment (owner decision).
