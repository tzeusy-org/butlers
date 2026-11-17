#!/usr/bin/env bash
# Task 2.5 smoke and no-reasoning witness for the DARK public MCP server on dev (RFC 0039 P2:
# "smoke test from a test pod in mcp with a test keyring"). Not run in CI.
#
# Usage: bws run -- scripts/k8s/public-mcp-smoke.sh [dev]
#   MCP_PUBLIC_HOP_BEARER  the hop bearer whose digest the server holds (bws run injects it)
#   LIVE_CHECKS=0          skip scripts/k8s/public-mcp-live-checks.sh (default: run it)
#   ROUNDS=30              timed calls per tool for the entity_lookup/search p95
#
# What it does, in order:
#   1. Refuses unless the verifier keyring is empty (dark: no gateway key yet) and the labelled
#      probe cannot match a Service selector in `mcp`.
#   2. Installs a throwaway Ed25519 test key as the keyring (ConfigMap butlers-public-mcp-keyring),
#      restarts the server so the first tool call times the canary probe, runs the live checks.
#   3. Runs scripts/k8s/public-mcp-smoke.py from a probe pod in `mcp` labelled like filter-proxy.
#   4. No-reasoning witness: rows created in butlers during the smoke window in sessions,
#      route_inbox, scheduled_tasks, routing_log, goals, message_inbox and ingestion_events,
#      by source; any source naming mcp/public fails.
#   On exit, always: the original (empty) keyring is restored and the probe pod deleted. The
#   test key's seed lives only in this process and the probe pod, and dies with them.
# Prints verdicts, counts and timings only; never the bearer, the seed or tool results.
set -uo pipefail
ENV_NAME="${1:-dev}"
[ "$ENV_NAME" = dev ] || { echo "smoke: only dev (OD18)" >&2; exit 2; }
[ -n "${MCP_PUBLIC_HOP_BEARER:-}" ] || { echo "smoke: MCP_PUBLIC_HOP_BEARER is not set (run under bws run)" >&2; exit 2; }
NS="butlers-public-$ENV_NAME" PG_NS="postgres-$ENV_NAME" MCP_NS=mcp
DEPLOY=butlers-public-mcp CM=butlers-public-mcp-keyring PROBE=pubmcp-smoke
URL="http://butlers-public-mcp.$NS.svc.cluster.local:8080/mcp"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PY=(/usr/local/bin/python -I /app/scripts/runtime_clis/python_entrypoint.py python)
TMP="$(mktemp -d)"
chmod 700 "$TMP"
fails=0
restore=0

