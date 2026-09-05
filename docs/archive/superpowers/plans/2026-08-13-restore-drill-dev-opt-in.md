> **ARCHIVED** — This implementation/design plan is historical. Archived on 2026-09-06.
> **Reason:** The --with-restore-drill launcher opt-in; the restore-drill contract is owned by the deployment-hardening spec.
> **Successor:** `openspec/specs/deployment-hardening/spec.md`.
>
> The doctrine on archival lives in `openspec/specs/docs-information-architecture/spec.md`.

# Restore-Drill Dev Opt-In Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let ordinary dev `./scripts/compose.sh` run the non-privileged base stack without a restore-drill credential, while preserving the existing fail-closed protected path for explicit dev opt-in and production.

**Architecture:** A new `--with-restore-drill` CLI flag controls a launcher-only boolean. Dev defaults to the base Compose file; `--prod` forces the boolean on. Every protected-only concern—the password-file and endpoint checks, merged Compose fragment, root-owned prepare/create/fence sequence, and nonce cleanup—uses that one boolean. Tests execute a copied launcher with fake Docker/sudo binaries, so they prove the real process boundary without a live stack.

**Tech Stack:** Bash, Docker Compose command construction, pytest, OpenSpec, Markdown.

## Global Constraints

- Never infer protected opt-in from a present secret or endpoint setting; only `--with-restore-drill` or `--prod` enables it.
- Do not read, print, fabricate, commit, or mutate a restore-drill password file.
- Do not change `butlers deploy`, direct-compose default-deny behavior, or the protected root-owned firewall contract.
- Ordinary dev must omit the protected Compose fragment and must not resolve or validate restore-only endpoint settings.
- Enabled dev and all production paths must retain the current fail-closed password preflight and prepare/create/fence-before-`up` ordering.
- Tests must use fake process boundaries only; no Docker lifecycle, firewall, database, or restore-drill runtime mutation.

---

### Task 1: Add behavior-executing launcher selection regressions

**Files:**

- Modify: `tests/config/test_restore_drill_executor_compose.py`

**Interfaces:**

- Consumes: copied `scripts/compose.sh`, a temporary `.env.dev` or `.env.prod`, and fake `docker`, `sudo`, `git`, `bd`, and `getent` commands.
- Produces: recorded command ordering and process exits for default dev, protected dev, and production launcher selections.

- [ ] **Step 1: Write the failing fake-launcher harness and default-dev test**

```python
def _run_launcher_harness(tmp_path: Path, *args: str, env_name: str, env_text: str) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    repo = tmp_path / "repo"
    scripts = repo / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(_COMPOSE_LAUNCHER, scripts / "compose.sh")
    shutil.copy2(_REPO_ROOT / "scripts" / "base-image-input-fingerprint.sh", scripts)
    for relative in (
        "Dockerfile.base",
        "scripts/runtime_cli_sandbox_init.c",
        "scripts/generate_runtime_cli_sandbox_manifest.py",
    ):
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("test input\\n", encoding="utf-8")
    (repo / f".env.{env_name}").write_text(env_text, encoding="utf-8")
    # Install fake docker/sudo/git/bd/getent commands that append only command
    # metadata to a test-local log; the sudo prepare form emits a valid nonce.
    ...
    completed = subprocess.run(
        ["bash", scripts / "compose.sh", *args],
        check=False,
        cwd=repo,
        capture_output=True,
        env=environment,
        text=True,
    )
    return completed, calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []


def test_dev_launcher_uses_only_base_compose_without_restore_drill_configuration(tmp_path: Path) -> None:
    completed, calls = _run_launcher_harness(
        tmp_path,
        "--skip-oauth-check",
        "--skip-tailscale-check",
        env_name="dev",
        env_text=(
            "POSTGRES_HOST=postgres.example.test\\n"
            "POSTGRES_PORT=5432\\n"
            "POSTGRES_PASSWORD=non-secret-test-value\\n"
            "RESTORE_DRILL_EXECUTOR_DB_HOST=127.0.0.1\\n"
            "RESTORE_DRILL_EXECUTOR_FIREWALL_DB_HOST=127.0.0.1\\n"
        ),
    )

    assert completed.returncode == 0, completed.stderr
    assert "Restore drill: disabled" in completed.stdout
    assert all("docker-compose.restore-drill.yml" not in call for call in calls)
    assert all("restore-drill-postgres-proxy" not in call for call in calls)
    assert all(_FIREWALL_WRAPPER not in call for call in calls)
```

