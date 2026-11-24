#!/usr/bin/env python3
"""Verify QA investigator isolation against a dev cluster (bu-ejow5p.11).

``render`` prints the manifests the run would apply (no cluster access). ``dry-run`` sends the
same Jobs to the API server with ``--dry-run=server`` (nothing is created) to check admission.
``run`` applies them with
``kubectl`` as the launcher service account (so the admission policy is exercised as in
production), reads each probe's content-blind receipt from the pod log and judges:

1. the baseline probe on the unmodified template passes every baseline check;
2. each inverse control (one hardening removed) goes RED, and the restored template passes it.

A synthetic canary Secret (random value, piped to kubectl on stdin, never printed) backs a
canary-publisher pod; the investigator must not reach that pod. The canary file and environment
live only in the canary pod, so the investigator's forbidden-path and forbidden-env checks are
sanity checks that the inverse controls cannot break open; the load-bearing proof of secrecy is
the refused connection to the canary pod and the absence of any mounted secret. Nothing real is
used: no GitHub credential, no provider mutation. Cluster acts are the owner's: this script is run
by a human and refuses any namespace that is not a ``-dev`` namespace, and any kube context that
does not look like a dev cluster.

The output is a receipt of check names, outcomes and digests, safe to attach to a review.
"""

from __future__ import annotations

import argparse
import json
import re
import secrets
import subprocess
import sys
import time
import uuid
from typing import Any

from butlers.core.qa import isolation_verifier as iv
from butlers.core.qa.egress_gate import EXIT_NOT_ENFORCED

_DEV_NAMESPACE = re.compile(r"^[a-z0-9-]+-dev$")
_CANARY_PORT = 9
#: A whole segment must be dev or k3d ("butlers-dev", "k3d-qa"); "prod-devops" must not match.
_DEV_CONTEXT = re.compile(r"(^|[-_.@/])(dev|k3d)([-_.@/]|$)")


def _first_line(text: str | None, limit: int = 400) -> str:
    """The server's own words, capped: why a call failed, never manifest content.

    kubectl prints ``error when retrieving current configuration of:`` and then the manifest
    and the real reason on later lines, so keep the first lines up to the dumped object.
    """
    kept: list[str] = []
    for line in (text or "").strip().splitlines():
        if line.startswith(("Name:", "Namespace:", "Object:", "{", "&{")):
            break
        kept.append(line.strip())
        if len(kept) == 3:
            break
    return " | ".join(kept)[:limit] or "no output"


def _kubectl(args: list[str], *, stdin: str | None = None, check: bool = True) -> str:
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["kubectl", *args], input=stdin, capture_output=True, text=True, check=False
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"kubectl {args[0]} failed (exit {result.returncode}): {_first_line(result.stderr)}"
        )
    return result.stdout


def _wait_job(namespace: str, name: str, timeout_s: int) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        out = _kubectl(
            ["-n", namespace, "get", "job", name, "-o", "jsonpath={.status.conditions[*].type}"],
            check=False,
        )
        if "Complete" in out or "Failed" in out:
            return
        time.sleep(3)
    raise TimeoutError(f"job {name} did not finish")


def _receipt(namespace: str, job: str) -> dict[str, Any] | None:
    log = _kubectl(["-n", namespace, "logs", f"job/{job}"], check=False)
    for line in reversed(log.strip().splitlines()):
        try:
            return json.loads(line)
        except ValueError:
            continue
    # No receipt: the container never reached the probe (the egress gate refused, the image
    # lacks a module). The probe's own output is content-blind; keep its last lines for the log.
    tail = " | ".join(line.strip()[:200] for line in log.strip().splitlines()[-2:]) or "no output"
    sys.stderr.write(f"probe job without a receipt: {tail}\n")
    return None


def _probe_job(args: argparse.Namespace, canary_host: str) -> dict[str, Any]:
    command = iv.probe_command(
        refused_tcp=[
            f"canary_publisher={canary_host}:{_CANARY_PORT}",
            *args.refused_tcp,
        ],
        reachable_tcp=args.reachable_tcp,
        forbidden_env=["QA_ISOLATION_CANARY"],
        # Not /proc/1/environ: the probe is PID 1, so its own uid can always read it.
        forbidden_path=["/canary/value", "/etc/shadow"],
        readable_path=args.readable_path,
        recheck_after=_RECHECK_AFTER_S,
    )
    return iv.build_probe_job(
        uuid.uuid4(),
        release=args.release,
        namespace=args.namespace,
        image=args.image,
        exchange_claim=args.exchange_claim,
        command=command,
        deadline_s=300,
    )


