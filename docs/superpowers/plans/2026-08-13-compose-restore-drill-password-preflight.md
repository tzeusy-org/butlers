# Restore-Drill Password Preflight Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `scripts/compose.sh` reject an absent or unusable restore-drill password-file setting before it can invoke Docker Compose lifecycle commands.

**Architecture:** The launcher will perform a small shell metadata check directly after it sources the selected environment file. The check accepts only a configured, readable, non-empty regular file and emits generic remediation text without opening or disclosing the file path or contents. Focused tests execute the extracted bootstrap boundary and prove both rejection and the normal file-backed path.

**Tech Stack:** Bash, pytest, Ruff.

## Global Constraints

- Do not create, source, read, print, or substitute the restore-drill password file or its contents.
- Do not change the protected Compose overlay, service profiles, firewall sequence, or secret ownership model.
- The check must run after `.env.<mode>` is sourced and before endpoint resolution, image builds, `down`, `create`, or `up`.
- Error text must identify the configuration key and required file properties without exposing the configured path.
- Preserve the ordinary launcher path when a valid non-empty regular file is configured.
- Update the repository `AGENTS.md` Notes to self with the Compose interpolation failure mode discovered here.

---

### Task 1: Add and verify the fail-closed launcher preflight

**Files:**

- Modify: `scripts/compose.sh:104`, immediately after `set +a`
- Modify: `tests/config/test_restore_drill_executor_compose.py`, adjacent to the launcher boundary tests
- Modify: `AGENTS.md`, under `# Notes to self`

**Interfaces:**

- Consumes: `RESTORE_DRILL_EXECUTOR_PASSWORD_FILE` from the selected environment or caller environment.
- Produces: exit status 1 and one generic `stderr` configuration error when the setting is absent, not a regular file, unreadable, or empty; exit status 0 from the bootstrap boundary for a valid file.

- [x] **Step 1: Write the failing bootstrap regression tests**

```python
@pytest.mark.parametrize("kind", ("unset", "missing", "directory", "empty", "unreadable"))
def test_restore_drill_launcher_requires_private_password_file_before_lifecycle(
    tmp_path: Path, kind: str
) -> None:
    environment_file = tmp_path / ".env.dev"
    environment_file.write_text(
        "POSTGRES_HOST=postgres.example.test\n"
        "POSTGRES_PORT=5432\n"
        "RESTORE_DRILL_EXECUTOR_FIREWALL_DB_HOST=10.23.4.5\n",
        encoding="utf-8",
    )
    launcher = _COMPOSE_LAUNCHER.read_text(encoding="utf-8")
    start = launcher.index("# ── Load environment-specific database config")
    end = launcher.index("# ── Mode-dependent configuration", start)
    bootstrap_boundary = launcher[start:end]
    env = {**os.environ, "PROJECT_DIR": str(tmp_path), "BUTLERS_MODE": "dev"}
    env.pop("RESTORE_DRILL_EXECUTOR_PASSWORD_FILE", None)
    configured_path = tmp_path / "password-file"
    if kind == "missing":
        env["RESTORE_DRILL_EXECUTOR_PASSWORD_FILE"] = str(configured_path)
    elif kind == "directory":
        configured_path.mkdir()
        env["RESTORE_DRILL_EXECUTOR_PASSWORD_FILE"] = str(configured_path)
    elif kind == "empty":
        configured_path.touch()
        env["RESTORE_DRILL_EXECUTOR_PASSWORD_FILE"] = str(configured_path)
    elif kind == "unreadable":
        if os.geteuid() == 0:
            pytest.skip("root can read permissionless files, so Bash -r cannot be observed")
        configured_path.write_text("test-only-password-marker\\n", encoding="utf-8")
        configured_path.chmod(0o000)
        env["RESTORE_DRILL_EXECUTOR_PASSWORD_FILE"] = str(configured_path)

    completed = subprocess.run(
        ["bash", "-c", "set -euo pipefail\\n" + bootstrap_boundary],
        check=False,
        capture_output=True,
        env=env,
        text=True,
    )

    assert completed.returncode == 1
    assert "RESTORE_DRILL_EXECUTOR_PASSWORD_FILE" in completed.stderr
    assert str(configured_path) not in completed.stdout + completed.stderr
    assert launcher.index("# Restore-drill executor password-file preflight") < launcher.index(
        '"${CMD[@]}" down --remove-orphans'
    )


def test_restore_drill_launcher_accepts_valid_private_password_file(tmp_path: Path) -> None:
    password_file = tmp_path / "password-file"
    password_marker = "test-only-password-marker"
    password_file.write_text(password_marker + "\\n", encoding="utf-8")
    environment_file = tmp_path / ".env.dev"
    environment_file.write_text(
        "POSTGRES_HOST=postgres.example.test\\n"
        "POSTGRES_PORT=5432\\n"
        "RESTORE_DRILL_EXECUTOR_FIREWALL_DB_HOST=10.23.4.5\\n",
        encoding="utf-8",
    )
    launcher = _COMPOSE_LAUNCHER.read_text(encoding="utf-8")
    start = launcher.index("# ── Load environment-specific database config")
    end = launcher.index("# ── Mode-dependent configuration", start)
    bootstrap_boundary = launcher[start:end]
    completed = subprocess.run(
        ["bash", "-c", "set -euo pipefail\\n" + bootstrap_boundary],
        check=False,
        capture_output=True,
        env={
            **os.environ,
            "PROJECT_DIR": str(tmp_path),
            "BUTLERS_MODE": "dev",
            "RESTORE_DRILL_EXECUTOR_PASSWORD_FILE": str(password_file),
        },
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert str(password_file) not in completed.stdout + completed.stderr
    assert password_marker not in completed.stdout + completed.stderr
```

