#!/usr/bin/env bash
# Live checks for the public MCP chart on dev. NOT run in CI and not by the chart PR: they need
# the server image deployed (go-live, PUBLIC_MCP_CHART_dev=on). Read-only except two short-lived
# probe pods in `mcp` and one in butlers-public-dev, removed on exit. Prints names and verdicts
# only, never a Secret value (the password is used inside the server pod's own environment).
#
# Usage: scripts/k8s/public-mcp-live-checks.sh [env]        (dev only; RFC 0039 OD18)
#   POLICY    D8 item 6: every NetworkPolicy selecting pods in the namespace is the chart's
#   POSTGRES  REQ-public-mcp-projection-014: postgres-<env> carries no policy that selects the
#             instance pods without admitting the public pod, and none admits the namespace
#             beyond the instance pods on 5432
#   REACH     task 2.5 network half: the labelled mcp pod reaches 8080, an unlabelled one does
#             not, the server pod reaches 5432 and nothing on the internet
#   ISOLATION task 1.8 public half: mcp_public_ro logs in to butlers_public, is refused by
#             pg_hba on butlers/postgres/template1
# Exit 0 only when every check passes.
set -uo pipefail
ENV_NAME="${1:-dev}"
[ "$ENV_NAME" = dev ] || { echo "live checks: only dev (OD18)" >&2; exit 2; }
NS="butlers-public-$ENV_NAME" PG_NS="postgres-$ENV_NAME" MCP_NS=mcp
SVC="butlers-public-mcp.$NS.svc.cluster.local"
CHART_POLICIES="butlers-public-mcp-default-deny butlers-public-mcp-allow-mcp-ingress butlers-public-mcp-allow-metrics-ingress butlers-public-mcp-allow-postgres-egress butlers-public-mcp-allow-dns-egress"
IMAGE="$(kubectl -n "$NS" get deploy butlers-public-mcp -o jsonpath='{.spec.template.spec.containers[0].image}')"
fails=0
ok() { echo "ok   $*"; }
bad() { echo "FAIL $*"; fails=$((fails + 1)); }
cleanup() { kubectl -n "$MCP_NS" delete pod pubmcp-probe-labelled pubmcp-probe-plain --ignore-not-found --wait=false >/dev/null 2>&1; }
trap cleanup EXIT

# POLICY
actual="$(kubectl -n "$NS" get networkpolicy -o name | sed 's|.*/||' | sort | tr '\n' ' ')"
expected="$(printf '%s\n' $CHART_POLICIES | sort | tr '\n' ' ')"
[ "$actual" = "$expected" ] && ok "POLICY namespace holds exactly the chart's policies" || bad "POLICY unexpected set: $actual"

# POSTGRES: list policy names in postgres-<env> with a podSelector that could select instance pods.
pg="$(kubectl -n "$PG_NS" get networkpolicy -o json | python3 -c '
import json, sys
for p in json.load(sys.stdin)["items"]:
    sel = p["spec"].get("podSelector", {})
    print(p["metadata"]["name"], "selector=" + json.dumps(sel, sort_keys=True))
')"
[ -z "$pg" ] && ok "POSTGRES no policy selects pods in $PG_NS (the public pod needs no ingress allow)" || bad "POSTGRES policies present, review whether the public pod's 5432 allow is needed: $pg"

# REACH
probe() { # name labels
  kubectl -n "$MCP_NS" run "$1" --image="$IMAGE" --restart=Never ${2:+--labels="$2"} --command -- sleep 120 >/dev/null 2>&1
  kubectl -n "$MCP_NS" wait --for=condition=Ready "pod/$1" --timeout=60s >/dev/null 2>&1
}
tcp() { # namespace pod host port
  kubectl -n "$1" exec "$2" -- python3 -c "import socket,sys; s=socket.socket(); s.settimeout(4); s.connect((sys.argv[1], int(sys.argv[2])))" "$3" "$4" >/dev/null 2>&1
}
probe pubmcp-probe-labelled "app.kubernetes.io/instance=butlers"
probe pubmcp-probe-plain ""
tcp "$MCP_NS" pubmcp-probe-labelled "$SVC" 8080 && ok "REACH labelled mcp pod reaches 8080" || bad "REACH labelled mcp pod refused"
tcp "$MCP_NS" pubmcp-probe-plain "$SVC" 8080 && bad "REACH unlabelled mcp pod reached 8080" || ok "REACH unlabelled mcp pod refused"
server="$(kubectl -n "$NS" get pod -l app.kubernetes.io/component=butlers-public-mcp -o name | head -1)"
tcp "$NS" "${server#pod/}" "postgres-$ENV_NAME-rw.$PG_NS.svc.cluster.local" 5432 && ok "REACH server pod reaches postgres 5432" || bad "REACH server pod cannot reach postgres 5432"
tcp "$NS" "${server#pod/}" 1.1.1.1 443 && bad "REACH server pod reached the internet" || ok "REACH server pod has no internet route"

# ISOLATION: the password is read from the pod's own environment and never printed.
iso='
import asyncio, os
import asyncpg
async def main():
    for db, want in (("butlers_public", True), ("butlers", False), ("postgres", False), ("template1", False)):
        try:
            c = await asyncpg.connect(host=os.environ["MCP_PUBLIC_DB_HOST"], port=int(os.environ["MCP_PUBLIC_DB_PORT"]),
                user=os.environ["MCP_PUBLIC_DB_USER"], password=os.environ["MCP_PUBLIC_DB_PASSWORD"], database=db, ssl="require", timeout=8)
            await c.close(); got = True
        except Exception as exc:
            got = False
        word = "allowed" if got else "refused"
        print(("ok   " if got == want else "FAIL ") + "ISOLATION login to " + db + ": " + word)
asyncio.run(main())
'
out="$(kubectl -n "$NS" exec "${server#pod/}" -- python3 -c "$iso" 2>&1)"
echo "$out" | grep -E '^(ok|FAIL) ' || bad "ISOLATION probe produced no verdict"
fails=$((fails + $(echo "$out" | grep -c '^FAIL ')))
[ "$fails" -eq 0 ] && echo "live checks passed" || echo "live checks FAILED ($fails)"
exit "$([ "$fails" -eq 0 ] && echo 0 || echo 1)"
