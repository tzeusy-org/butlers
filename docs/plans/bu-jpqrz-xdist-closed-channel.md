# pytest-xdist closed-channel session-finish contribution draft

- **Date:** 2026-09-12
- **Status:** Local diagnostic and upstream contribution draft; nothing has been posted upstream
- **Issue:** `bu-jpqrz`
**Audience:** A maintainer deciding whether to prepare an upstream pytest-xdist pull request

## Decision summary

The secondary session-finish error is reproducible at the real execnet remote-worker seam.
With pytest-xdist 3.8.0, severing the worker's outgoing execnet transport immediately before
`WorkerInteractor.pytest_sessionfinish` sends `workerfinished` produces:

```text
PluggyTeardownRaisedWarning: A plugin raised an exception during an old-style hookwrapper teardown.
OSError: cannot send (already closed?)
```

This is transport noise after the worker has computed its exit status. It is not evidence that the
underlying test failure, resource failure, or controller loss should be treated as successful. A
narrow upstream change is justified: suppress only `OSError` from the terminal
`channel.send(("workerfinished", ...))`, after the wrapped session-finish hooks have completed.
Keep the debug log call outside that exception boundary. Do not catch around `yield`, and do not
catch `BaseException`, `Exception`, serialization errors, or unrelated hook failures.

The candidate policy was exercised only in a disposable upstream checkout. It retained the normal
`workerfinished` payload, retained a failing test report and exit status 1 before synthetic transport
loss, and allowed an unrelated remote `RuntimeError` from `pytest_sessionfinish` to reach the
controller as `execnet.RemoteError`.

