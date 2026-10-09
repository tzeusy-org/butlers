"""Closed subprocess evidence for owning disposable restore fixtures.

Captured SQL, command arguments, row data and stderr text never leave this
classifier. Fixed stages/codes describe only the real command that ran; an
absent category remains unknown, never evidence of a successful restore.
"""

from __future__ import annotations

import json
import re
import subprocess


def emit_restore_diagnostic(
    result: subprocess.CompletedProcess[str], *, stage: str, artifact: str | None = None
) -> None:
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
        sql_error_seen=bool(re.search(r"(?m)^\s*(?:psql:(?:<stdin>|-):\d+:\s*)?ERROR:", output)),
        fatal_seen=bool(re.search(r"(?m)^\s*(?:psql:(?:<stdin>|-):\d+:\s*)?FATAL:", output)),
        missing_psql=bool(re.search(r"(?m)psql: (?:command )?not found\s*$", output)),
        missing_bash=bool(re.search(r"(?m)bash: (?:command )?not found\s*$", output)),
    )
    states = {
        code: bool(
            re.search(
                rf"(?m)^\s*(?:psql:(?:<stdin>|-):\d+:\s*)?(?:ERROR|FATAL):\s*{code}(?:\s|$)",
                output,
            )
        )
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
    # Only source-generated stage markers classify input offsets. Neither line
    # contents nor row counts/identifiers are returned. A reached later audit
    # uses a different input stream, so its offsets cannot be attributed here.
    errors = list(
        re.finditer(
            r"(?m)^\s*psql:(?:<stdin>|-):(\d+):\s*(?:ERROR|FATAL):\s*([0-9A-Z]{5})(?:\s|$)",
            output,
        )
    )
    stage_codes = {
        name: {code: False for code in states}
        for name in ("ordinary_dump", "cost_claim_import", "native_copy_import")
    }
    certificate_codes = {
        name: {code: False for code in states}
        for name in ("posture", "filtered_read", "input_read", "point_check")
    }
    # These literal markers come from the checked-in certificate script, not
    # row content or command arguments. Only its stderr stream can position a
    # later code-only psql error; never borrow dump-stream line offsets here.
    certificate_stage = None
    if flags["definer_audit_passed"]:
        for line in (result.stderr or "").splitlines():
            marker = re.fullmatch(r"RETENTION_NATIVE_CERT_STAGE=([a-z_]+)", line)
            if line.startswith("RETENTION_NATIVE_CERT_STAGE="):
                certificate_stage = marker[1] if marker and marker[1] in certificate_codes else None
                continue
            error = re.fullmatch(
                r"\s*(?:psql:(?:<stdin>|-):\d+:\s*)?(?:ERROR|FATAL):\s*([0-9A-Z]{5})(?:\s.*)?",
                line,
            )
            if error and certificate_stage is not None and error[1] in states:
                certificate_codes[certificate_stage][error[1]] = True
    if artifact is not None and not flags["definer_audit_started"]:
        lines = artifact.splitlines()
        cost_start = next(
            (
                i
                for i, line in enumerate(lines, 1)
                if line.startswith("CREATE TEMP TABLE butlers_cost_claim_restore_rows")
            ),
            None,
        )
        native_start = next(
            (
                i
                for i, line in enumerate(lines, 1)
                if line == "-- Butlers scoped OwnTracks copy history"
            ),
            None,
        )
        for error in errors:
            offset, code = int(error[1]), error[2]
            if not 1 <= offset <= len(lines) or code not in states:
                continue
            name = (
                "native_copy_import"
                if native_start is not None and offset >= native_start
                else "cost_claim_import"
                if cost_start is not None and offset >= cost_start
                else "ordinary_dump"
            )
            stage_codes[name][code] = True
    print(
        "RESTORE_COMMAND_DIAGNOSTIC "
        + json.dumps(
            {
                "stage": stage,
                "returncode": int(result.returncode),
                "flags": flags,
                "sqlstates": states,
                "source_stage_codes": stage_codes,
                "certificate_stage_codes": certificate_codes,
            },
            sort_keys=True,
        )
    )
