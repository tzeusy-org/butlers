"""Closed subprocess evidence for owning disposable restore fixtures.

Captured SQL, command arguments, row data and stderr text never leave this
classifier. Fixed stages/codes describe only the real command that ran; an
absent category remains unknown, never evidence of a successful restore.
"""

from __future__ import annotations

import json
import re
import subprocess


def emit_restore_diagnostic(result: subprocess.CompletedProcess[str], *, stage: str) -> None:
    if stage not in {"raw_drill", "certified_restore"}:
        raise ValueError("restore diagnostic stage differs")
    output = (result.stdout or "") + "\n" + (result.stderr or "")
    literal_flags = {
        "psql_restore_failed": "[restore] ERROR: psql restore failed",
        "definer_audit_started": "[restore] Auditing SECURITY DEFINER ownership",
        "definer_audit_passed": "no SECURITY DEFINER function in 'public' fell to",
        "native_posture_refused": "Native copy restore posture is unavailable",
        "native_cohort_refused": "native copy history did not restore exactly",
        "native_certificate_refused": "native copy history restoration is not certified",
        "native_input_refused": "Native copy restoration input differs",
        "native_row_refused": "Native copy restoration row differs",
        "native_import_cohort_refused": "Native copy restoration cohort differs",
        "certified_done": "[restore] done",
        "transaction_aborted": "current transaction is aborted",
        "role_membership_refused": "must be able to SET ROLE",
    }
    flags = {name: text in output for name, text in literal_flags.items()}
    flags.update(
        sql_error_seen=bool(re.search(r"(?m)^\s*ERROR:", output)),
        fatal_seen=bool(re.search(r"(?m)^\s*FATAL:", output)),
        missing_psql=bool(re.search(r"(?m)psql: (?:command )?not found\s*$", output)),
        missing_bash=bool(re.search(r"(?m)bash: (?:command )?not found\s*$", output)),
    )
    states = {
        code: bool(re.search(rf"(?m)^\s*(?:ERROR|FATAL):\s*{code}(?:\s|$)", output))
        for code in (
            "42501",
            "42P01",
            "42703",
            "42601",
            "22P02",
            "23503",
            "P0001",
            "25P02",
            "23514",
            "23505",
            "42883",
            "42P17",
            "42704",
            "2BP01",
            "55000",
            "42804",
            "22023",
            "XX000",
            "42P07",
        )
    }
    print(
        "RESTORE_COMMAND_DIAGNOSTIC "
        + json.dumps(
            {
                "stage": stage,
                "returncode": int(result.returncode),
                "flags": flags,
                "sqlstates": states,
            },
            sort_keys=True,
        )
    )