- [ ] **Step 2: Add protected-path failure and ordering tests**

```python
def test_dev_opt_in_requires_password_file_before_lifecycle(tmp_path: Path) -> None:
    completed, calls = _run_launcher_harness(
        tmp_path,
        "--with-restore-drill",
        "--skip-oauth-check",
        "--skip-tailscale-check",
        env_name="dev",
        env_text="POSTGRES_HOST=postgres.example.test\\nPOSTGRES_PORT=5432\\n",
    )

    assert completed.returncode == 1
    assert "RESTORE_DRILL_EXECUTOR_PASSWORD_FILE" in completed.stderr
    assert calls == []


def test_dev_opt_in_preserves_prepared_protected_start_order(tmp_path: Path) -> None:
    password_file = tmp_path / "password-file"
    password_file.write_text("test-only-password-marker\\n", encoding="utf-8")
    completed, calls = _run_launcher_harness(
        tmp_path,
        "--with-restore-drill",
        "--skip-oauth-check",
        "--skip-tailscale-check",
        env_name="dev",
        env_text=(
            "POSTGRES_HOST=postgres.example.test\\nPOSTGRES_PORT=5432\\n"
            f"RESTORE_DRILL_EXECUTOR_PASSWORD_FILE={password_file}\\n"
            "RESTORE_DRILL_EXECUTOR_FIREWALL_DB_HOST=10.23.4.5\\n"
        ),
    )

    assert completed.returncode == 0, completed.stderr
    assert calls.index(next(call for call in calls if " down --remove-orphans" in call)) < calls.index(
        next(call for call in calls if "--prepare-executor-capability-v1" in call)
    ) < calls.index(next(call for call in calls if "create restore-drill-postgres-proxy restore-drill-executor" in call))
    assert str(password_file) not in completed.stdout + completed.stderr
```

- [ ] **Step 3: Run the new tests before production changes**

Run: `uv run pytest tests/config/test_restore_drill_executor_compose.py -q -k 'dev_launcher_uses_only_base or dev_opt_in'`

Expected: FAIL. Current code either rejects default dev for the missing private password file or rejects `--with-restore-drill` as an unknown flag.

### Task 2: Gate the protected launcher path behind the explicit selection

**Files:**

- Modify: `scripts/compose.sh`
- Modify: `tests/config/test_restore_drill_executor_compose.py`

**Interfaces:**

- Consumes: `--with-restore-drill`, `--prod`, `.env.<mode>`, and the existing private password-file/endpoint values.
- Produces: `CMD=(docker compose -f docker-compose.yml)` for ordinary dev, and the existing merged command plus prepared lifecycle for protected dev/prod.

- [ ] **Step 1: Add a single explicit launcher control**

```bash
RESTORE_DRILL_ENABLED=false

for arg in "$@"; do
  case "$arg" in
    --prod)                 BUTLERS_MODE=prod ;;
    --with-restore-drill)   RESTORE_DRILL_ENABLED=true ;;
    # existing flags unchanged
  esac
done

if [ "$BUTLERS_MODE" = "prod" ]; then
  RESTORE_DRILL_ENABLED=true
fi
readonly RESTORE_DRILL_ENABLED
```

Keep baseline `POSTGRES_HOST`/`POSTGRES_PORT` raw-whitespace checks active, but skip restore-only raw endpoint checks unless `RESTORE_DRILL_ENABLED=true`.

- [ ] **Step 2: Guard every protected-only stage**

```bash
if [ "$RESTORE_DRILL_ENABLED" = "true" ]; then
  # Existing password-file preflight and all restore endpoint resolution.
fi

CMD=(docker compose -f docker-compose.yml)
if [ "$RESTORE_DRILL_ENABLED" = "true" ]; then
  CMD+=(-f docker-compose.restore-drill.yml)
fi

if [ "$RESTORE_DRILL_ENABLED" = "true" ]; then
  # Existing prepare nonce, create relay/executor, and root-owned fence.
fi
"${CMD[@]}" up -d "${SCALE_ARGS[@]}"
if [ "$RESTORE_DRILL_ENABLED" = "true" ]; then
  unset RESTORE_DRILL_EXECUTOR_FIREWALL_CAPABILITY_NONCE
fi
```

