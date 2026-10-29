# Standard Lint Tools

> **Scope:** What `make lint` runs, how the pinned secret scanner and workflow linter are installed,
> what their output may contain, and how to review an allowance or roll the tools back.
> **Belongs here:** Ruff rule selection, gitleaks and actionlint wiring, allowlist review.
> **Does NOT belong here:** Test selection and CI shards (see [Testing Strategy](testing-strategy.md)).

`make lint` is the single lint path: CI's guards job runs exactly `make lint`, so a local pass is
the CI result for these checks. It runs, in order:

| step | command | fails on |
| --- | --- | --- |
| Ruff | `uv run ruff check src/ tests/ roster/ scripts/` | rules E, F, I, UP, B, ASYNC |
| Package layers | `uv run --no-sync lint-imports` | the import contracts in `pyproject.toml` |
| Secret scan | `make secrets-scan` | any gitleaks finding not covered by a reviewed allowance |
| Workflow lint | `make lint-workflows` | any actionlint finding in `.github/workflows/*.yml` / `*.yaml` |
| Detector self-test | `make lint-tools-self-test` | either tool missing a planted fault |

## Installing the tools

```bash
make install-lint-tools   # once per worktree; CI runs it before Lint
```

`scripts/lint_tools.py install` downloads the releases pinned in `scripts/lint-tool-versions.json`
(gitleaks 8.30.1 and actionlint 1.7.12, for Linux and macOS on x86_64 and arm64), verifies each
archive's SHA-256, extracts only the single top-level binary (no traversal, links or nested
members), and publishes the tool directory into the ignored `.tmp/lint-tools/` cache in one rename.
Downloads are bounded to 60 s and 64 MiB; each tool run to 300 s and 16 MiB of output.

The scan commands never download. A missing tool, a cache that does not match its pin, or a binary
that changed after its verified install is a failure naming `make install-lint-tools`, never a skip.
`BUTLERS_LINT_TOOLS_DIR` moves the cache (the tests use it).

To change a pin, update the version, URLs and digests together from the official release
checksums, then run `make install-lint-tools` and `make lint`.

## Secret scan

`make secrets-scan` scans the checked-out source: Git-tracked plus untracked non-ignored files,
copied (regular files only; symlinks are not followed) into a private temporary directory outside
the repository. It does not scan Git history, other worktrees, or ignored files such as `.env`
and caches, so a pass is not a history-clean claim.

### Active source-export failure matrix (AC8)

The source boundary is the checked-out directory, not the index's historical path types.
Every source component must be opened relative to an already-open checkout directory without
following symlinks. Copying uses the resulting file descriptor; a path check followed by a new
path-based read is insufficient. Missing working-tree files and final symlinks remain excluded;
an inaccessible or non-directory ancestor refuses the export categorically. No outside content
or raw filesystem error may appear in diagnostics.

| Failure or transition | Required outcome | Executing evidence |
| --- | --- | --- |
| Tracked, staged-added, non-ignored untracked, or index-removed but present regular file | Export current bytes with mode 0600 | Source-export manifest regression |
| Ignored or working-tree-deleted file | Exclude it | Source-export manifest regression |
| Deleted parent directory, Gitlink directory, or final FIFO | Exclude it without blocking or reading it | Source-export manifest regression |
| Tracked file replaced by a final symlink, before or after staging | Exclude it without reading its target | Source-export manifest regression |
| Tracked directory replaced by an outside-directory symlink, unstaged | Refuse export; never read the outside file | Source-export manifest regression |
| Directory replacement staged, removing its former indexed children | Exclude the remaining final symlink | Source-export manifest regression |
| Parent or final file replaced by a symlink immediately before its open | Refuse parent / exclude final file | Source-export replacement regression |
| Parent or final path replaced after its descriptor is opened | Copy only the originally opened source bytes | Source-export replacement regression |

The executing owners in `tests/contracts/test_standard_lint_tools.py` are
`test_source_export_confines_git_manifest_transitions` (13 cases, including categorical exit 2
and sanitized diagnostics) and `test_source_export_confines_replacements_during_open` (4 cases).
All inputs, including outside-directory targets, are disposable nonsecret data under the test's
temporary directory. The matrix remains active through independent review; focused passes prove
these boundaries, while hosted CI remains the broader integration evidence.

gitleaks runs with `--redact=100`, the reviewed `.gitleaks.toml`, no `.gitleaksignore`
fingerprints, inline `gitleaks:allow` comments ignored, and every ambient `GITLEAKS_*` variable
removed. Its stdout, stderr and report stay in the private directory and are deleted. The command
prints only `path:line: rule-id` per finding and a count, never the secret, match, author or
entropy. If a finding is a real credential, treat the value as compromised: tell the owner so it can
be rotated, and do not paste it anywhere.

### Reviewing an allowance

`.gitleaks.toml` extends the default rules. Each `[[allowlists]]` entry is a reviewed false
positive that names exact paths and exact rules (`condition = "AND"`) and pins the allowed values:
exact anchored literals (vendored bundle constants, test fixtures, prose identifiers) or a fixed
nonsecret shape a credential cannot meet (a SHA-256 digest, a model id, a timezone). A real
credential in the same file therefore still fails. The self-test enforces this: it refuses any
allowance that is not rule-scoped to exact literal paths, plants random real-shaped values in every
allowed path, and fails if any goes undetected. Add an entry only with its reason in
`description`. Never allow a directory, all tests, a baseline report, or a value you have not
identified. Prefer making a test fixture obviously synthetic (for example `abc123`) over widening
an allowance.

## Workflow lint

`make lint-workflows` runs actionlint on every workflow file with `-shellcheck= -pyflakes=`, so the
result does not depend on which optional linters a host has. It prints `file:line:column: kind` per
finding; run the printed actionlint command on a file for the full messages. Exit status 1 is
findings; 2 and 3 are tool errors and also fail.

## Detector self-test

`make lint-tools-self-test` proves both detectors on disposable inputs each run: gitleaks must find
a freshly generated, never-issued AWS-shaped key id and leave it out of its report, pass a clean
file, and catch random real-shaped values planted in every path a `.gitleaks.toml` allowance
covers; actionlint must pass a valid workflow and flag a malformed one.
`tests/contracts/test_standard_lint_tools.py` covers the fail-closed install and report handling,
and repeats the detector checks when the tools are installed.

## Rollback

Revert the Makefile `lint` recipe, the CI "Install lint tools" step, `.gitleaks.toml`,
`scripts/lint_tools.py` and `scripts/lint-tool-versions.json` together, and delete
`.tmp/lint-tools/`. The Ruff source fixes are independent and stay.
