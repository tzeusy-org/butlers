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

# The psql variable is populated inside psql from the file. It is quoted as a
# SQL literal by :'<name>', never supplied as a command-line argument or
# copied into an environment variable.
psql -X -v ON_ERROR_STOP=1 <<SQL
\set restore_drill_executor_password \`cat "$RESTORE_DRILL_EXECUTOR_PASSWORD_FILE"\`
ALTER ROLE restore_drill_executor LOGIN CREATEDB NOINHERIT NOSUPERUSER NOCREATEROLE NOREPLICATION;
SELECT format('ALTER ROLE restore_drill_executor PASSWORD %L', :'restore_drill_executor_password') \gexec
\unset restore_drill_executor_password
SQL
