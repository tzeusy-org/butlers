## 1. Implementation

- [x] 1.1 Identifier-only log fields at every evaluator log site
- [x] 1.2 New caplog sentinel tests in `tests/connectors/test_discretion_logs_identifiers_only.py`
- [x] 1.3 Audit row updated in `docs/connectors/bearer-material-custody-audit.md`

## 2. Verification

- [x] 2.1 `openspec validate --all --strict`
- [ ] 2.2 Contract-evolution manifest binds the one retained assertion to `REQ-connector-base-spec-007`
