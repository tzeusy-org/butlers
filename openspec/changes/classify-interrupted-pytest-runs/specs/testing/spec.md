## MODIFIED Requirements

### Requirement: Pytest Run Verdicts Require a Positive Terminator
A pytest run's outcome SHALL be established by positive evidence that the run finished — a summary line, or the process exit status — never by the absence of a failure line. A run that produced neither is UNKNOWN, and UNKNOWN SHALL NOT be treated as a pass.

Where both terminators are present and the exit status alone cannot distinguish a failed run from an unfinished one, the verdict SHALL read them together. That is exactly one status: pytest's `2`, the *interrupted* run, which `--maxfail` produces on an ordinary test failure. Reading it as UNKNOWN would make UNKNOWN the label on the most common red run there is, and UNKNOWN only carries weight while it stays rare.

#### Scenario: Truncated log has no verdict
- **WHEN** `scripts/pytest_gate.py verdict LOG` reads a log carrying neither a gate sentinel nor a pytest summary line, including the xdist truncation whose workers report `OSError: cannot send (already closed?)` after their controller is signal-killed
- **THEN** it reports `UNKNOWN` and exits `2`, so a shell `&&` chain fails closed
- **AND** it names the killed-controller signature when that marker is present, rather than reporting an undifferentiated UNKNOWN

#### Scenario: Sentinel carries the exit status and outranks the log prose
- **WHEN** a `## pytest-gate exit=N` sentinel is present
- **THEN** the verdict comes from `N` alone for every value except `2`: `0` is PASS, `1` is FAILED, and `3`, `4`, `5`, or any `128+signal` value is UNKNOWN because the suite rendered no verdict
- **AND** a nonzero sentinel outranks an earlier green summary line in the same log
- **AND** `N` of `2` is resolved by the interrupted-run scenario below, because that status alone does not say whether the run was stopped by a failure or by something that reached no verdict

#### Scenario: An interrupted run is read against its own counts
- **WHEN** the sentinel reports exit `2`, the status pytest uses for an interrupted session and therefore the status `--maxfail` produces on an ordinary test failure
- **THEN** the log's last pytest summary line decides it: counts including `failed` or `error` are FAILED
- **AND** exit `2` with no summary line at all is UNKNOWN, because the run stopped before establishing anything
- **AND** exit `2` whose last summary line counts no failures — a `Ctrl-C` partway through a green run — is UNKNOWN and SHALL NOT be PASS, because the run never reached the tests it was interrupted before
- **BECAUSE** the summary line is itself a positive terminator, so consulting it applies the rule twice rather than relaxing it; it may take exit `2` down to FAILED and never up to PASS

#### Scenario: No other nonzero status is softened by a summary line
- **WHEN** the sentinel reports `3`, `4`, `5`, or any `128+signal` value and the log also carries a pytest summary line
- **THEN** the verdict is UNKNOWN regardless of what those counts say: exit `5` with a green summary is not a PASS, and a signal exit with a failing summary is not a FAILED
- **BECAUSE** those statuses do not report an incomplete verdict awaiting corroboration, they report that no verdict exists — nothing was collected, the gate misfired, or the run was killed — and counts printed before that cannot contradict it

#### Scenario: Summary line classified only on positive counts
- **WHEN** no sentinel is present but a pytest summary line is
- **THEN** counts including `failed` or `error` are FAILED, `no tests ran` and any summary counting no passing tests are UNKNOWN, and only a summary reporting passes with no failures or errors is PASS
- **AND** the last summary line in the log wins, so a rerun's verdict supersedes the earlier one

#### Scenario: Run receipt survives the caller being killed
- **WHEN** `scripts/pytest_gate.py run` launches pytest and the caller's process group is signalled mid-run
- **THEN** pytest continues, because the child was started in its own session
- **AND** the sentinel is appended by that child rather than by the runner, so the receipt is written even though the runner is gone

#### Scenario: Quality-gate make targets produce a receipt and a verdict
- **WHEN** `make test-qg` or `make test-qg-serial` runs
- **THEN** pytest is launched through `scripts/pytest_gate.py run` on the project interpreter (`uv run python`, never a bare `python3`, which resolves outside the venv and turns every run into `ModuleNotFoundError` -> exit 4 -> UNKNOWN), writing its log under `.tmp/test-logs/`
- **AND** the target ends with `scripts/pytest_gate.py verdict`, whose exit status is the target's, so an UNKNOWN run fails the gate instead of passing silently
- **AND** the run is mirrored to the terminal as it goes, so routing through the gate costs no interactivity

