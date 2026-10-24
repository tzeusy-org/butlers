#!/usr/bin/env bash
# UNEXECUTED: requires separately reviewed ROOT allocation and DRAFT carrier.
set -euo pipefail
umask 077
expected=4cbf30c8e7290e7c86fd38ce9502820b70b88f5a
[[ "${GITHUB_RUN_ATTEMPT:?}" == 1 ]]
[[ "${DIAG_SUBJECT_HEAD:?}" == "$expected" ]]
[[ "${DIAG_OFFSET:?}" == +45d ]]
[[ "${GITHUB_SHA:?}" =~ ^[0-9a-f]{40}$ ]]
[[ "${GITHUB_RUN_ID:?}" =~ ^[0-9]+$ ]]
[[ "$(git rev-parse HEAD)" == "$expected" ]]
[[ "$(git rev-parse HEAD^{tree})" == 8f91eb7ab6d627152d4abafd78bc7af52890a23e ]]
test -z "$(git status --porcelain)"
test -d .venv && test ! -L .venv
observer_dir=$(realpath "${DIAG_OBSERVER_DIR:?}")
case "$observer_dir" in "$PWD"/*) exit 71;; esac
out="${RUNNER_TEMP:?}/pg-clock12-diagnostic-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"
mkdir -m 700 "$out"  # exclusive mkdir refuses replay into prior output
mkdir -m 700 "$out/trace" "$out/published"
raw="$out/raw-private.log"
: > "$raw"
chmod 600 "$raw"
metadata_failures=0
cleanup() {
  incoming_exit=$?
  trap - EXIT
  set +e
  # Early qualification errors preserve their own exit. After the owner, NO
  # metadata/cleanup error is allowed to replace its captured actual exit.
  final_exit=${subject_exit:-$incoming_exit}
  rm -f -- "$raw" "$out/trace"/owned-*.id 2>/dev/null
  private_remove_exit=$?
  timeout --signal=TERM --kill-after=2s 5 \
   .venv/bin/python - "$out" >/dev/null 2>&1 <<'PY_CLEANUP'
import json,pathlib,sys
out=pathlib.Path(sys.argv[1])
record={'scope':'DIAGNOSTIC_ONLY','observational_pass':False,
        'raw_private_absent':not (out/'raw-private.log').exists(),
        'private_ownership_files_absent':not any((out/'trace').glob('owned-*.id')),
        'container_absence_requires_separate_receipt':True}
f=out/'published'/'private-cleanup.json'
f.write_text(json.dumps(record)+'\n');f.chmod(0o600)
PY_CLEANUP
  cleanup_metadata_exit=$?
  printf '{"scope":"DIAGNOSTIC_ONLY","observational_pass":false,"final_exit":%d,"subject_exit_captured":%s,"metadata_failure_bits":%d,"validator_exit":%d,"private_remove_exit":%d,"cleanup_metadata_exit":%d}\n' \
    "$final_exit" "${subject_exit_captured:-false}" "$metadata_failures" "${validator_exit:-255}" \
    "$private_remove_exit" "$cleanup_metadata_exit" 2>/dev/null > "$out/published/post-owner-status.json"
  # Even failure to persist this last status cannot change the owner outcome.
  exit "$final_exit"
}
trap cleanup EXIT
# No Docker permission probe or external database/credential discovery.
# Shared shifted pgvector container remains owned by the unchanged root fixture.
LIBFAKETIME_SO=$(dpkg -L libfaketime | awk '/\/libfaketime\.so\.1$/ {print; exit}')
test -n "$LIBFAKETIME_SO"
.venv/bin/python - <<'PY'
import pathlib,sys,butlers
assert sys.version_info[:3] == (3,12,15)
assert pathlib.Path(butlers.__file__).resolve().is_relative_to(pathlib.Path.cwd()/'src')
PY
set +e
timeout --signal=SIGABRT --kill-after=30s 900 \
 env LD_PRELOAD="$LIBFAKETIME_SO" FAKETIME=+45d PYTHONFAULTHANDLER=1 \
 TESTCONTAINERS_RYUK_DISABLED=true PG_CLOCK_DIAG_OUTPUT="$out/trace" \
 PYTHONPATH="$observer_dir:$PWD${PYTHONPATH:+:$PYTHONPATH}" \
 uv run --no-sync pytest tests/integration/test_filtered_event_buffer_jsonb_roundtrip.py \
 -p pg_clock_phase_observer -v -rf --tb=no --ignore=tests/e2e \
 -m "not bench and not perf and not nightly and not faketime_fragile" \
 -n auto --dist loadfile --timeout=300 --timeout-method=thread \
 > "$raw" 2>&1
subject_exit=$?
subject_exit_captured=true
# Remain under set +e for ALL post-owner work. This small fixed-schema carrier
# precedes validation so an observer failure need not erase the owner's status.
printf '{"scope":"DIAGNOSTIC_ONLY","observational_pass":false,"subject_exit":%d}\n' \
 "$subject_exit" 2>/dev/null > "$out/published/subject-exit.json" || metadata_failures=$((metadata_failures | 1))
timeout --signal=TERM --kill-after=5s 20 \
 .venv/bin/python "$observer_dir/validate_receipt.py" "$out/trace" "$out/published/observation" "$subject_exit" \
 >/dev/null 2>&1
validator_exit=$?
# Input strings admitted by closed regexes before owner; no JSON escaping of
# arbitrary values is needed. A failed write is recorded independently.
printf '{"scope":"DIAGNOSTIC_ONLY","observational_pass":false,"subject":"%s","diagnostic_ref_sha":"%s","run":"%s","attempt":1,"offset":"+45d","validator_exit":%d,"metadata_failure_bits":%d}\n' \
 "$expected" "$GITHUB_SHA" "$GITHUB_RUN_ID" "$validator_exit" "$metadata_failures" \
 2>/dev/null > "$out/published/binding.json" || metadata_failures=$((metadata_failures | 2))
chmod 600 "$out/published/subject-exit.json" "$out/published/binding.json" 2>/dev/null || metadata_failures=$((metadata_failures | 4))
# No raw stderr, tracebacks, SQL, data or JUnit is published. Container absence
# requires the distinct qualified audit; finally or VM teardown is no proof.
exit "$subject_exit"
