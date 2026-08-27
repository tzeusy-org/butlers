"""Focused tests for the read-only Tailscale Serve data-plane probe."""

from __future__ import annotations

import importlib.util
import json
import os
import ssl
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from textwrap import dedent
from types import ModuleType, SimpleNamespace

import pytest

pytestmark = pytest.mark.unit

_SCRIPT = Path("scripts/tailscale_serve_probe.py")


def _probe_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("tailscale_serve_probe", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _sequence_transport(
    module: ModuleType,
    responses: list[object],
) -> tuple[Callable[[str, float], object], list[tuple[str, float]]]:
    calls: list[tuple[str, float]] = []
    remaining: Iterator[object] = iter(responses)

    def transport(url: str, timeout: float) -> object:
        calls.append((url, timeout))
        response = next(remaining)
        if isinstance(response, BaseException):
            raise response
        return response

    return transport, calls


def test_probe_requires_exact_ready_json_body() -> None:
    module = _probe_module()
    transport, _ = _sequence_transport(
        module,
        [module.ProbeResponse(status=200, body=b'{"message":"ok"}')],
    )

    result = module.probe_url(
        "https://device.example.ts.net/butlers-dev-api/api/health",
        transport=transport,
        attempts=1,
    )

    assert result.outcome is module.ProbeOutcome.BODY_INVALID
    assert result.status_code == 200


def test_probe_accepts_only_top_level_ok_status() -> None:
    module = _probe_module()
    transport, _ = _sequence_transport(
        module,
        [module.ProbeResponse(status=200, body=b'{"status":"ok"}')],
    )

    result = module.probe_url(
        "https://device.example.ts.net/butlers-dev-api/api/health",
        transport=transport,
        attempts=1,
    )

    assert result.outcome is module.ProbeOutcome.OK
    assert result.status_code == 200


def test_https_transport_requires_hostname_and_expiry_verification(monkeypatch) -> None:
    module = _probe_module()
    context = SimpleNamespace(
        check_hostname=False,
        verify_mode=None,
        minimum_version=None,
    )
    captured: dict[str, object] = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def getcode(self):
            return 200

        def read(self, _limit):
            return b'{"status":"ok"}'

    class Opener:
        def open(self, request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return Response()

    monkeypatch.setattr(module.ssl, "create_default_context", lambda: context)
    monkeypatch.setattr(
        module.urllib.request,
        "build_opener",
        lambda *handlers: captured.update(handlers=handlers) or Opener(),
    )

    response = module._strict_https_get(
        "https://device.example.ts.net/butlers-dev-api/api/health",
        timeout=4.0,
    )

    assert response == module.ProbeResponse(200, b'{"status":"ok"}')
    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.minimum_version == ssl.TLSVersion.TLSv1_2
    assert captured["timeout"] == 4.0
    assert any(isinstance(handler, module._NoRedirectHandler) for handler in captured["handlers"])
    assert any(
        isinstance(handler, module.urllib.request.HTTPSHandler) for handler in captured["handlers"]
    )


def test_probe_classifies_certificate_failure_without_retrying() -> None:
    module = _probe_module()
    transport, calls = _sequence_transport(module, [ssl.SSLCertVerificationError()])

    result = module.probe_url(
        "https://device.example.ts.net/butlers-dev-api/api/health",
        transport=transport,
        attempts=3,
    )

    assert result.outcome is module.ProbeOutcome.CERT_INVALID
    assert result.attempts == 1
    assert len(calls) == 1


def test_probe_classifies_route_404_without_retrying() -> None:
    module = _probe_module()
    transport, calls = _sequence_transport(
        module,
        [module.ProbeResponse(status=404, body=b"not found")],
    )

    result = module.probe_url(
        "https://device.example.ts.net/butlers-dev-api/api/health",
        transport=transport,
        attempts=3,
    )

    assert result.outcome is module.ProbeOutcome.ROUTE_404
    assert result.attempts == 1
    assert len(calls) == 1


def test_probe_retries_timeouts_with_fixed_delay_then_succeeds() -> None:
    module = _probe_module()
    transport, calls = _sequence_transport(
        module,
        [TimeoutError(), TimeoutError(), module.ProbeResponse(200, b'{"status":"ok"}')],
    )
    sleeps: list[float] = []

    result = module.probe_url(
        "https://device.example.ts.net/butlers-dev-api/api/health",
        transport=transport,
        attempts=3,
        retry_delay=0.25,
        sleeper=sleeps.append,
    )

    assert result.outcome is module.ProbeOutcome.OK
    assert result.attempts == 3
    assert len(calls) == 3
    assert sleeps == [0.25, 0.25]


def test_probe_reports_timeout_after_bounded_attempts() -> None:
    module = _probe_module()
    transport, calls = _sequence_transport(module, [TimeoutError(), TimeoutError()])

    result = module.probe_url(
        "https://device.example.ts.net/butlers-dev-api/api/health",
        transport=transport,
        attempts=2,
        retry_delay=0,
        sleeper=lambda _: None,
    )

    assert result.outcome is module.ProbeOutcome.TIMEOUT
    assert result.attempts == 2
    assert len(calls) == 2


@pytest.mark.parametrize(
    ("timeout", "attempts", "retry_delay"),
    [
        (float("nan"), 1, 0),
        (float("inf"), 1, 0),
        (31, 1, 0),
        (1, 5, 0),
        (1, 1, 6),
    ],
)
def test_probe_rejects_non_finite_or_out_of_policy_retry_settings(
    timeout: float,
    attempts: int,
    retry_delay: float,
) -> None:
    module = _probe_module()

    with pytest.raises(ValueError, match="bounded probe policy"):
        module.probe_url(
            "https://device.example.ts.net/butlers-dev-api/api/health",
            timeout=timeout,
            attempts=attempts,
            retry_delay=retry_delay,
        )


def test_probe_rejects_target_hosts_own_tailscale_identity(monkeypatch, capsys) -> None:
    module = _probe_module()
    monkeypatch.setattr(module, "_tailscale_self_dns_name", lambda: "device.example.ts.net")
    transport_called = False

    def forbidden_transport(_url: str, _timeout: float):
        nonlocal transport_called
        transport_called = True
        raise AssertionError("same-host identity must fail before HTTPS")

    monkeypatch.setattr(module, "_strict_https_get", forbidden_transport)

    result = module.main(["--url", "https://device.example.ts.net/butlers-dev-api/api/health"])

    assert result == 27
    assert "identity-same-host" in capsys.readouterr().err
    assert transport_called is False


@pytest.mark.parametrize("identity", [None, ""])
def test_probe_requires_readable_executor_tailscale_identity(monkeypatch, capsys, identity) -> None:
    module = _probe_module()
    monkeypatch.setattr(module, "_tailscale_self_dns_name", lambda: identity)

    result = module.main(["--url", "https://device.example.ts.net/butlers-dev-api/api/health"])

    assert result == 27
    assert "identity-unavailable" in capsys.readouterr().err


def test_probe_attests_distinct_executor_identity_before_success(monkeypatch, capsys) -> None:
    module = _probe_module()
    monkeypatch.setattr(module, "_tailscale_self_dns_name", lambda: "verifier.example.ts.net")
    monkeypatch.setattr(
        module,
        "_strict_https_get",
        lambda _url, _timeout: module.ProbeResponse(200, b'{"status":"ok"}'),
    )

    result = module.main(["--url", "https://device.example.ts.net/butlers-dev-api/api/health"])

    assert result == 0
    assert "TAILSCALE_SERVE_PROBE_IDENTITY=verified-distinct" in capsys.readouterr().out


def test_failure_message_is_actionable_without_echoing_response_body() -> None:
    module = _probe_module()
    result = module.ProbeResult(
        outcome=module.ProbeOutcome.ROUTE_404,
        attempts=1,
        status_code=404,
    )

    message = module.format_failure(
        result,
        "https://device.example.ts.net/butlers-dev-api/api/health",
    )

    assert "route-404" in message
    assert "mapping" in message.lower()
    assert "butlers-dev-api/api/health" in message
    assert "not found" not in message.lower()


def _write_executable(path: Path, content: str) -> None:
    path.write_text("#!/usr/bin/env bash\n" + dedent(content), encoding="utf-8")
    path.chmod(0o755)


def _launcher_harness(
    tmp_path: Path,
    *,
    probe_context: str | None = "off-host",
    probe_exit: int = 0,
    omit_api_mapping: bool = False,
    probe_command: bool = True,
    actual_probe: bool = False,
    probe_attests: bool = True,
    probe_hangs: bool = False,
    serve_status_mode: str = "valid",
    tailscale_dns_name: object = "device.example.ts.net",
    extra_environment: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """Run compose.sh against fake commands; never contacts Docker or Tailscale."""

    repo = tmp_path / "launcher-repo"
    scripts = repo / "scripts"
    scripts.mkdir(parents=True)
    compose = Path("scripts/compose.sh")
    scripts.joinpath("compose.sh").write_text(compose.read_text(encoding="utf-8"), encoding="utf-8")
    scripts.joinpath("compose.sh").chmod(0o755)
    probe_script = Path("scripts/tailscale_serve_probe.py")
    scripts.joinpath("tailscale_serve_probe.py").write_text(
        probe_script.read_text(encoding="utf-8"), encoding="utf-8"
    )
    base_fingerprint = Path("scripts/base-image-input-fingerprint.sh")
    scripts.joinpath("base-image-input-fingerprint.sh").write_text(
        base_fingerprint.read_text(encoding="utf-8"), encoding="utf-8"
    )
    for relative in (
        "Dockerfile.base",
        "scripts/runtime_cli_sandbox_init.c",
        "scripts/generate_runtime_cli_sandbox_manifest.py",
    ):
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("test-only input\n", encoding="utf-8")
    (repo / ".env.dev").write_text(
        "POSTGRES_HOST=127.0.0.1\nPOSTGRES_PASSWORD=test-only\n", encoding="utf-8"
    )

    calls = tmp_path / "calls.log"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    handlers: object = {
        "/butlers-dev": {"Proxy": "http://localhost:42173/butlers-dev"},
        "/owntracks-dev": {"Proxy": "http://localhost:42086/owntracks"},
    }
    if not omit_api_mapping:
        handlers["/butlers-dev-api"] = {"Proxy": "http://localhost:42200"}
    host_config: object = {"Handlers": handlers}
    if serve_status_mode == "host-array":
        host_config = []
    elif serve_status_mode == "host-scalar":
        host_config = "invalid"
    elif serve_status_mode == "handlers-array":
        host_config = {"Handlers": []}
    elif serve_status_mode == "handlers-scalar":
        host_config = {"Handlers": "invalid"}
    serve_status = {"Web": {"device.example.ts.net:443": host_config}}
    tailscale_status = {"BackendState": "Running", "Self": {"DNSName": tailscale_dns_name}}
    _write_executable(
        fake_bin / "tailscale",
        f"""
        # This fixture provides only read-only status output and never invokes a
        # real tailscale binary.
        if [[ "$*" == "status --json" ]]; then
          printf '%s\\n' '{json.dumps(tailscale_status)}'
        elif [[ "$*" == "serve status --json" && "{serve_status_mode}" == "failure" ]]; then
          exit 7
        elif [[ "$*" == "serve status --json" && "{serve_status_mode}" == "malformed" ]]; then
          printf '%s\\n' 'not-json'
        elif [[ "$*" == "serve status --json" ]]; then
          printf '%s\\n' '{json.dumps(serve_status)}'
        else
          printf 'fake tailscale command: %s\\n' "$*" >> "$LAUNCHER_CALLS"
        fi
        """,
    )
    _write_executable(
        fake_bin / "docker",
        """
        printf 'docker %s\\n' "$*" >> "$LAUNCHER_CALLS"
        exit 0
        """,
    )
    _write_executable(
        fake_bin / "bd",
        """
        printf 'bd %s\\n' "$*" >> "$LAUNCHER_CALLS"
        exit 0
        """,
    )
    _write_executable(
        fake_bin / "sudo",
        """
        printf 'sudo %s\\n' "$*" >> "$LAUNCHER_CALLS"
        if [[ "$*" == "-n true" ]]; then exit 1; fi
        exit 0
        """,
    )
    _write_executable(
        fake_bin / "git",
        """
        if [[ "$1" == "rev-parse" ]]; then printf 'test-sha\\n'; fi
        """,
    )
    probe = fake_bin / "probe"
    _write_executable(
        probe,
        f"""
        printf 'probe %s\\n' "$*" >> "$LAUNCHER_CALLS"
        {"printf '%s\\n' 'TAILSCALE_SERVE_PROBE_IDENTITY=verified-distinct'" if probe_attests else ":"}
        {"sleep 30" if probe_hangs else ":"}
        exit {probe_exit}
        """,
    )

    environment = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "LAUNCHER_CALLS": str(calls),
    }
    if probe_command:
        environment["TAILSCALE_SERVE_PROBE_COMMAND"] = (
            f"{sys.executable} {scripts / 'tailscale_serve_probe.py'}"
            if actual_probe
            else str(probe)
        )
    else:
        environment.pop("TAILSCALE_SERVE_PROBE_COMMAND", None)
    if probe_context is not None:
        environment["TAILSCALE_SERVE_PROBE_CONTEXT"] = probe_context
    if extra_environment:
        environment.update(extra_environment)
    completed = subprocess.run(
        ["bash", str(scripts / "compose.sh"), "--skip-oauth-check"],
        cwd=repo,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    return completed, calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []


def test_launcher_runs_off_host_probe_after_compose_up(tmp_path: Path) -> None:
    completed, calls = _launcher_harness(tmp_path)

    assert completed.returncode == 0, completed.stderr
    up_index = next(i for i, call in enumerate(calls) if "compose" in call and " up -d" in call)
    probe_index = next(i for i, call in enumerate(calls) if call.startswith("probe "))
    firewall_check_index = next(i for i, call in enumerate(calls) if call == "sudo -n true")
    assert probe_index > up_index
    assert probe_index > firewall_check_index
    assert not any(call.startswith("fake tailscale ") for call in calls[probe_index + 1 :])
    probe_call = calls[probe_index]
    assert "https://device.example.ts.net/butlers-dev-api/api/health" in probe_call
    assert "--timeout 10" in probe_call
    assert "--retries 2" in probe_call


def test_launcher_refuses_on_host_probe_context(tmp_path: Path) -> None:
    completed, calls = _launcher_harness(tmp_path, probe_context="on-host")

    assert completed.returncode != 0
    assert "off-host" in completed.stderr.lower()
    assert not any(call.startswith("probe ") for call in calls)


@pytest.mark.parametrize("tailscale_dns_name", [None, "", "not a usable hostname"])
def test_launcher_refuses_configured_probe_without_usable_data_plane_target(
    tmp_path: Path,
    tailscale_dns_name: object,
) -> None:
    completed, calls = _launcher_harness(tmp_path, tailscale_dns_name=tailscale_dns_name)

    assert completed.returncode != 0
    assert "data-plane target-unavailable" in completed.stderr
    assert "Self.DNSName" in completed.stderr
    assert not any("compose" in call and " up -d" in call for call in calls)
    assert not any(call.startswith("probe ") for call in calls)
    assert not any(call.startswith("fake tailscale ") for call in calls)


def test_caller_asserted_off_host_context_cannot_bless_same_host_executor(tmp_path: Path) -> None:
    completed, calls = _launcher_harness(
        tmp_path,
        probe_context="off-host",
        probe_attests=False,
    )

    assert completed.returncode != 0
    assert "identity-unverified" in completed.stderr
    assert any("compose" in call and " up -d" in call for call in calls)


def test_actual_probe_rejects_same_host_executor_despite_context_label(tmp_path: Path) -> None:
    completed, calls = _launcher_harness(tmp_path, probe_context="off-host", actual_probe=True)

    assert completed.returncode != 0
    assert "identity-same-host" in completed.stderr
    assert any("compose" in call and " up -d" in call for call in calls)


def test_launcher_bounds_hung_executor_with_outer_deadline(tmp_path: Path) -> None:
    completed, calls = _launcher_harness(
        tmp_path,
        probe_hangs=True,
        extra_environment={
            "TAILSCALE_SERVE_PROBE_TIMEOUT_SECONDS": "0.1",
            "TAILSCALE_SERVE_PROBE_RETRIES": "0",
            "TAILSCALE_SERVE_PROBE_RETRY_DELAY_SECONDS": "0",
        },
    )

    assert completed.returncode != 0
    assert "executor-timeout" in completed.stderr
    assert any(call.startswith("probe ") for call in calls)


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("TAILSCALE_SERVE_PROBE_TIMEOUT_SECONDS", "NaN"),
        ("TAILSCALE_SERVE_PROBE_TIMEOUT_SECONDS", "31"),
        ("TAILSCALE_SERVE_PROBE_RETRIES", "999999"),
        ("TAILSCALE_SERVE_PROBE_RETRY_DELAY_SECONDS", "inf"),
    ],
)
def test_launcher_rejects_invalid_probe_settings_before_mutation(
    tmp_path: Path,
    variable: str,
    value: str,
) -> None:
    completed, calls = _launcher_harness(
        tmp_path,
        extra_environment={variable: value},
    )

    assert completed.returncode != 0
    assert "invalid Tailscale Serve probe settings" in completed.stderr
    assert not any(call.startswith("fake tailscale ") for call in calls)
    assert not any("compose" in call for call in calls)


def test_launcher_makes_missing_data_plane_probe_explicit(tmp_path: Path) -> None:
    completed, calls = _launcher_harness(tmp_path, probe_command=False, probe_context=None)

    assert completed.returncode == 0, completed.stderr
    assert "data-plane probe deferred" in completed.stdout
    assert "control-plane mappings only" in completed.stdout
    assert not any(call.startswith("probe ") for call in calls)


@pytest.mark.parametrize(
    ("probe_exit", "failure_class"),
    [
        (20, "cert-invalid"),
        (21, "route-404"),
        (22, "timeout"),
    ],
)
def test_launcher_surfaces_data_plane_failure_class(
    tmp_path: Path,
    probe_exit: int,
    failure_class: str,
) -> None:
    completed, calls = _launcher_harness(tmp_path, probe_exit=probe_exit)

    assert completed.returncode != 0
    assert failure_class in completed.stderr
    assert any(call.startswith("probe ") for call in calls)


def test_launcher_distinguishes_mapping_missing_after_apply(tmp_path: Path) -> None:
    completed, calls = _launcher_harness(tmp_path, omit_api_mapping=True)

    assert completed.returncode != 0
    assert "mapping-missing" in completed.stderr
    assert "butlers-dev-api" in completed.stderr
    assert not any(call.startswith("probe ") for call in calls)


@pytest.mark.parametrize(
    ("serve_status_mode", "failure_class"),
    [("failure", "status-unreadable"), ("malformed", "status-malformed")],
)
def test_launcher_distinguishes_unreadable_and_malformed_serve_status(
    tmp_path: Path,
    serve_status_mode: str,
    failure_class: str,
) -> None:
    completed, calls = _launcher_harness(tmp_path, serve_status_mode=serve_status_mode)

    assert completed.returncode != 0
    assert failure_class in completed.stderr
    assert "mapping-missing" not in completed.stderr
    assert not any(call.startswith("fake tailscale ") for call in calls)
    assert not any(call.startswith("probe ") for call in calls)


@pytest.mark.parametrize(
    "serve_status_mode",
    ["host-array", "host-scalar", "handlers-array", "handlers-scalar"],
)
def test_launcher_rejects_malformed_nested_serve_status_as_unreadable(
    tmp_path: Path,
    serve_status_mode: str,
) -> None:
    completed, calls = _launcher_harness(tmp_path, serve_status_mode=serve_status_mode)

    assert completed.returncode != 0
    assert "status-malformed" in completed.stderr
    assert "mapping-missing" not in completed.stderr
    assert not any(call.startswith("fake tailscale ") for call in calls)
    assert not any(call.startswith("probe ") for call in calls)