#: Seconds the probe waits before repeating its refused-connection checks (start-up race tell).
_RECHECK_AFTER_S = 20


class VerifierError(RuntimeError):
    """A cluster call failed in a way that is not an admission decision: never a catch."""


ADMITTED = "admitted"
REFUSED = "refused"


def _is_policy_denial(stderr: str) -> bool:
    """True only for this feature's ValidatingAdmissionPolicy denying the request."""
    return (
        "ValidatingAdmissionPolicy" in stderr
        and "denied request" in stderr
        and "-qa-investigator-" in stderr
    )


def _apply(args: argparse.Namespace, manifest: dict[str, Any], *, dry_run: bool = False) -> str:
    """Apply as the launcher service account: ``ADMITTED`` or ``REFUSED``.

    A failure counts as a refusal (an admission catch) only when stderr is this feature's policy
    denying the request. Any other failure (no cluster, forbidden, bad manifest, timeout) raises
    ``VerifierError``: it must never be scored as a control being caught.
    """
    as_arg = f"--as=system:serviceaccount:{args.namespace}:{args.launcher_sa}"
    extra = ["--dry-run=server"] if dry_run else []
    result = subprocess.run(  # noqa: S603
        ["kubectl", as_arg, "apply", *extra, "-f", "-"],
        input=json.dumps(manifest),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return ADMITTED
    if _is_policy_denial(result.stderr or ""):
        return REFUSED
    raise VerifierError(
        f"kubectl apply failed and was not an admission policy denial: {_first_line(result.stderr)}"
    )


def _gate_refused(namespace: str, job: str) -> bool:
    """Whether the Job's container exited with the egress gate's refusal code."""
    code = _kubectl(
        [
            "-n",
            namespace,
            "get",
            "pods",
            "-l",
            f"job-name={job}",
            "-o",
            "jsonpath={.items[0].status.containerStatuses[0].state.terminated.exitCode}",
        ],
        check=False,
    ).strip()
    return code == str(EXIT_NOT_ENFORCED)


def _run_one(
    args: argparse.Namespace, manifest: dict[str, Any]
) -> tuple[dict[str, Any] | None, bool, bool]:
    """Apply and read one probe: ``(receipt, refused_by_admission, refused_by_egress_gate)``."""
    if _apply(args, manifest) == REFUSED:
        return None, True, False
    name = manifest["metadata"]["name"]
    _wait_job(args.namespace, name, 300)
    receipt = _receipt(args.namespace, name)
    gated = receipt is None and _gate_refused(args.namespace, name)
    return receipt, False, gated


def run(args: argparse.Namespace) -> dict[str, Any]:
    ns = args.namespace
    canary_name = f"qa-isolation-canary-{uuid.uuid4().hex[:8]}"
    secret_name = canary_name
    created: list[tuple[str, str]] = []
    try:
        _kubectl(
            ["-n", ns, "create", "secret", "generic", secret_name, "--from-file=value=/dev/stdin"],
            stdin=secrets.token_urlsafe(24),
        )
        created.append(("secret", secret_name))
        pod = iv.canary_pod(
            name=canary_name, namespace=ns, image=args.image, canary_secret=secret_name
        )
        _kubectl(["apply", "-f", "-"], stdin=json.dumps(pod))
        created.append(("pod", canary_name))
        _kubectl(
            ["-n", ns, "wait", "--for=condition=Ready", f"pod/{canary_name}", "--timeout=120s"]
        )
        host = _kubectl(
            ["-n", ns, "get", "pod", canary_name, "-o", "jsonpath={.status.podIP}"]
        ).strip()

        baseline_manifest = _probe_job(args, host)
        created.append(("job", baseline_manifest["metadata"]["name"]))
        baseline, baseline_refused, _ = _run_one(args, baseline_manifest)
        failures = iv.baseline_verdict(None if baseline_refused else baseline)

        verdicts = []
        restored_failed: list[Any] = []
        for control in iv.INVERSE_CONTROLS:
            removed_manifest = iv.render_inverse(_probe_job(args, host), control)
            created.append(("job", removed_manifest["metadata"]["name"]))
            removed, removed_refused, removed_gated = _run_one(args, removed_manifest)
            restored_manifest = _probe_job(args, host)
            created.append(("job", restored_manifest["metadata"]["name"]))
            restored, _, _ = _run_one(args, restored_manifest)
            verdicts.append(
                iv.judge_control(
                    control,
                    removed,
                    restored,
                    removed_refused=removed_refused,
                    removed_gate_refused=removed_gated,
                )
            )
            # Content-blind detail for a restored probe that did not pass: names and categories.
            restored_failed.append(
                [row for row in (restored or {}).get("checks", []) if row.get("outcome") != "pass"]
                if restored is not None
                else "no_receipt"
            )
    finally:
        for kind, name in created:
            _kubectl(
                ["-n", ns, "delete", kind, name, "--ignore-not-found", "--wait=false"], check=False
            )
    passed = not failures and all(v.ok for v in verdicts)
    return {
        "schema": 1,
        "passed": passed,
        "baseline_failures": failures,
        "baseline_digest": (baseline or {}).get("digest"),
        "controls": [
            {
                "name": v.name,
                "red_when_removed": v.red_when_removed,
                "green_when_restored": v.green_when_restored,
                "caught_by": v.caught_by,
                "restored_failed_checks": detail,
            }
            for v, detail in zip(verdicts, restored_failed, strict=True)
        ],
    }


def dry_run(args: argparse.Namespace) -> dict[str, Any]:
    """Server-side dry-run: creates nothing, deletes nothing, reads no pod.

    Admission (including the ValidatingAdmissionPolicies) evaluates server-side dry-run
    requests, so this checks the installed policies against the real API server's CEL type and
    cost checking, which a chart render and cel-python cannot. With ``--policies-file`` (a
    ``helm template`` of the chart) the policies and bindings are also dry-run applied as the
    current user. The probe Job must be admitted and every inverse control refused.
    """
    policies_valid: bool | None = None
    policies_error: str | None = None
    if args.policies_file:
        result = subprocess.run(  # noqa: S603
            ["kubectl", "apply", "--dry-run=server", "-f", args.policies_file],
            capture_output=True,
            text=True,
            check=False,
        )
        policies_valid = result.returncode == 0
        if not policies_valid:
            # The server's own words, first line only, so the receipt says why (no manifest text).
            lines = (result.stderr or "").strip().splitlines()
            policies_error = lines[0][:300] if lines else "kubectl failed with no output"
    host = "192.0.2.1"
    probe_admitted = _apply(args, _probe_job(args, host), dry_run=True) == ADMITTED
    refused = {
        c.name: _apply(args, iv.render_inverse(_probe_job(args, host), c), dry_run=True) == REFUSED
        for c in iv.INVERSE_CONTROLS
    }
    verdict = iv.judge_dry_run(
        policies_valid=policies_valid, probe_admitted=probe_admitted, refused=refused
    )
    return {
        "schema": 1,
        "mode": "dry-run",
        "passed": verdict.ok,
        "policies_valid": verdict.policies_valid,
        "policies_error": policies_error,
        "probe_admitted": verdict.probe_admitted,
        "unrefused_controls": list(verdict.unrefused_controls),
    }


def render(args: argparse.Namespace) -> dict[str, Any]:
    host = "<canary-pod-ip>"
    return {
        "probe": _probe_job(args, host),
        "inverse": {
            c.name: iv.render_inverse(_probe_job(args, host), c) for c in iv.INVERSE_CONTROLS
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("mode", choices=["render", "dry-run", "run"])
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--image", required=True, help="the daemon image, by digest")
    parser.add_argument("--release", default="butlers")
    parser.add_argument("--exchange-claim", default="butlers-qa-exchange")
    parser.add_argument("--launcher-sa", default="butlers-up")
    parser.add_argument("--refused-tcp", action="append", default=[], metavar="NAME=HOST:PORT")
    parser.add_argument("--reachable-tcp", action="append", default=[], metavar="NAME=HOST:PORT")
    parser.add_argument(
        "--policies-file", help="dry-run: a `helm template` of the chart's policies and bindings"
    )
    parser.add_argument("--readable-path", action="append", default=[])
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not _DEV_NAMESPACE.match(args.namespace):
        sys.stderr.write("refusing: the verifier only runs in a -dev namespace\n")
        return 2
    if args.mode in ("run", "dry-run"):
        context = _kubectl(["config", "current-context"], check=False).strip()
        if "prod" in context or not _DEV_CONTEXT.search(context):
            sys.stderr.write(
                "refusing: the current kube context does not look like a dev cluster\n"
            )
            return 2
    modes = {"render": render, "dry-run": dry_run, "run": run}
    try:
        result = modes[args.mode](args)
    except (VerifierError, RuntimeError, TimeoutError) as exc:
        sys.stderr.write(f"verifier error (not a pass): {type(exc).__name__}: {exc}\n")
        return 3
    sys.stdout.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return 0 if args.mode == "render" or result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