Update existing password and endpoint-boundary tests to set `RESTORE_DRILL_ENABLED=true` in their isolated shell environment. Add a production missing-secret test to retain the mandatory production posture.

- [ ] **Step 3: Run RED/GREEN focused checks**

Run:

```bash
uv run pytest tests/config/test_restore_drill_executor_compose.py -q
bash -n scripts/compose.sh
uv run ruff check tests/config/test_restore_drill_executor_compose.py
uv run ruff format --check tests/config/test_restore_drill_executor_compose.py
```

Expected: all commands exit 0; the default dev harness records no protected Compose or root-wrapper call, while explicit dev/prod retain fail-closed behavior.

### Task 3: Align the operational and OpenSpec contract

**Files:**

- Modify: `docs/operations/docker-deployment.md`
- Modify: `docs/operations/backup-restore.md`
- Modify: `scripts/README.md`
- Modify: `openspec/changes/restore-drill-recovery-truthfulness/design.md`
- Modify: `openspec/changes/restore-drill-recovery-truthfulness/specs/deployment-hardening/spec.md`
- Modify: `tests/config/test_restore_drill_executor_compose.py`

**Interfaces:**

- Consumes: the explicit CLI selection and the existing protected firewall contract.
- Produces: consistent instructions that only opted-in dev, production, and `butlers deploy` may include the protected overlay.

- [ ] **Step 1: State the operator behavior without revealing a secret path**

Document these exact modes:

```text
./scripts/compose.sh                         # ordinary base-only dev stack
./scripts/compose.sh --with-restore-drill    # provisioned dev opt-in; protected sequence required
./scripts/compose.sh --prod                  # protected overlay required and fail-closed
butlers deploy                               # unchanged production deploy path
```

Explain that switching an opted-in dev project back to ordinary dev uses the normal `down --remove-orphans` lifecycle and does not authorize a direct protected start.

- [ ] **Step 2: Amend the active deployment-hardening delta and design**

Add a scenario stating that default dev omits the protected fragment and does not require its file-secret or endpoint checks; explicit dev opt-in and all production execution retain prepare/create/fence-before-start. Update the design statement that currently says both launchers always add the fragment.

- [ ] **Step 3: Extend document assertions and validate the changed spec**

Run:

```bash
uv run pytest tests/config/test_restore_drill_executor_compose.py -q
npx openspec validate restore-drill-recovery-truthfulness --strict
git diff --check
```

Expected: launcher tests and strict validation pass, and the diff contains no whitespace errors.

### Task 4: Review, commit, and publish the cohesive PR

**Files:**

- Verify: all files above

- [ ] **Step 1: Re-read the behavior matrix**

Verify default dev, explicit dev with/without a valid file, and production with/without a valid file against the acceptance criteria in `bu-kqnum.8.9`.

- [ ] **Step 2: Run the final focused gate once**

Run:

```bash
uv run pytest tests/config/test_restore_drill_executor_compose.py -q
bash -n scripts/compose.sh
uv run ruff check tests/config/test_restore_drill_executor_compose.py
uv run ruff format --check tests/config/test_restore_drill_executor_compose.py
npx openspec validate restore-drill-recovery-truthfulness --strict
git diff --check
```

Expected: every command exits 0.

- [ ] **Step 3: Commit and push the feature branch**

```bash
git add scripts/compose.sh tests/config/test_restore_drill_executor_compose.py \
  docs/operations/docker-deployment.md docs/operations/backup-restore.md scripts/README.md \
  openspec/changes/restore-drill-recovery-truthfulness/design.md \
  openspec/changes/restore-drill-recovery-truthfulness/specs/deployment-hardening/spec.md \
  docs/archive/superpowers/plans/2026-08-13-restore-drill-dev-opt-in.md
git commit -m "fix: make restore drill opt-in for dev"
git push -u origin agent/bu-kqnum.8.9
```

Then open a draft PR, obtain independent engineering review, satisfy the applicable PR-head gates,
mark it ready, and add it to the merge queue with `gh pr merge <n> --squash --auto`. The queue's
`merge_group` run supplies the terminal current-tree verification.
