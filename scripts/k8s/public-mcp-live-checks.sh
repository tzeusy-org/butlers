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
# Exit 0 only when every check passes; INCONCLUSIVE counts as not passed. Exit 3: aborted before
# creating any probe.
set -uo pipefail
ENV_NAME="${1:-dev}"
[ "$ENV_NAME" = dev ] || { echo "live checks: only dev (OD18)" >&2; exit 2; }
NS="butlers-public-$ENV_NAME" PG_NS="postgres-$ENV_NAME" MCP_NS=mcp
SVC="butlers-public-mcp.$NS.svc.cluster.local"
CHART_POLICIES="butlers-public-mcp-default-deny butlers-public-mcp-allow-mcp-ingress butlers-public-mcp-allow-metrics-ingress butlers-public-mcp-allow-postgres-egress butlers-public-mcp-allow-dns-egress"
IMAGE="$(kubectl -n "$NS" get deploy butlers-public-mcp -o jsonpath='{.spec.template.spec.containers[0].image}')"
fails=0
ok() { echo "ok   $*"; }
inconclusive() { echo "INCONCLUSIVE $*"; fails=$((fails + 1)); }
bad() { echo "FAIL $*"; fails=$((fails + 1)); }
cleanup() { kubectl -n "$MCP_NS" delete pod pubmcp-probe-labelled pubmcp-probe-plain --ignore-not-found --wait=false >/dev/null 2>&1; }
trap cleanup EXIT

server_pod="$(kubectl -n "$NS" get pod -l app.kubernetes.io/component=butlers-public-mcp -o name | head -1)"; server_pod="${server_pod#pod/}"
# POLICY
actual="$(kubectl -n "$NS" get networkpolicy -o name | sed 's|.*/||' | sort | tr '\n' ' ')"
expected="$(printf '%s\n' $CHART_POLICIES | sort | tr '\n' ' ')"
[ "$actual" = "$expected" ] && ok "POLICY namespace holds exactly the chart's policies" || bad "POLICY unexpected set: $actual"

# POLICY, cluster-wide objects (D8 item 6): report those that exist; "not visible" is never a pass.
for kind in ciliumclusterwidenetworkpolicies.cilium.io globalnetworkpolicies.crd.projectcalico.org \
  clusterpolicies.kyverno.io; do
  if ! kubectl get crd "$kind" >/dev/null 2>&1; then
    err="$(kubectl get crd "$kind" 2>&1 >/dev/null)"
    case "$err" in
      *orbidden*) inconclusive "POLICY cluster-wide $kind not visible (access forbidden)" ;;
      *) ok "POLICY cluster-wide $kind: CRD absent" ;;
    esac
    continue
  fi
  names="$(kubectl get "$kind" -o name 2>&1)" || { inconclusive "POLICY cluster-wide $kind not visible: list failed"; continue; }
  [ -z "$names" ] && ok "POLICY cluster-wide $kind: none" \
    || bad "POLICY cluster-wide $kind exists, review whether it selects pods in $NS: $(echo "$names" | tr '\n' ' ')"
done
# Gatekeeper constraints are one CRD per template; list the constraint templates' kinds.
if kubectl get crd constrainttemplates.templates.gatekeeper.sh >/dev/null 2>&1; then
  kinds="$(kubectl get constrainttemplates -o jsonpath='{range .items[*]}{.spec.crd.spec.names.kind}{"\n"}{end}' 2>&1)" \
    || { inconclusive "POLICY gatekeeper constraint kinds not visible"; kinds=""; }
  for k in $kinds; do
    found="$(kubectl get "$k" -o name 2>&1)" || { inconclusive "POLICY gatekeeper $k not visible"; continue; }
    [ -z "$found" ] && ok "POLICY gatekeeper $k: none" || bad "POLICY gatekeeper constraint $k exists, review: $(echo "$found" | tr '\n' ' ')"
  done
else
  ok "POLICY gatekeeper: CRD absent"
fi

# POSTGRES: list policy names in postgres-<env> with a podSelector that could select instance pods.
pg="$(kubectl -n "$PG_NS" get networkpolicy -o json | python3 -c '
import json, sys
for p in json.load(sys.stdin)["items"]:
    sel = p["spec"].get("podSelector", {})
    print(p["metadata"]["name"], "selector=" + json.dumps(sel, sort_keys=True))
')"
[ -z "$pg" ] && ok "POSTGRES no policy selects pods in $PG_NS (the public pod needs no ingress allow)" || bad "POSTGRES policies present, review whether the public pod's 5432 allow is needed: $pg"

# CANARIES/FILES: the server's required files are mounted (names only; contents never read).
for f in bearer/digest db/password canaries/canaries.json keyring/keyring.json switches/disabled_domains; do
  kubectl -n "$NS" exec "${server_pod}" -- test -s "/etc/butlers-public-mcp/$f" >/dev/null 2>&1 \
    && ok "FILES $f present and non-empty" || bad "FILES $f missing or empty (keyring is empty until owner act 4)"
done

# CANARIES: the list must be non-empty (the host refuses on []). Counts only; no value is printed.
canary_count="$(kubectl -n "$NS" exec "${server_pod}" -- python3 -c '
import json
v = json.load(open("/etc/butlers-public-mcp/canaries/canaries.json"))
print(len(v) if isinstance(v, list) and all(isinstance(x, str) and len(x) >= 8 for x in v) else -1)
' 2>/dev/null)"
case "$canary_count" in
  ""|-1|0) bad "CANARIES file is invalid or an empty list (need at least one string of 8 or more characters)" ;;
  *) ok "CANARIES $canary_count canaries, all long enough" ;;
