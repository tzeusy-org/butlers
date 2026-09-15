# Tasks

## 1. Measure
- [x] 1.1 Establish the current effective `-n` for `make test-qg-serial` by running its
      real argv through pytest and reading `config.option.numprocesses`, rather than
      inferring it from the absent flag. Result: `3`, `dist='loadfile'`, nodes booted.

## 2. Contract
- [x] 2.1 ADD the `testing` requirement **Quality-Gate Targets State Their Own Execution
      Mode**, with scenarios for the serial target, the default parallel target, and the
      form the guard has to take to mean anything.

## 3. Fix
- [x] 3.1 `make test-qg-serial` passes `-n 0` after `$(QG_PYTEST_ARGS)`.
- [x] 3.2 Record in the recipe comment why `-n 0` is not redundant and why `-p no:xdist`
      is not an option.
- [x] 3.3 Qualify the `scripts/pytest_gate.py` docstring claim that every run in this
      repository is an xdist run.
- [x] 3.4 Note the addopts-inheritance trap in `docs/testing/markers-and-fixtures.md`.

## 4. Tests
- [x] 4.1 `tests/contracts/test_qg_serial_target.py` takes the target's argv from
      `make -n`, not from parsing Makefile variables.
- [x] 4.2 It reads the effective value from a real pytest process, asserting
      `numprocesses == 0`, `dist == "no"`, an empty `tx`, and no registered `dsession`.
- [x] 4.3 The counterpart test pins `make test-qg` as still distributed, so the fix
      cannot be over-applied.
- [x] 4.4 Verified red: with `-n 0` removed from the Makefile the serial test fails,
      reporting `pytest resolved -n to 3`.
