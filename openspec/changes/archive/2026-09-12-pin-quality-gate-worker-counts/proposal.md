# Make each quality-gate target state its own worker count

## Why

`make test-qg-serial` is documented as the "serial fallback for order-dependent
debugging". It ran on three xdist workers.

The recipe passed `QG_PYTEST_ARGS` with no `-n`, which reads as "no parallelism".
But pytest prepends `addopts` to every invocation, and this repository's `addopts`
carries `-n 3 --dist loadfile`. An omitted `-n` is therefore not a default -- it is
silently three workers.

That inverts the target's whole purpose. An order-dependent failure is precisely what
parallel workers reshuffle, so the one tool you reach for when you suspect ordering was
the one tool guaranteed not to answer the question. Measured before the fix, on the
target's real argv:

    numprocesses=3  dist='loadfile'   bringing up nodes...

and after:

    numprocesses=0  dist='no'  tx=[]  (no DSession registered)

Nobody noticed for the same reason it is worth writing down: the defect is invisible in
the diff that caused it. Someone put `-n 3` in `addopts`; nothing in the Makefile
changed, and no reader of the Makefile can see the merged value. `-n 3` was added for a
sound reason (three workers avoid OOM when polecats run alongside k3s) and the target it
broke is one nobody runs on a good day, so the inversion sat there until an unrelated
investigation into `--maxfail` exit codes surfaced it.

## What changes

- `make test-qg-serial` passes `-n 0` explicitly. `-p no:xdist` would instead turn the
  inherited `-n 3` into an unrecognized-argument error, so `-n 0` is the only override
  that works from the call site.
- A contract test pins the *effective* worker count of both gate targets, so `addopts`
  cannot silently re-parallelise the serial one again.
- The `testing` capability gains the requirement that a gate target states its execution
  mode rather than inheriting one.

## Why a test and not just the one-line fix

A one-line Makefile fix with no test is exactly how this regressed in the first place.
The value that matters does not exist until pytest merges `addopts` with argv, so any
guard that reads either half alone pins nothing: a grep for `-n 0` in the Makefile would
have passed happily while `addopts` grew a `--dist` clause that changed the answer.

`tests/contracts/test_qg_serial_target.py` therefore refuses to infer. It takes the
target's argv from `make -n` -- the command line that would really run, not a
hand-rolled expansion of Makefile variables -- and hands it to a real pytest process,
which reports `config.option.numprocesses` and whether xdist registered a distributed
session. The probe exits at `pytest_sessionstart`, after xdist has settled the
configuration and before DSession boots a worker, so both subprocesses finish in about a
second whether they pass or fail.

## Impact

- `Makefile`: `test-qg-serial` gains `-n 0`, and a comment recording why it is not
  redundant.
- `tests/contracts/test_qg_serial_target.py` (new): the serial target resolves to `-n 0`
  with no DSession; the default target stays distributed.
- `scripts/pytest_gate.py`: the docstring's "every run here is an xdist run" is now
  qualified — under `-n 0`, `--maxfail` raises `Failed` and exits `1` rather than
  `Interrupted` and exit `2`. Both statuses were already classified correctly; only the
  prose was stale.
- `docs/testing/markers-and-fixtures.md`: records the addopts-inheritance trap next to
  the parallel-execution table that documents `-n 3`.
- No CI workflow changes. Every workflow invocation either passes `-n auto` explicitly
  or is not claiming to be serial.
