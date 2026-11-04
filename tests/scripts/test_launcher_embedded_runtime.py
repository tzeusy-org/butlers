"""Execute actual workflow Python against real private files and sealed pure validators."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from scripts import session_host_ci as ci
from scripts import session_host_probe_adapter as adapter

PACKET = Path(__file__).parent
# Optional override permits isolated review without modifying the checkout.
SOURCE_ROOT = Path(os.environ.get("LAUNCHER_TEST_SOURCE_ROOT", Path(__file__).resolve().parents[2]))
WORKFLOW = PACKET / "session-host-causal.yml"
if not WORKFLOW.exists():
    WORKFLOW = SOURCE_ROOT / ".github/workflows/session-host-causal.yml"
SENTINEL = "PRIVATE_SYNTHETIC_SENTINEL_DO_NOT_EMIT"


def embedded(name):
    workflow = yaml.safe_load(WORKFLOW.read_text())
    step = next(s for s in workflow["jobs"]["causal"]["steps"] if s.get("name") == name)
    return step["run"].split("<<'PYCODE'\n", 1)[1].rsplit("\nPYCODE", 1)[0]


# Bounded pure synthetic receipt fixture conserved from the reviewed harness tests.
# It launches no process and contains no authority material.


def native_record(outcome="red", *, arm="A1", nonce="a" * 32, profile=None):
    record = {
        "version": 1,
        "node": adapter.NODE,
        "collected": 1,
        "arm": arm,
        "nonce": nonce,
        "profile_sha256": profile or ci.PROFILE_SHA256,
        "native_sha256": adapter.NATIVE_SHA256,
        "phases": {
            "setup": "passed",
            "call": "failed" if outcome == "red" else "passed",
            "teardown": "passed",
        },
        "images": [
            {"destination": f"/runtime/{name}", "sha256": digit * 64, "mode": 0o555, "size": 100}
            for name, digit in (("init", "1"), ("program", "2"))
        ],
        "adapter_ok": True,
        "diagnostic": dict(adapter.RED_DIAGNOSTIC),
        "peer_state": dict(adapter.RED_STATE),
        "settlement": {
            "owner_healthy": True,
            "owner_pending": 0,
            "stage_empty": True,
            "domains": [
                dict.fromkeys(
                    (
                        "namespace_dead",
                        "child_reaped",
                        "settled",
                        "fds_closed",
                        "spawn_done",
                        "reaper_done",
                    ),
                    True,
                )
                for _ in range(1 if outcome == "red" else 2)
            ],
        },
        "exitstatus": 1 if outcome == "red" else 0,
    }
    if outcome == "green":
        record["diagnostic"].update(
            ready_receipt="matched",
            ready_bytes=34,
            stderr_bytes=0,
            stderr_category="empty",
            stderr_reason="unclassified",
            stderr_sha256=hashlib.sha256(b"").hexdigest(),
        )
        record["peer_state"].update(
            ready=True, exit=-9, output_bytes=27, execution_failure="CancelledError"
        )
    files = [
        {
            "destination": image["destination"],
            "perms": "0555",
            "fd_mode": 0o777,
            "regular": True,
            "seals": 15,
            "sha256": image["sha256"],
            "size": image["size"],
        }
        for image in record["images"]
    ]
    files += [
        {
            "destination": f"/invocation/{name}",
            "perms": "0400",
            "fd_mode": 0o777,
            "regular": True,
            "seals": 15,
        }
        for name in ("model-credential", "prompt", "mcp.json")
    ]
    record["launches"] = [
        dict(
            role=role,
            files=files,
            inherited=[
                "--info-fd",
                "--block-fd",
                "--shim-gate-fd",
                *[f["destination"] for f in files],
            ],
            argv_sha256="4" * 64,
        )
        for role in (["peer"] if outcome == "red" else ["peer", "reader"])
    ]
    return record


def fixture(tmp_path):
    settings = yaml.safe_load(WORKFLOW.read_text())["jobs"]["causal"]["env"]
    sources = json.loads(settings["PROBE_SOURCES_JSON"])
    env = {
        **os.environ,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": str(SOURCE_ROOT),
        "PROBE_SOURCE_SHA": settings["PROBE_SOURCE_SHA"],
        "PROBE_SOURCE_TREE": settings["PROBE_SOURCE_TREE"],
        "PROBE_SEAL_SHA256": settings["PROBE_SEAL_SHA256"],
        "PROBE_SOURCES_JSON": json.dumps(sources),
        "WORKFLOW_SHA": "d" * 40,
        "EVENT_SHA": "e" * 40,
        "PR_HEAD_SHA": "f" * 40,
        "GITHUB_RUN_ID": "123",
        "GITHUB_RUN_ATTEMPT": "1",
        "RUNNER_TEMP": str(tmp_path),
        "RUNNER_ENVIRONMENT": "github-hosted",
    }
    report = {
        "version": 1,
        "status": "causal_aba_observed",
        "github_run_id": "123",
        "github_run_attempt": "1",
        "identity": {
            "head": env["PROBE_SOURCE_SHA"],
            "tree": env["PROBE_SOURCE_TREE"],
            "sources": sources,
            **dict.fromkeys(("bwrap", "bwrap_version", "kernel", "apparmor"), "4" * 64),
        },
        "arms": [],
    }
    from scripts import session_host_ci as ci

    factory = native_record
    for name, digit, outcome in (("A1", "a", "red"), ("B", "b", "green"), ("A2", "c", "red")):
        profile = ci.RUNNER_PROFILE_SHA256 if name == "B" else ci.PROFILE_SHA256
        native = factory(outcome, arm=name, nonce=digit * 32, profile=profile)
        report["arms"].append(
            {
                "arm": name,
                "nonce": digit * 32,
                "profile_sha256": profile,
                "outcome": outcome,
                "native": native,
                "cleanup": "observed",
                "global_restriction": 1,
                "generic_userns": "denied",
            }
        )
    parent = tmp_path / "butlers-session-host-causal"
    parent.mkdir(mode=0o700)
    path = parent / "probe-report.json"
    path.write_text(json.dumps(report))
    path.chmod(0o600)
    return env, report, path


def run_code(code, env, cwd):
    return subprocess.run(
        [sys.executable, "-B", "-c", code], cwd=cwd, env=env, capture_output=True, timeout=10
    )


@pytest.mark.parametrize(
    "mutation",
    [
        None,
        "nested_unknown",
        "duplicate",
        "nonfinite",
        "wrong_run",
        "wrong_source",
        "symlink",
        "overcap",
        "parent_mode",
    ],
)
def test_actual_embedded_stager_uses_closed_report_and_real_file_refusals(tmp_path, mutation):
    env, report, path = fixture(tmp_path)
    if mutation == "nested_unknown":
        report["arms"][0]["native"]["settlement"]["unexpected"] = SENTINEL
    elif mutation == "wrong_run":
        report["github_run_id"] = "999"
    elif mutation == "wrong_source":
        report["identity"]["sources"]["uv.lock"] = "0" * 64
    raw = json.dumps(report).encode()
    if mutation == "duplicate":
        raw = raw[:-1] + b',"status":"' + SENTINEL.encode() + b'"}'
    elif mutation == "nonfinite":
        raw = raw.replace(b'"version": 1', b'"version": NaN', 1)
    elif mutation == "overcap":
        raw += b" " * (262145 - len(raw))
    path.write_bytes(raw)
    if mutation == "symlink":
        target = tmp_path / "private-target"
        path.rename(target)
        path.symlink_to(target)
    elif mutation == "parent_mode":
        path.parent.chmod(0o755)
    result = run_code(embedded("Stage only closed successful evidence"), env, tmp_path)
    assert SENTINEL.encode() not in result.stdout + result.stderr
    out = tmp_path / "session-host-causal-closed-evidence"
    if mutation is None:
        assert result.returncode == 0
        assert (out / "report.json").read_bytes() == raw
        binding = json.loads((out / "binding.json").read_text())
        assert binding["report_sha256"] == hashlib.sha256(raw).hexdigest()
        assert (
            binding["GITHUB_RUN_ID"] == "123"
            and binding["PROBE_SOURCE_SHA"] == env["PROBE_SOURCE_SHA"]
        )
        assert sorted(f.name for f in out.iterdir()) == ["binding.json", "report.json"]
    else:
        assert result.returncode != 0
        assert not out.exists()


@pytest.mark.parametrize("mutation", [None, "source_bytes", "head", "runner"])
def test_actual_embedded_preflight_refuses_before_privilege(tmp_path, mutation):
    env, report, path = fixture(tmp_path)
    for name in report["identity"]["sources"]:
        destination = tmp_path / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((SOURCE_ROOT / name).read_bytes())
    # Controlled git read seam: no Git refs mutated, exact real code still executes.
    binary = tmp_path / "bin"
    binary.mkdir()
    git = binary / "git"
    git.write_text(
        "#!"
        + sys.executable
        + (
            '\nimport sys\na=sys.argv[1:]\nif a==["rev-parse","HEAD"]: print("13'
            'ca133ad8c86f6ba23bf782449fcb9ac145f2da")\nelif a==["rev-parse","H'
            'EAD^{tree}"]: print("2ce06bffae08d4bb243a1b7af7ae42f4b19df65f")\n'
            'elif a!=["status","--porcelain"]: raise SystemExit(9)\n'
        )
    )
    git.chmod(0o755)
    env["PATH"] = str(binary) + os.pathsep + env["PATH"]
    if mutation == "source_bytes":
        (tmp_path / "scripts/runtime_cli_sandbox_init.c").write_text(SENTINEL)
    elif mutation == "head":
        env["PROBE_SOURCE_SHA"] = "0" * 40
    elif mutation == "runner":
        env["RUNNER_ENVIRONMENT"] = "self-hosted"
    result = run_code(embedded("Verify reviewed source before privileged setup"), env, tmp_path)
    assert SENTINEL.encode() not in result.stdout + result.stderr
    assert (result.returncode == 0) == (mutation is None)
