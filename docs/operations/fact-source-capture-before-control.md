# Old-runtime fact-capture PostgreSQL witness

The separately named `Fact Source Capture BEFORE Control` workflow is a regression-evidence
control. It does not change ordinary CI fan-in and does not replace the full protected gate.
Only a separately reviewed and ROOT-admitted PR-triggered execution supplies evidence. There
is no manual dispatch, arbitrary command, selector, revision input or deployed database.

The fixed workload is `9b46e8e7342411cf3e8f2b75c139d077509233b0`. In a disposable checkout,
only `src/butlers/core/fact_authority.py` is replaced with its immutable
`37d3917c38080a0e43aa73d8f114b715c2377568` blob. The complete mode/blob manifest, physical
overlay tree, runtime hash, lock, interpreter, controller source, run/attempt and fresh nonce
remain bound. Its real venv imports that disposable checkout's source.

The existing `test_capacity_one_registered_owner_capture_and_concurrent_sources` node runs
unchanged, with its actual migrated PostgreSQL roles, capacity-one pool and registered
Relationship MCP fixtures. The original 15-second capture and 300-second item limits remain.
Only a genuine in-CALL old-code nested-acquire `TimeoutError` qualifies as the expected RED.
Setup failure, missing CALL, skip, another exception, healthy BEFORE, timeout or incomplete
cleanup remains refused/UNKNOWN. A registered endpoint does not prove the blocked old code
reached a network lookup. The observer records only actual known reachability.

Before fixture teardown the observer checks cancelled capture settlement, pool reacquisition,
transaction release and `SELECT 1`, zero partial durable and in-memory reports. Complete actual
fixture/process teardown and an unrelated sentinel must survive. Observation never replaces
or mocks the pool, migration, resolver, transaction or test body. After complete cleanup the
candidate runtime is restored exactly and the same node executes once in a fresh process.
It must pass setup/CALL/teardown. No source retry is allowed.

The old test remains **FAILED**. `WITNESS_COMPLETE` is a separate aggregate control verdict for
that observed RED, paired candidate GREEN and complete cleanup. Its schema stays distinct
from complete shard, smoke, coverage or release evidence. Existing candidate AFTER evidence
and the mandatory full protected source tree remain separate.

The producer invokes the existing owned `install_ci_uv.py` within its own confined domain,
with a private path-output sink. The existing thirteen workflow installer consumers remain
unchanged; installation is part of this control rather than a new workflow installer step.
The existing confined child subreaper owns every execution domain. The aggregate 2480-second
budget starts before checkout and includes the preserved 640-second UV installer and
640-second environment reserve, two 480-second node envelopes including cleanup, and 120
seconds each for setup and evidence. The 45-minute job is provisional, not measured p95.
Every phase receives remaining time and retains cleanup reserve; expiration/cancellation
cannot produce a witness. No local Docker/PG privilege retry is supported.

Only bounded closed JSON identity/phase/cleanup receipts are uploaded. Raw failure output stays
in protected runner-temporary files and is removed with the disposable checkout; it is never
an artifact. DSNs, credentials, payloads, traceback locals and arbitrary logs are excluded.
Missing/upload/cancelled/incomplete evidence remains UNKNOWN. Actual provider archive digest,
source readback and independent complete receipt verification are required after execution.