- [x] **Step 2: Run the focused test before the implementation**

Run: `uv run pytest tests/config/test_restore_drill_executor_compose.py -q --tb=short`

Expected: FAIL because the bootstrap boundary currently allows missing, non-file, and empty settings to pass through to later Compose interpolation.

- [x] **Step 3: Add the minimal launcher-owned validation**

```bash
# Restore-drill executor password-file preflight: Compose interpolates this
# protected secret even for lifecycle commands, so fail before Docker can stop
# the existing stack.  Metadata checks never read or disclose the secret.
if [[ -z "${RESTORE_DRILL_EXECUTOR_PASSWORD_FILE:-}" ]]; then
  echo "ERROR: RESTORE_DRILL_EXECUTOR_PASSWORD_FILE must name the private restore-drill executor password file." >&2
  exit 1
fi
if [[ ! -f "$RESTORE_DRILL_EXECUTOR_PASSWORD_FILE" || ! -r "$RESTORE_DRILL_EXECUTOR_PASSWORD_FILE" || ! -s "$RESTORE_DRILL_EXECUTOR_PASSWORD_FILE" ]]; then
  echo "ERROR: RESTORE_DRILL_EXECUTOR_PASSWORD_FILE must name a readable, non-empty regular file." >&2
  exit 1
fi
```

Add one concise `AGENTS.md` note stating that the protected overlay must preflight this setting before `down`, because Compose interpolation can otherwise report an arbitrary missing service.

- [x] **Step 4: Run the focused regression and syntax/lint gates**

Run:

```bash
uv run pytest tests/config/test_restore_drill_executor_compose.py -q --tb=short
bash -n scripts/compose.sh
uv run ruff check tests/config/test_restore_drill_executor_compose.py
uv run ruff format --check tests/config/test_restore_drill_executor_compose.py
```

Expected: all commands exit 0.

- [x] **Step 5: Commit the coherent fix**

```bash
git add AGENTS.md \
  docs/superpowers/plans/2026-08-13-compose-restore-drill-password-preflight.md \
  scripts/compose.sh \
  tests/config/test_restore_drill_executor_compose.py
git commit -m "fix: preflight restore-drill password file"
```
