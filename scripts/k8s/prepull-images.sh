#!/usr/bin/env bash
# Pull a release's images onto its serving node(s) before Helm replaces any pod.
#
# Usage (from deploy/helm/butlers): prepull-images.sh <namespace> <tag> <release> -- <helm args...>
#
# Every workload uses the Recreate strategy (no two schedulers, pollers or migrators may
# overlap), so Helm stops the old pods before the new ones pull their image. With a large
# image on a slow registry path that left dev down for the whole pull (run 20261114170231:
# >10 min in Init "Pulling image", rollout timed out). This script pulls first, while the
# old pods keep serving, so the Recreate outage is only the container restart.
#
# It renders the release with the same Helm arguments, collects every distinct image, and
# runs one short-lived pod per node that hosts the release's pods today. The pod has one
# container per image (imagePullPolicy IfNotPresent, a no-op command) and is pinned with
# nodeName. Success means every container reports an imageID. The pod is always deleted. It
# carries no app.kubernetes.io/instance label, so the runtime topology preflight never
# counts it. A namespace with no release pods has nothing to keep serving and is skipped.
#
#   PREPULL_TIMEOUT        seconds to wait per node (default 5400)
#   PREPULL_POLL_SECONDS   status poll interval (default 15)
#
# Read-only apart from that one pod: it never touches the release, scales or deletes
# anything else. A failure exits non-zero before anything is released.
set -euo pipefail

[ "$#" -ge 3 ] || { echo "usage: $0 <namespace> <tag> <release> -- <helm args...>" >&2; exit 2; }
ns="$1" tag="$2" release="$3"
# An empty argument (e.g. `make deploy-dev TAG=`) would shift the rest and render a wrong release.
{ [ -n "$ns" ] && [ -n "$tag" ] && [ -n "$release" ]; } \
  || { echo "namespace, tag and release must be non-empty" >&2; exit 2; }
shift 3
[ "${1:-}" = "--" ] && shift
timeout="${PREPULL_TIMEOUT:-5400}"
poll="${PREPULL_POLL_SECONDS:-15}"

rendered="$(helm template "$release" . "$@")"
mapfile -t images < <(printf '%s\n' "$rendered" | python3 -I -c '
import re, sys
seen = []
for line in sys.stdin:
    match = re.match(r"""^\s*-?\s*image:\s*["\x27]?([^"\x27\s]+)["\x27]?\s*$""", line)
    if match and match.group(1) not in seen:
        seen.append(match.group(1))
print("\n".join(seen))
')
[ "${#images[@]}" -gt 0 ] && [ -n "${images[0]}" ] || { echo "pre-pull: release renders no images" >&2; exit 1; }

mapfile -t nodes < <(kubectl -n "$ns" get pods -l "app.kubernetes.io/instance=$release" \
  -o jsonpath='{range .items[*]}{.spec.nodeName}{"\n"}{end}' | sed '/^$/d' | sort -u)
if [ "${#nodes[@]}" -eq 0 ]; then
  echo "pre-pull: no $release pods in $ns to keep serving; skipping"
  exit 0
fi

pod=""
cleanup() {
  [ -z "$pod" ] || kubectl -n "$ns" delete pod "$pod" --ignore-not-found --wait=false >/dev/null 2>&1 || true
}
trap cleanup EXIT

for node in "${nodes[@]}"; do
  pod="$(printf '%s-image-prepull-%s' "$release" "$node" | tr -c 'a-z0-9-\n' '-' | cut -c1-63 | sed 's/-*$//')"
  kubectl -n "$ns" delete pod "$pod" --ignore-not-found --wait=true >/dev/null
  python3 -I -c '
import json, sys
pod, node, deadline, images = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4:]
print(json.dumps({
    "apiVersion": "v1",
    "kind": "Pod",
    "metadata": {"name": pod, "labels": {"app.kubernetes.io/component": "image-prepull"}},
    "spec": {
        "nodeName": node,
        "restartPolicy": "Never",
        "activeDeadlineSeconds": deadline,
        "automountServiceAccountToken": False,
        "terminationGracePeriodSeconds": 0,
        "containers": [
            {
                "name": f"image-{index}",
                "image": image,
                "imagePullPolicy": "IfNotPresent",
                # The pull is the point; a missing shell only fails the no-op afterwards.
                "command": ["/bin/sh", "-c", "exit 0"],
                "resources": {
                    "requests": {"cpu": "1m", "memory": "8Mi"},
                    "limits": {"cpu": "50m", "memory": "32Mi"},
                },
            }
            for index, image in enumerate(images)
        ],
    },
}))
' "$pod" "$node" "$((timeout + 60))" "${images[@]}" | kubectl -n "$ns" create -f - >/dev/null
  echo "pre-pull: $tag: ${#images[@]} image(s) on node $node (timeout ${timeout}s)"

  start=$SECONDS
  while :; do
    verdict="$(kubectl -n "$ns" get pod "$pod" -o json | python3 -I -c '
import json, sys
expected = int(sys.argv[1])
status = json.load(sys.stdin).get("status", {})
containers = status.get("containerStatuses") or []
pulled = sum(bool(c.get("imageID")) for c in containers)
fatal = sorted({
    (c.get("state", {}).get("waiting") or {}).get("reason", "")
    for c in containers
} & {"InvalidImageName", "ErrImageNeverPull"})
if fatal:
    print("fail " + ",".join(fatal))
elif pulled == expected:
    print("done")
elif status.get("phase") in {"Succeeded", "Failed"}:
    print(f"fail finished with {pulled}/{expected} images pulled")
else:
    print(f"wait {pulled}/{expected}")
' "${#images[@]}")"
    case "$verdict" in
      done) echo "pre-pull: node $node has every image ($((SECONDS - start))s)"; break ;;
      fail*) echo "pre-pull: node $node: ${verdict#fail }" >&2; exit 1 ;;
    esac
    if [ $((SECONDS - start)) -ge "$timeout" ]; then
      echo "pre-pull: node $node timed out after ${timeout}s (${verdict#wait } pulled); nothing released" >&2
      exit 1
    fi
    if [ $(((SECONDS - start) % 60)) -lt "$poll" ] || [ "$poll" -eq 0 ]; then
      echo "pre-pull: node $node ${verdict#wait } image(s) present after $((SECONDS - start))s"
    fi
    sleep "$poll"
  done
  cleanup
  pod=""
done
