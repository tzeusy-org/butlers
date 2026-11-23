"""bu-ejow5p.11: the in-pod isolation probe is content-blind and its checks bite."""

from __future__ import annotations

import json
import os
import socket

import pytest

from butlers.core.qa import isolation_probe as probe

pytestmark = pytest.mark.unit


def _args(*argv: str):
    return probe._parser().parse_args(list(argv))


def _by_name(results):
    return {r.name: r for r in results}


def test_a_listening_port_fails_the_refused_check_and_passes_the_reachable_control() -> None:
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    try:
        results = _by_name(
            probe.run_checks(
                _args(
                    "--refused-tcp",
                    f"canary=127.0.0.1:{port}",
                    "--reachable-tcp",
                    f"proxy=127.0.0.1:{port}",
                )
            )
        )
    finally:
        server.close()
    assert results["tcp_refused:canary"].passed is False
    assert results["tcp_reachable:proxy"].passed is True


def test_a_closed_port_passes_refused_and_fails_reachable() -> None:
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    port = server.getsockname()[1]
    server.close()
    results = _by_name(
        probe.run_checks(
            _args(
                "--refused-tcp",
                f"canary=127.0.0.1:{port}",
                "--reachable-tcp",
                f"proxy=127.0.0.1:{port}",
            )
        )
    )
    assert results["tcp_refused:canary"].passed is True
    assert results["tcp_reachable:proxy"].passed is False


def test_a_readable_forbidden_path_fails_and_workspace_control_follows_writability(
    tmp_path,
) -> None:
    secret = tmp_path / "canary"
    secret.write_text("SYNTHETIC-CANARY-VALUE")
    results = probe.run_checks(
        _args(
            "--forbidden-path",
            str(secret),
            "--readable-path",
            str(secret),
            "--workspace",
            str(tmp_path),
        )
    )
    named = [r for r in results if r.name.startswith("forbidden_path_unreadable")]
    assert [r.passed for r in named] == [False]
    assert _by_name(results)["workspace_writable"].passed is True
    receipt = json.dumps(probe.receipt(results))
    assert "SYNTHETIC-CANARY-VALUE" not in receipt
    assert str(secret) not in receipt


def test_a_present_forbidden_env_name_fails(monkeypatch) -> None:
    monkeypatch.setenv("CANARY_PRESENT", "synthetic-value")
    results = _by_name(
        probe.run_checks(
            _args("--forbidden-env", "CANARY_PRESENT", "--forbidden-env", "CANARY_ABSENT_X")
        )
    )
    assert results["forbidden_env_absent:CANARY_PRESENT"].passed is False
    assert results["forbidden_env_absent:CANARY_ABSENT_X"].passed is True
    assert "synthetic-value" not in json.dumps(probe.receipt(list(results.values())))


def test_the_uid_check_fails_unless_the_expected_non_root_uid() -> None:
    results = _by_name(probe.run_checks(_args("--expected-uid", str(os.getuid() + 1))))
    assert results["uid_not_root"].passed is False


def test_a_raising_check_is_recorded_not_propagated() -> None:
    results = _by_name(probe.run_checks(_args("--refused-tcp", "bad=nonsense")))
    assert results["tcp_refused:bad"].passed is False
    assert results["tcp_refused:bad"].category == "ValueError"


def test_the_receipt_digest_is_stable_and_pass_is_all_or_nothing() -> None:
    a = probe.CheckResult("x", probe.NEGATIVE, True)
    b = probe.CheckResult("y", probe.POSITIVE, False)
    assert probe.receipt([a])["passed"] is True
    assert probe.receipt([a, b])["passed"] is False
    assert probe.receipt([a, b])["digest"] == probe.receipt([a, b])["digest"]
    assert probe.receipt([a])["digest"] != probe.receipt([a, b])["digest"]