cleanup() {
  if [ "$restore" = 1 ]; then
    if kubectl -n "$NS" patch configmap "$CM" --type merge --patch-file "$TMP/restore.json" >/dev/null; then
      echo "restored the original keyring (the server re-reads it within about a minute)"
    else
      echo "RESTORE FAILED: put the keyring back with: kubectl -n $NS patch configmap $CM --type merge -p '{\"data\":{\"keyring.json\":\"\"}}'" >&2
    fi
  fi
  kubectl -n "$MCP_NS" delete pod "$PROBE" --ignore-not-found --wait=false >/dev/null 2>&1
  kubectl -n "$MCP_NS" delete networkpolicy butlers-public-mcp-probe-egress --ignore-not-found >/dev/null 2>&1
  rm -rf "$TMP"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

# 1. Guards and site facts (non-secret: image, audience, first allowed subject, keyring text).
kubectl -n "$NS" get deploy "$DEPLOY" -o json >"$TMP/deploy.json" || { echo "smoke ABORTED: no $DEPLOY in $NS" >&2; exit 3; }
kubectl -n "$NS" get configmap "$CM" -o json >"$TMP/cm.json" || { echo "smoke ABORTED: no ConfigMap $CM" >&2; exit 3; }
read -r IMAGE AUD SUB < <(python3 - "$TMP/deploy.json" <<'PY'
import json, sys
c = json.load(open(sys.argv[1]))["spec"]["template"]["spec"]["containers"][0]
env = {e["name"]: e.get("value", "") for e in c.get("env", [])}
print(c["image"], env["MCP_PUBLIC_AUDIENCE"], env["MCP_PUBLIC_ALLOWED_SUBS"].split(",")[0].strip())
PY
) || { echo "smoke ABORTED: cannot read the server's image and site values" >&2; exit 3; }
python3 - "$TMP/cm.json" "$TMP/restore.json" <<'PY' || { echo "smoke ABORTED: the keyring is not empty; past owner act 4 the smoke goes through the gateway Worker (bu-y66o00.7)" >&2; exit 3; }
import json, sys
current = json.load(open(sys.argv[1])).get("data", {}).get("keyring.json", "")
json.dump({"data": {"keyring.json": current}}, open(sys.argv[2], "w"))
sys.exit(1 if current.strip() else 0)
PY
kubectl -n "$MCP_NS" get svc -o json | python3 -c '
import json, sys
for svc in json.load(sys.stdin)["items"]:
    sel = svc["spec"].get("selector") or {}
    probe = {"app.kubernetes.io/instance": "mcp-butlers", "butlers.tze.how/public-mcp-probe": "true"}
    if sel and all(probe.get(a) == b for a, b in sel.items()):
        sys.exit("probe labels would match Service " + svc["metadata"]["name"])
' || { echo "smoke ABORTED: cannot prove the labelled probe is safe in $MCP_NS" >&2; exit 3; }

# 2. Probe pod, test key, restart.
overrides='{"spec":{"securityContext":{"runAsNonRoot":true,"runAsUser":10001,"seccompProfile":{"type":"RuntimeDefault"}},"containers":[{"name":"probe","image":"'"$IMAGE"'","command":["sleep","1200"],"securityContext":{"allowPrivilegeEscalation":false,"capabilities":{"drop":["ALL"]},"readOnlyRootFilesystem":true,"runAsNonRoot":true,"seccompProfile":{"type":"RuntimeDefault"}}}]}}'
kubectl -n "$MCP_NS" run "$PROBE" --image="$IMAGE" --restart=Never --labels=app.kubernetes.io/instance=mcp-butlers,butlers.tze.how/public-mcp-probe=true --overrides="$overrides" >/dev/null \
  && kubectl -n "$MCP_NS" wait --for=condition=Ready "pod/$PROBE" --timeout=120s >/dev/null \
  || { echo "smoke ABORTED: probe pod did not start" >&2; exit 3; }
CODE="$(cat "$HERE/public-mcp-smoke.py")"
SEED="$(openssl rand -hex 32)"
KID="public-mcp-smoke-$(date +%s)"
printf '{"seed_hex":"%s","kid":"%s"}\n' "$SEED" "$KID" \
  | kubectl -n "$MCP_NS" exec -i "$PROBE" -- "${VENV_PY[@]}" -c "$CODE" keyring >"$TMP/keyring.json" \
  || { echo "smoke ABORTED: could not render the test keyring" >&2; exit 3; }
python3 -c 'import json, sys; json.dump({"data": {"keyring.json": open(sys.argv[1]).read()}}, open(sys.argv[2], "w"))' \
  "$TMP/keyring.json" "$TMP/patch.json"
restore=1
kubectl -n "$NS" patch configmap "$CM" --type merge --patch-file "$TMP/patch.json" >/dev/null || { echo "smoke ABORTED: keyring patch refused" >&2; exit 3; }
echo "installed test key $KID as the verifier keyring"
kubectl -n "$NS" rollout restart "deploy/$DEPLOY" >/dev/null && kubectl -n "$NS" rollout status "deploy/$DEPLOY" --timeout=240s >/dev/null \
  || { echo "smoke ABORTED: the server did not come back Ready" >&2; exit 3; }
echo "server restarted on the test keyring"

if [ "${LIVE_CHECKS:-1}" = 1 ]; then
  "$HERE/public-mcp-live-checks.sh" "$ENV_NAME" || fails=$((fails + 1))
fi

# Namespace `mcp` is default-deny both ways (homelab); the probe needs its short-lived egress
# allow to the public pod's 8080. Applied here, after the live checks, which delete their copy.
kubectl apply -f "$HERE/public-mcp-probe-egress.yaml" >/dev/null || { echo "smoke ABORTED: cannot apply the probe egress allow in $MCP_NS" >&2; exit 3; }
sleep 5  # let the CNI program the allow before the first call

# 3 and 4. Smoke inside the witness window.
primary="$(kubectl -n "$PG_NS" get pod -l "cnpg.io/cluster=$PG_NS,cnpg.io/instanceRole=primary" -o name | head -1)"
psql_butlers() { kubectl -n "$PG_NS" exec -i "$primary" -c postgres -- psql -X -At -v ON_ERROR_STOP=1 -d butlers "$@"; }
T0="$(psql_butlers -c 'SELECT now()')" || { echo "smoke ABORTED: cannot read the witness clock" >&2; exit 3; }
SEED="$SEED" KID="$KID" AUD="$AUD" SUB="$SUB" URL="$URL" python3 -c '
import json, os
keys = {"seed_hex": "SEED", "kid": "KID", "aud": "AUD", "sub": "SUB", "url": "URL", "bearer": "MCP_PUBLIC_HOP_BEARER"}
cfg = {k: os.environ[v] for k, v in keys.items()}
cfg["rounds"] = int(os.environ.get("ROUNDS", "30"))
print(json.dumps(cfg))
' | kubectl -n "$MCP_NS" exec -i "$PROBE" -- "${VENV_PY[@]}" -c "$CODE" smoke || fails=$((fails + 1))
unset SEED

psql_butlers -v t0="$T0" >"$TMP/witness.txt" <<'SQL' || { echo "FAIL WITNESS query failed"; fails=$((fails + 1)); }
WITH t(sch, tbl, cls, ts) AS (
  SELECT table_schema::text, table_name::text,
         CASE table_name WHEN 'sessions' THEN 'trigger_source' WHEN 'scheduled_tasks' THEN 'source' END,
         CASE table_name WHEN 'sessions' THEN 'started_at' WHEN 'route_inbox' THEN 'received_at' ELSE 'created_at' END
  FROM information_schema.tables
  WHERE table_type = 'BASE TABLE' AND table_schema <> 'dashboard_auth'
    AND table_name IN ('sessions', 'route_inbox', 'scheduled_tasks')
  UNION ALL SELECT * FROM (VALUES
    ('switchboard', 'routing_log', 'source_channel', 'created_at'),
    ('switchboard', 'goals', 'source_kind', 'created_at'),
    ('switchboard', 'message_inbox', NULL, 'received_at'),
    ('public', 'ingestion_events', 'source_channel', 'received_at')) AS v
)
SELECT format('SELECT %L, %s, count(*) FROM %I.%I WHERE %I >= %L::timestamptz GROUP BY 2',
              sch || '.' || tbl, COALESCE(quote_ident(cls) || '::text', '''-'''), sch, tbl, ts, :'t0')
FROM t ORDER BY sch, tbl
\gexec
SQL
# psql interpolates :'t0' only in script input, never in -c.
window="$(echo "SELECT round(extract(epoch FROM now() - :'t0'::timestamptz))" | psql_butlers -v t0="$T0" 2>/dev/null || echo '?')"
echo "WITNESS rows created in butlers during the ${window}s smoke window (table|source|count):"
sed 's/^/  /' "$TMP/witness.txt"
if awk -F'|' 'tolower($2) ~ /mcp|public/ { found = 1 } END { exit !found }' "$TMP/witness.txt"; then
  echo "FAIL WITNESS a row in the window names an mcp/public source"
  fails=$((fails + 1))
else
  echo "ok   WITNESS no session, route, goal, schedule or ingestion row names an mcp/public source"
fi

[ "$fails" -eq 0 ] && echo "public MCP smoke passed" || echo "public MCP smoke FAILED ($fails)"
exit "$([ "$fails" -eq 0 ] && echo 0 || echo 1)"
