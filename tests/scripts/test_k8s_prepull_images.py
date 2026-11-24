"""scripts/k8s/prepull-images.sh: pull a release's images before Helm replaces pods (bu-ll2m3i).

Every workload uses the Recreate strategy, so old pods stop before new ones start. A slow
image pull (9 GB base layer over a relayed tailnet path) then left dev down for the whole pull.
Pulling first, while the old pods keep serving, makes the outage only the container restart.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts/k8s/prepull-images.sh"

_TEMPLATE = """\
---
kind: Deployment
spec:
  template:
    spec:
      initContainers:
        - name: migrate
          image: registry.invalid/butlers-app:tag123
      containers:
        - name: api
          image: "registry.invalid/butlers-app:tag123"
---
kind: Deployment
spec:
  template:
    spec:
      containers:
        - name: web
          image: registry.invalid/butlers-frontend:tag123-dev
"""

_HELM = """#!/usr/bin/env bash
echo "helm $*" >> "$CALL_LOG"
[ -n "$FAIL_TEMPLATE" ] && exit 4
cat "$TEMPLATE_FILE"
"""

# kubectl dispatches on its arguments. The pod's observed state comes from $POD_STATE
# (a JSON file), so each test chooses how the pull ends.
_KUBECTL = """#!/usr/bin/env bash
echo "kubectl $*" >> "$CALL_LOG"
case "$*" in
  *" create -f -"*) cat > "$CREATED"; exit 0 ;;
  *" get pods -l "*) printf '%b' "$NODES"; exit 0 ;;
  *" get pod "*) cat "$POD_STATE"; exit 0 ;;
  *" delete pod "*) exit 0 ;;
esac
exit 0
"""


@pytest.fixture()
def env(tmp_path: Path) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("helm", _HELM), ("kubectl", _KUBECTL)):
        (bin_dir / name).write_text(body, encoding="utf-8")
        (bin_dir / name).chmod(0o755)
    (tmp_path / "template.yaml").write_text(_TEMPLATE, encoding="utf-8")
    (tmp_path / "calls.log").write_text("", encoding="utf-8")
    return {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "CALL_LOG": str(tmp_path / "calls.log"),
        "TEMPLATE_FILE": str(tmp_path / "template.yaml"),
        "CREATED": str(tmp_path / "created.json"),
        "POD_STATE": str(tmp_path / "pod.json"),
        "NODES": "node-a\\nnode-a\\n",
        "FAIL_TEMPLATE": "",
        "PREPULL_POLL_SECONDS": "0",
        "PREPULL_TIMEOUT": "5",
    }


def _state(env: dict[str, str], phase: str, statuses: list[dict]) -> None:
    Path(env["POD_STATE"]).write_text(
        json.dumps({"status": {"phase": phase, "containerStatuses": statuses}}), encoding="utf-8"
    )


def _pulled(name: str) -> dict:
    return {"name": name, "imageID": "registry.invalid/x@sha256:" + "0" * 64, "state": {}}


def _cleaned_up(calls: list[str]) -> bool:
    """The pre-pull pod is deleted after it was created, whatever the outcome."""
    created = [i for i, c in enumerate(calls) if "create -f -" in c]
    deleted = [i for i, c in enumerate(calls) if " delete pod " in c]
    return bool(created) and bool(deleted) and deleted[-1] > created[-1]


def _run(tmp_path: Path, env: dict[str, str]) -> tuple[int, str, list[str]]:
    script = shutil.copy2(_SCRIPT, tmp_path / "prepull.sh")
    result = subprocess.run(
        ["bash", str(script), "ns-x", "tag123", "rel", "--", "-n", "ns-x", "--set", "image.tag=tag123"],
        capture_output=True, text=True, env=env, cwd=tmp_path,
    )  # fmt: skip
    calls = Path(env["CALL_LOG"]).read_text(encoding="utf-8").splitlines()
    return result.returncode, result.stdout + result.stderr, calls


def test_pulls_every_distinct_release_image_on_each_serving_node(
    tmp_path: Path, env: dict[str, str]
) -> None:
    _state(env, "Succeeded", [_pulled("image-0"), _pulled("image-1")])
    code, output, calls = _run(tmp_path, env)
    assert code == 0, output
    # The same Helm arguments render exactly what the release will run.
    assert calls[0] == "helm template rel . -n ns-x --set image.tag=tag123"
    pod = json.loads(Path(env["CREATED"]).read_text(encoding="utf-8"))
    spec = pod["spec"]
    assert spec["nodeName"] == "node-a"
    assert spec["restartPolicy"] == "Never"
    assert [c["image"] for c in spec["containers"]] == [
        "registry.invalid/butlers-app:tag123",
        "registry.invalid/butlers-frontend:tag123-dev",
    ]
    assert all(c["imagePullPolicy"] == "IfNotPresent" for c in spec["containers"])
    # Not a release pod: the runtime topology preflight must never count it.
    assert "app.kubernetes.io/instance" not in pod["metadata"].get("labels", {})
    assert sum("create -f -" in c for c in calls) == 1  # one node, deduplicated
    assert _cleaned_up(calls)


def test_a_fresh_namespace_has_nothing_to_keep_serving_and_skips(
    tmp_path: Path, env: dict[str, str]
) -> None:
    env["NODES"] = ""
    code, output, calls = _run(tmp_path, env)
    assert code == 0, output
    assert "skipping" in output
    assert not any("create -f -" in c for c in calls)


def test_an_unpullable_image_fails_before_anything_is_released(
    tmp_path: Path, env: dict[str, str]
) -> None:
    waiting = {
        "name": "image-1",
        "imageID": "",
        "state": {"waiting": {"reason": "InvalidImageName"}},
    }
    _state(env, "Pending", [_pulled("image-0"), waiting])
    code, output, calls = _run(tmp_path, env)
    assert code == 1
    assert "InvalidImageName" in output
    assert _cleaned_up(calls)


def test_a_pull_that_outlasts_the_timeout_fails_and_cleans_up(
    tmp_path: Path, env: dict[str, str]
) -> None:
    pulling = {
        "name": "image-0",
        "imageID": "",
        "state": {"waiting": {"reason": "ContainerCreating"}},
    }
    _state(env, "Pending", [pulling])
    env["PREPULL_TIMEOUT"] = "1"
    env["PREPULL_POLL_SECONDS"] = "1"
    code, output, calls = _run(tmp_path, env)
    assert code == 1
    assert "timed out" in output
    assert _cleaned_up(calls)


def test_a_finished_pod_missing_an_image_id_is_not_a_pull(
    tmp_path: Path, env: dict[str, str]
) -> None:
    _state(env, "Failed", [_pulled("image-0"), {"name": "image-1", "imageID": "", "state": {}}])
    code, _output, _calls = _run(tmp_path, env)
    assert code == 1


def test_a_render_failure_stops_the_deploy(tmp_path: Path, env: dict[str, str]) -> None:
    env["FAIL_TEMPLATE"] = "1"
    code, _output, calls = _run(tmp_path, env)
    assert code != 0
    assert not any("create -f -" in c for c in calls)