esac

# REACH
# Probe pods satisfy the restricted Pod Security profile. Their labels must not match any Service
# selector in `mcp`: a match would put the probe behind that Service. Abort if that cannot be read.
PROBE_LABEL="app.kubernetes.io/instance=butlers"
svc_json="$(kubectl -n "$MCP_NS" get svc -o json 2>/dev/null)" || {
  echo "live checks ABORTED: cannot read Services in $MCP_NS (forbidden?); the labelled probe cannot be proven safe" >&2
  exit 3
}
echo "$svc_json" | PROBE_LABEL="$PROBE_LABEL" python3 -c '
import json, os, sys
k, v = os.environ["PROBE_LABEL"].split("=", 1)
labels = {k: v}
for svc in json.load(sys.stdin)["items"]:
    sel = svc["spec"].get("selector") or {}
    if sel and all(labels.get(a) == b for a, b in sel.items()):
        print("probe labels would match Service " + svc["metadata"]["name"]); sys.exit(1)
' || { echo "live checks ABORTED: the labelled probe would match a Service selector in $MCP_NS" >&2; exit 3; }
probe() { # name labels
  local overrides='{"spec":{"securityContext":{"runAsNonRoot":true,"runAsUser":10001,"seccompProfile":{"type":"RuntimeDefault"}},"containers":[{"name":"probe","image":"'"$IMAGE"'","command":["sleep","120"],"securityContext":{"allowPrivilegeEscalation":false,"capabilities":{"drop":["ALL"]},"readOnlyRootFilesystem":true,"runAsNonRoot":true,"seccompProfile":{"type":"RuntimeDefault"}}}]}}'
  kubectl -n "$MCP_NS" run "$1" --image="$IMAGE" --restart=Never ${2:+--labels="$2"} --overrides="$overrides" >/dev/null 2>&1
  kubectl -n "$MCP_NS" wait --for=condition=Ready "pod/$1" --timeout=60s >/dev/null 2>&1
}
tcp() { # namespace pod host port
  kubectl -n "$1" exec "$2" -- python3 -c "import socket,sys; s=socket.socket(); s.settimeout(4); s.connect((sys.argv[1], int(sys.argv[2])))" "$3" "$4" >/dev/null 2>&1
}
probe pubmcp-probe-labelled "$PROBE_LABEL"
probe pubmcp-probe-plain ""
tcp "$MCP_NS" pubmcp-probe-labelled "$SVC" 8080 && ok "REACH labelled mcp pod reaches 8080" || bad "REACH labelled mcp pod refused"
tcp "$MCP_NS" pubmcp-probe-plain "$SVC" 8080 && bad "REACH unlabelled mcp pod reached 8080" || ok "REACH unlabelled mcp pod refused"
server="$(kubectl -n "$NS" get pod -l app.kubernetes.io/component=butlers-public-mcp -o name | head -1)"
tcp "$NS" "${server#pod/}" "postgres-$ENV_NAME-rw.$PG_NS.svc.cluster.local" 5432 && ok "REACH server pod reaches postgres 5432" || bad "REACH server pod cannot reach postgres 5432"
tcp "$NS" "${server#pod/}" 1.1.1.1 443 && bad "REACH server pod reached the internet" || ok "REACH server pod has no internet route"

# ISOLATION: the password is read from the pod's own mounted file and never printed.
iso='
import asyncio, os
import asyncpg
async def main():
    password = open("/etc/butlers-public-mcp/db/password").read().strip()
    for db, want in (("butlers_public", True), ("butlers", False), ("postgres", False), ("template1", False)):
        try:
            c = await asyncpg.connect(host=os.environ["MCP_PUBLIC_DB_HOST"], port=int(os.environ["MCP_PUBLIC_DB_PORT"]),
                user=os.environ["MCP_PUBLIC_DB_USER"], password=password, database=db, ssl="require", timeout=8)
            await c.close()
            word = "allowed"
        except Exception as exc:
            # Only a pg_hba/auth refusal (SQLSTATE class 28) counts as refused. A timeout, DNS
            # or TLS error says nothing about the boundary.
            state = getattr(exc, "sqlstate", None) or ""
            word = "refused" if state.startswith("28") else "inconclusive"
        if word == "inconclusive":
            print("INCONCLUSIVE ISOLATION login to " + db + ": " + type(exc).__name__)
        else:
            good = (word == "allowed") == want
            print(("ok   " if good else "FAIL ") + "ISOLATION login to " + db + ": " + word)
asyncio.run(main())
'
out="$(kubectl -n "$NS" exec "${server#pod/}" -- python3 -c "$iso" 2>&1)"
echo "$out" | grep -E '^(ok|FAIL|INCONCLUSIVE) ' || bad "ISOLATION probe produced no verdict"
fails=$((fails + $(echo "$out" | grep -cE '^(FAIL|INCONCLUSIVE) ')))
[ "$fails" -eq 0 ] && echo "live checks passed" || echo "live checks FAILED ($fails)"
exit "$([ "$fails" -eq 0 ] && echo 0 || echo 1)"
