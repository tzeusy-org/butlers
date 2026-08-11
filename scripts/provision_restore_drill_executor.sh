#!/usr/bin/env bash
# Enable the pre-reserved restore-drill role through its private secret file.
#
# Run only as the privileged PostgreSQL bootstrap operator after init-db.sql
# has reserved restore_drill_executor. Connection selection is intentionally
# left to the operator's existing psql authentication configuration; this
# script never reads shared application credentials.

set -euo pipefail

: "${RESTORE_DRILL_EXECUTOR_PASSWORD_FILE:?Set RESTORE_DRILL_EXECUTOR_PASSWORD_FILE to the private secret file}"

if [[ ! -r "$RESTORE_DRILL_EXECUTOR_PASSWORD_FILE" ]]; then
    printf '%s\n' 'restore-drill executor password file is not readable' >&2
    exit 2
fi

if [[ ! -s "$RESTORE_DRILL_EXECUTOR_PASSWORD_FILE" ]]; then
    printf '%s\n' 'restore-drill executor password file is empty' >&2
    exit 2
fi

# psql meta-commands parse newlines, so a line terminator can turn a password
# file into extra input. Match the executor's exact file contract: allow one
# terminal LF, reject every embedded/multiple LF and all CR before invoking a
# database client. The raw bytes remain base64 literal data; UTF-8/NUL are
# checked by PostgreSQL inside the transaction below so a decode failure can
# never leave the role partially enabled.
#
# Base64 is restricted to psql-safe token characters. Feed that encoded token
# through stdin and let psql quote it with :'<name>' before decoding, rather
# than expanding the raw secret into a psql heredoc or process argument.
restore_drill_executor_password_b64="$(
    python3 - "$RESTORE_DRILL_EXECUTOR_PASSWORD_FILE" <<'PY'
import base64
import pathlib
import sys

raw = pathlib.Path(sys.argv[1]).read_bytes()
if raw.endswith(b"\n"):
    raw = raw[:-1]
if not raw or b"\n" in raw or b"\r" in raw:
    print("restore-drill executor password file must contain exactly one single line", file=sys.stderr)
    raise SystemExit(2)
sys.stdout.write(base64.b64encode(raw).decode("ascii"))
PY
)"
trap 'unset restore_drill_executor_password_b64' EXIT

{
    printf '\\set restore_drill_executor_password_b64 %s\n' "$restore_drill_executor_password_b64"
    printf '%s\n' 'BEGIN;'
    printf '%s\n' 'ALTER ROLE restore_drill_executor LOGIN CREATEDB NOINHERIT NOSUPERUSER NOCREATEROLE NOREPLICATION;'
    printf '%s\n' "SELECT format("
    printf '%s\n' "    'ALTER ROLE restore_drill_executor PASSWORD %L',"
    printf '%s\n' "    convert_from(decode(:'restore_drill_executor_password_b64', 'base64'), 'UTF8')"
    printf '%s\n' ') \gexec'
    printf '%s\n' 'COMMIT;'
    printf '%s\n' '\unset restore_drill_executor_password_b64'
} | psql -X -v ON_ERROR_STOP=1
