# Historical migration evidence

`core_255_a090a8ac.py.txt` is the complete public `core_255` source from commit
`a090a8acd39e08a32bf4971e265e401e7334f42d` in PR #4347, before the combined
grant/retention correction. Its SHA256 is
`2fa2f2257f1416ab11f875a663674f80a8da182280d400c1b3e90927c6198a39`.

The owning PostgreSQL test verifies that digest, copies the current complete
Alembic environment into `tmp_path`, and substitutes this one revision there.
It compares the exact initial source with independent grant-only and keep-only
controls. Each destructive variant uses a separate disposable database and
asserts that its amendment ledger is empty before rollback. The old function
grants and search path are historical evidence, not a supported security
posture. The `.txt` extension keeps the snapshot outside Alembic discovery.

The current source stays authoritative. No historical applied migration is
edited, no deployed database is queried, and no new core revision is allocated.