One suitable future contribution destination is a focused pull request against
[`pytest-dev/pytest-xdist:master`](https://github.com/pytest-dev/pytest-xdist), referencing open
issue [#1269](https://github.com/pytest-dev/pytest-xdist/issues/1269) for the observed secondary
warning without claiming to fix that report's PostgreSQL resource exhaustion. Do not file a new
issue. Publication remains a separate approval gate.

## Scope and boundaries

This draft does not change Butlers runtime code, `conftest.py`, plugins, dependencies, test
selection, or CI behavior. It does not vendor pytest-xdist. Butlers already treats a killed or
unterminated pytest run as `UNKNOWN` through `scripts/pytest_gate.py`; removing a worker-side
secondary traceback must not weaken that fail-closed controller/gate behavior.

The experiments used only worker-created processes and a disposable checkout under `/tmp`. No
production process was signaled or terminated. No upstream issue, comment, branch, or pull request
was created.

## Pinned evidence environment

| Item | Exact value used |
| --- | --- |
| pytest-xdist release | `v3.8.0` |
| pytest-xdist commit | `1e3e4dc16523c8a8f6c67d95a950166420718c99` |
| Upstream `master` inspected | `eba6a4475eb8697fc476357ff54d7b8b948781cf` |
| Python | `3.12.4` |
| pytest | `9.1.0` |
| execnet | `2.1.2` |
| pluggy | `1.6.0` |
| filelock | `3.20.3` |
| iniconfig | `2.3.0` |
| packaging | `26.3` |
| pygments | `2.21.0` |
| Platform | `Linux-5.15.0-190-generic-x86_64-with-glibc2.35` |

These pytest, pytest-xdist, execnet, and pluggy versions match the versions locked by Butlers at
the investigation commit. The latest published pytest-xdist release was still `v3.8.0` when
rechecked. Upstream `master` had not changed the relevant session-finish send relative to the tag;
its differences in `src/xdist/remote.py` were unrelated worker-ramp work.

## The actual seam

At the pinned release:

1. `testing/test_remote.py::WorkerSetup.setup` creates an execnet popen gateway and calls
   `WorkerController.setup`.
2. `WorkerController.setup` calls `gateway.remote_exec(remote_module)`. The remote module is
   source-loaded into the worker process, so monkeypatching an imported controller-side
   `WorkerInteractor` class does not modify the worker implementation.
3. `WorkerInteractor.pytest_sessionfinish` stores `exitstatus`, `shouldfail`, and `shouldstop`,
   yields to other session-finish hooks, then sends the `workerfinished` event.
4. execnet 2.1.2 raises `OSError: cannot send (already closed?)` when its underlying write transport
   is already closed.

The upstream source references are:

- [`WorkerSetup`](https://github.com/pytest-dev/pytest-xdist/blob/1e3e4dc16523c8a8f6c67d95a950166420718c99/testing/test_remote.py#L39-L68)
- [`test_happy_run_events_converted`](https://github.com/pytest-dev/pytest-xdist/blob/1e3e4dc16523c8a8f6c67d95a950166420718c99/testing/test_remote.py#L204-L230)
- [`WorkerInteractor.pytest_sessionfinish`](https://github.com/pytest-dev/pytest-xdist/blob/1e3e4dc16523c8a8f6c67d95a950166420718c99/src/xdist/remote.py#L141-L149)
- [`WorkerController.setup`](https://github.com/pytest-dev/pytest-xdist/blob/1e3e4dc16523c8a8f6c67d95a950166420718c99/src/xdist/workermanage.py#L325-L353)

`test_happy_run_events_converted` is itself marked `xfail("implement a simple test for event
production")`; it is useful as the event-conversion reference, not as green completion evidence.
The adjacent `test_basic_collect_and_runtests` supplies the existing completed-worker assertion.

## Ranked hypotheses and results

| Rank | Hypothesis | Falsifier | Result |
| --- | --- | --- | --- |
| 1 | The warning comes from the final `workerfinished` send after the transport is gone. | Real remote execution closes the transport but does not raise from that send. | Confirmed. The captured exception was the exact execnet `OSError`. |
| 2 | A controller-side monkeypatch is sufficient to exercise or fix the worker. | `remote_exec` source-loads `remote.py` independently. | Rejected. The real worker must be driven through `WorkerSetup`; an imported-class monkeypatch is the wrong seam. |
| 3 | Suppressing only the terminal send will also hide an inner session-finish failure. | A remote plugin raises `RuntimeError`, but the controller does not receive it. | Rejected. The error is thrown at `yield`, before the candidate catch, and remained an `execnet.RemoteError`. |
| 4 | The secondary send error is the primary test verdict. | A failing `testreport` and exit status 1 are absent before the send error. | Rejected. Both existed before synthetic transport loss. They cannot be delivered in `workerfinished` after the transport is gone, but the change does not rewrite them. |

## Reproduction

### 1. Create and pin a disposable checkout

```bash
scratch_dir="$(mktemp -d /tmp/bu-jpqrz-xdist.XXXXXX)"
trap 'test -n "${scratch_dir:-}" && rm -rf -- "$scratch_dir"' EXIT
git clone --filter=blob:none https://github.com/pytest-dev/pytest-xdist.git "$scratch_dir/upstream"
git -C "$scratch_dir/upstream" checkout v3.8.0
git -C "$scratch_dir/upstream" rev-parse HEAD
cd "$scratch_dir/upstream"
uv venv .venv --python 3.12.4
uv pip install --python .venv/bin/python --no-deps -e .
uv pip install --python .venv/bin/python \
  'pytest==9.1.0' \
  'execnet==2.1.2' \
  'pluggy==1.6.0' \
  'filelock==3.20.3' \
  'iniconfig==2.3.0' \
  'packaging==26.3' \
  'pygments==2.21.0'
.venv/bin/python - <<'PY'
import importlib.metadata
import platform

expected = {
    "pytest-xdist": "3.8.0",
    "pytest": "9.1.0",
    "execnet": "2.1.2",
    "pluggy": "1.6.0",
    "filelock": "3.20.3",
    "iniconfig": "2.3.0",
    "packaging": "26.3",
    "pygments": "2.21.0",
}
actual = {name: importlib.metadata.version(name) for name in expected}
assert platform.python_version() == "3.12.4", platform.python_version()
assert actual == expected, actual
print(f"python=={platform.python_version()}")
for name, version in actual.items():
    print(f"{name}=={version}")
PY
```

The checkout must print
`1e3e4dc16523c8a8f6c67d95a950166420718c99`. The verification block fails unless
Python is exactly 3.12.4 and every package in the pinned evidence table has the exact recorded
version. The editable pytest-xdist install is sourced from the exact checked-out tag and uses
`--no-deps`; every dependency used by this reproduction is then installed explicitly.

### 2. Establish normal completion

```bash
timeout --signal=TERM --kill-after=5s 30s \
  .venv/bin/python -m pytest \
  testing/test_remote.py::TestWorkerInteractor::test_happy_run_events_converted \
  testing/test_remote.py::TestWorkerInteractor::test_basic_collect_and_runtests \
  -vv --tb=short
```

Observed exit status: `0`. Observed result: `1 passed, 1 xfailed`. The xfail is the existing
`test_happy_run_events_converted`; `test_basic_collect_and_runtests` received `workerfinished` and
asserted that `workeroutput` was present.

### 3. Add the one real-remote diagnostic/regression shape

Add one temporary test beside `test_happy_run_events_converted` in `testing/test_remote.py`. This is
the proposed closed-channel regression shape; it deliberately uses `WorkerSetup` and remote-side
hook code. The outgoing transport close is a synthetic test seam, not a proposed production API.

```python
def test_sessionfinish_ignores_closed_workerfinished_transport(
    self,
    worker: WorkerSetup,
    unserialize_report: UnserializerReport,
) -> None:
    import time

    record = worker.pytester.path / "sessionfinish-record.txt"
    worker.pytester.makeconftest(
        f"""
        from pathlib import Path
        import pytest

        RECORD = Path({str(record)!r})

        def interactor(session):
            matches = [
                plugin
                for plugin in session.config.pluginmanager.get_plugins()
                if plugin.__class__.__name__ == "WorkerInteractor"
            ]
            assert len(matches) == 1
            return matches[0]

        @pytest.hookimpl(
            hookwrapper=True, tryfirst=True, specname="pytest_sessionfinish"
        )
        def pytest_sessionfinish_capture(session, exitstatus):
            outcome = yield
            try:
                outcome.get_result()
            except BaseException as exc:
                result = f"{{type(exc).__name__}}: {{exc}}"
            else:
                result = "none"
            RECORD.write_text(
                f"exitstatus={{int(exitstatus)}}\\nexception={{result}}\\n",
                encoding="utf-8",
            )

        @pytest.hookimpl(trylast=True)
        def pytest_sessionfinish(session, exitstatus):
            if hasattr(session.config, "workerinput"):
                remote = interactor(session)
                remote.sendevent("close_now")
                remote.channel.gateway._io.close_write()
        """
    )
    worker.pytester.makepyfile(
        """
        def test_func():
            assert False, "primary failure remains in testreport"
        """
    )
    worker.setup()
    ev = worker.popevent("collectionfinish")
    worker.sendcommand("runtests", indices=list(range(len(ev.kwargs["ids"]))))
    worker.sendcommand("shutdown")

    call_report = None
    while True:
        ev = worker.popevent()
        if ev.name == "testreport":
            report = unserialize_report(ev.kwargs["data"])
            if report.when == "call":
                call_report = report
        if ev.name == "close_now":
            break
    assert call_report is not None
    assert call_report.failed
    assert "primary failure remains in testreport" in str(call_report.longrepr)

    deadline = time.monotonic() + 5
    while not record.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError("remote sessionfinish record was not written")
        time.sleep(0.01)
    assert record.read_text(encoding="utf-8") == ("exitstatus=1\nexception=none\n")
    worker.slp.channel.close()
```

Run it with a hard timeout so every unsuccessful experiment terminates:

```bash
timeout --signal=TERM --kill-after=5s 30s \
  .venv/bin/python -m pytest \
  testing/test_remote.py::TestWorkerInteractor::test_sessionfinish_ignores_closed_workerfinished_transport \
  -vv -s --tb=short
```

On unmodified 3.8.0, the proposed assertion fails because the record contains:

```text
exitstatus=1
exception=OSError: cannot send (already closed?)
```

The remote process also emits the `PluggyTeardownRaisedWarning`. The controller has already received
the failing call-phase `testreport` containing `primary failure remains in testreport` before the
transport is severed.

For the passing-worker comparison, change the generated `test_func` body to `pass`, replace
`assert call_report.failed` with `assert call_report.passed`, remove the failure-message assertion,
and expect exit status 0. Unmodified 3.8.0 then records `exitstatus=0` plus the same secondary
`OSError`; the candidate records `exitstatus=0` plus `exception=none`. Both variants were run. This
comparison does not need to become a second upstream regression because the existing
normal-completion test already protects the connected `workerfinished` payload.

### 4. Exercise the candidate policy

The narrow draft change in `WorkerInteractor.pytest_sessionfinish` is:

```diff
         workeroutput["shouldstop"] = self.session.shouldstop
         yield
-        self.sendevent("workerfinished", workeroutput=workeroutput)
+        kwargs = {"workeroutput": workeroutput}
+        self.log("sending", "workerfinished", kwargs)
+        try:
+            self.channel.send(("workerfinished", kwargs))
+        except OSError:
+            pass
```

Then run:

```bash
timeout --signal=TERM --kill-after=5s 30s \
  .venv/bin/python -m pytest \
  testing/test_remote.py::TestWorkerInteractor::test_sessionfinish_ignores_closed_workerfinished_transport \
  testing/test_remote.py::TestWorkerInteractor::test_basic_collect_and_runtests \
  -q -s --tb=short
```

Observed candidate result: both tests passed. The synthetic record contained `exitstatus=1` and
`exception=none`; the failing `testreport` had already reached the controller-side harness; the
normal case still received the ordinary `workerfinished` payload. No closed-channel teardown
warning appeared.

### 5. Check an unrelated exception

A separate disposable diagnostic, not a proposed second upstream regression, completes the normal
collection, run, and shutdown lifecycle while the channel remains connected:

```python
def test_unrelated_sessionfinish_exception_is_not_suppressed(
    self, worker: WorkerSetup
) -> None:
    worker.pytester.makeconftest(
        """
        import pytest

        @pytest.hookimpl(trylast=True)
        def pytest_sessionfinish(session, exitstatus):
            if hasattr(session.config, "workerinput"):
                raise RuntimeError("unrelated sessionfinish failure")
        """
    )
    worker.pytester.makepyfile("def test_func(): pass")
    worker.setup()
    ev = worker.popevent("collectionfinish")
    worker.sendcommand("runtests", indices=list(range(len(ev.kwargs["ids"]))))
    worker.sendcommand("shutdown")

    with pytest.raises(
        worker.slp.channel.RemoteError,
        match="unrelated sessionfinish failure",
    ):
        list(worker.slp.channel)
```

Run the control with the same bounded lifecycle:

```bash
timeout --signal=TERM --kill-after=5s 30s \
  .venv/bin/python -m pytest \
  testing/test_remote.py::TestWorkerInteractor::test_unrelated_sessionfinish_exception_is_not_suppressed \
  -vv -s --tb=short
```

Observed result with the candidate policy: the diagnostic passed because the unrelated
`RuntimeError` reached the controller as `execnet.RemoteError`. This happens structurally because
the wrapped hook error is thrown at `yield`; execution never reaches the later, narrowly scoped
`channel.send` catch.

## Exception and race policy

The candidate policy is intentionally small:

- Build `workeroutput` before `yield`, exactly as today.
- Let every inner session-finish hook exception propagate at `yield`.
- Keep logging outside the exception handler, so an unrelated debug-output failure is not swallowed.
- Catch only `OSError` raised by the final execnet `channel.send` of `workerfinished`.
- Let all non-`OSError` failures, including serialization failures, propagate unchanged.
- Leave controller-side worker-loss, worker-exit, and test-result handling untouched.

There is no useful recovery for this one send when the transport is gone: retrying the same dead
channel cannot deliver `workeroutput`. The worker's locally computed exit status is not rewritten;
it remains available in normal completion and was observed as 1 in the failing synthetic run. When
the controller is absent, no worker-side exception policy can make that payload observable to it.
That is why the change is noise suppression only, not a success classification or crash-recovery
change.

## Upstream issue and related-work check

The following was rechecked against GitHub on 2026-09-12:

- [Issue #1269](https://github.com/pytest-dev/pytest-xdist/issues/1269) is open, has one comment,
  has no linked development branch or pull request, and was last updated on 2025-11-07. Its primary
  failure is PostgreSQL `out of shared memory`; the maintainer response correctly notes that xdist
  does not manage application resources. Its log also contains the same secondary
  `pytest_sessionfinish` send error.
- [Issue #204](https://github.com/pytest-dev/pytest-xdist/issues/204) remains open and concerns
  controller behavior when a worker crashes or raises internally. It reinforces the requirement
  not to turn a primary error into success, but it is not a duplicate of this terminal-send race.
- [Issue #1278](https://github.com/pytest-dev/pytest-xdist/issues/1278) and open
  [PR #1302](https://github.com/pytest-dev/pytest-xdist/pull/1302) concern reporting a worker
  process's nonzero exit to a live controller. That is the opposite direction from a worker trying
  to send `workerfinished` after controller transport loss; this draft does not subsume or modify
  that work.
- Searches over open and closed upstream issues and pull requests for `pytest_sessionfinish`,
  `workerfinished`, `PluggyTeardownRaisedWarning`, `closed channel`, and `cannot send` found no
  pull request implementing this narrow session-finish policy.

Therefore the single proposed destination is a new focused pytest-xdist pull request against
`master`, with one real-remote closed-channel regression and a reference to #1269. The PR should
say explicitly that it suppresses only a secondary transport error and does not fix PostgreSQL
resource exhaustion, controller termination, worker crash classification, or test selection.

## Evidence ledger

| Run | Unmodified or candidate | Outcome |
| --- | --- | --- |
| Upstream happy/basic references | Unmodified 3.8.0 | Exit 0; `1 passed, 1 xfailed`; normal `workerfinished.workeroutput` observed. |
| Synthetic closed transport, passing inner test | Unmodified 3.8.0 | Exit 0 for the diagnostic harness; captured worker exit status 0 plus exact secondary `OSError`; teardown warning emitted. |
| Synthetic closed transport, failing inner test | Unmodified 3.8.0 | Exit 0 for the diagnostic harness; failed call `testreport` observed; captured worker exit status 1 plus exact secondary `OSError`; teardown warning emitted. |
| Synthetic closed transport, failing inner test | Candidate patch | Exit 0; failed call `testreport` observed; captured worker exit status 1 and no session-finish exception; no closed-channel warning. |
| Normal completed worker | Candidate patch | Exit 0; existing completed-worker test passed and received `workeroutput`. |
| Unrelated remote session-finish `RuntimeError` | Candidate patch | Exit 0 for the diagnostic harness because it observed the expected `execnet.RemoteError`; the unrelated exception was not suppressed. |

These runs establish the exact send failure and the behavior of the candidate exception boundary in
the pinned environment. They do not establish cross-platform behavior or maintainer acceptance.
An eventual upstream PR should let pytest-xdist's hosted Python/OS matrix provide that broader
evidence before merge.

## Publication checklist

- [ ] Rebase the draft onto the then-current pytest-xdist `master` and re-pin its SHA.
- [ ] Re-run the one real-remote regression red on unmodified upstream and green with the candidate.
- [ ] Run the existing `testing/test_remote.py` scope in the upstream environment.
- [ ] Run upstream lint/type checks and the supported test matrix required by its contributor guide.
- [ ] Reference #1269 without claiming to resolve its PostgreSQL resource failure.
- [ ] Confirm no newer issue or pull request owns the same terminal-send race.
- [ ] Obtain explicit approval before creating any upstream issue, comment, branch, or pull request.

## Disposal

The investigation checkout and all child processes are disposable. The reproduction commands use a
30-second timeout with a 5-second forced-cleanup grace, and the shell trap removes only the exact
directory returned by `mktemp`. The completed investigation removed its worker-created disposable
checkout after recording the evidence above.

Butlers test delta for this draft: `Tests: +0 ~0 -0`.
