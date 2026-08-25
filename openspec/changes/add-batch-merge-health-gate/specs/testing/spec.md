## ADDED Requirements

### Requirement: Post-Merge Integrity Gate Consumption
The project SHALL consume the target branch's post-merge integrity verdicts before
merging the next pull request in a batch, and SHALL halt the batch rather than
merge onto a branch whose verdicts are red or not yet trustworthy. A pull request's
own CI can only observe its own branch, so a per-PR green tick is not evidence
about the merged tree; the post-merge gates are, and a gate nothing reads produces
no protection.

#### Scenario: A red target branch halts the batch
- **WHEN** a push-triggered workflow on the target branch has concluded with a
  failing conclusion for the exact base SHA a merge would land on
- **THEN** the merge route returns a nonzero, batch-halting result and issues no
  merge request
- **AND** the result names the workflow that is red

#### Scenario: Absence of a run is never read as a pass
- **WHEN** no workflow run exists for the base SHA and the merged commit changed a
  path the workflow's `paths:` filter covers
- **THEN** the verdict is "not created yet" and the batch waits rather than merging
- **AND** an exhausted wait budget resolves to a halt, never to a proceed

#### Scenario: A path filter exclusion is distinguished from a missing run
- **WHEN** no workflow run exists for the base SHA and the merged commit changed no
  path the workflow's `paths:` filter covers
- **THEN** the verdict is "excluded by path filter" and the merge may proceed
- **AND** a changed-path list that cannot be determined or was truncated yields an
  unknown verdict instead of an exclusion

#### Scenario: In-flight and cancelled runs are unknown, not green
- **WHEN** a run for the base SHA reports an empty-string conclusion, or a
  `cancelled` conclusion
- **THEN** the verdict is unsettled and the batch waits
- **AND** a workflow whose concurrency group is keyed on the branch ref with
  `cancel-in-progress` is not polled for a per-SHA verdict at all, because every
  push to the branch cancels the previous run

#### Scenario: Local guards are enumerated from the tree under test
- **WHEN** the repo-wide guard sweep runs against a checkout of the merged tree
- **THEN** the guards are discovered from that tree's own CI definition rather than
  a list frozen in the gate, so a guard added after a branch was cut still runs
- **AND** a guard that leaves the tree dirty is reported as a failure, covering
  generator-style guards whose signal is a rewritten file rather than an exit code

#### Scenario: Landing the fix for a red target branch stays possible
- **WHEN** an operator acknowledges one specific red workflow by name
- **THEN** the merge route proceeds despite that workflow being red
- **AND** any other red workflow still halts the batch
