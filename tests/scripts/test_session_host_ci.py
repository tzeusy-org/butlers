"""Offline policy/state controls; fake host operations earn no native credit."""

import hashlib
import io
import json
import subprocess
import tarfile
from pathlib import Path

import pytest

from scripts import session_host_ci as ci


@pytest.fixture
def host(monkeypatch, tmp_path):
    package = b"synthetic-public-package"
    profile = (
        b"profile bwrap /usr/bin/bwrap flags=(attach_disconnected) {\n}\n"
        b"profile unpriv_bwrap flags=(attach_disconnected) {\n  audit deny capability,\n}\n#"
    )
    profile += b"x" * (1936 - len(profile))
    derived = profile.replace(
        b"flags=(attach_disconnected)", b"flags=(attach_disconnected,mediate_deleted)"
    )
    tar = io.BytesIO()
    with tarfile.open(fileobj=tar, mode="w") as files:
        member = tarfile.TarInfo(ci.PROFILE_MEMBER)
        member.size = len(profile)
        files.addfile(member, io.BytesIO(profile))
    monkeypatch.setattr(ci, "PACKAGE_SHA256", hashlib.sha256(package).hexdigest())
    monkeypatch.setattr(ci, "PROFILE_SHA256", hashlib.sha256(profile).hexdigest())
    monkeypatch.setattr(ci, "RUNNER_PROFILE_SHA256", hashlib.sha256(derived).hexdigest())
    text = Path.read_text
    exists = Path.exists
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda path, *a, **kw: (
            'VERSION_ID="24.04"' if str(path) == "/etc/os-release" else text(path, *a, **kw)
        ),
    )
    monkeypatch.setattr(
        Path,
        "exists",
        lambda path: False if str(path).startswith("/etc/apparmor.d/local/") else exists(path),
    )
    monkeypatch.setattr(ci.urllib.request, "urlopen", lambda *a, **kw: io.BytesIO(package))
    state = dict(
        restriction=b"1",
        names=set(),
        generic_exit=1,
        before=b"bwrap: setting up uid map: Permission denied",
        after=31,
        load_exit=0,
        load_names={"bwrap", "unpriv_bwrap"},
        remove_exit=0,
        calls=[],
    )

    def run(args, **kwargs):
        state["calls"].append(args)
        code, out, err = 0, b"", b""
        if args[:2] == ["sysctl", "-n"]:
            out = state["restriction"]
        elif args[:3] == ["sudo", "-n", "cat"]:
            out = "\n".join(f"{name} (enforce)" for name in state["names"]).encode()
        elif args[0] == "unshare":
            code, err = state["generic_exit"], b"unshare: unshare failed: Operation not permitted"
        elif args[0] == "dpkg-deb":
            out = tar.getvalue()
        elif args[:4] == ["sudo", "-n", "apparmor_parser", "-a"]:
            code = state["load_exit"]
            if not code:
                state["names"] = set(state["load_names"])
        elif args[:4] == ["sudo", "-n", "apparmor_parser", "-R"]:
            code = state["remove_exit"]
            if not code:
                state["names"] = set()
        else:
            raise AssertionError("unexpected host operation")
        return subprocess.CompletedProcess(args, code, out, err)

    def preflight(path):
        if path.name == "before":
            return subprocess.CompletedProcess([], 1, b"", state["before"])
        return subprocess.CompletedProcess([], state["after"], b"", b"")

    monkeypatch.setattr(ci, "run", run)
    monkeypatch.setattr(ci, "repeat_preflight", preflight)
    return state, tmp_path / "experiment", package, tar.getvalue()


def test_scoped_positive_keeps_global_restriction_and_cleans_both_profiles(host, capsys):
    state, root, _, _ = host
    ci.prepare(root)
    assert state["names"] == {"bwrap", "unpriv_bwrap"}
    assert state["restriction"] == b"1"
    assert sum(args[0] == "unshare" for args in state["calls"]) == 2
    ci.cleanup(root)
    assert state["names"] == set()
    output = capsys.readouterr().out
    assert "scoped_profile_proven" in output and "scoped_profile_removed" in output
    assert not any("-w" in args or "-r" in args for args in state["calls"])


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("restriction", b"0", "global_restriction_not_one"),
        ("names", {"foreign-bwrap"}, "preloaded_profile_conflict"),
        ("generic_exit", 0, "generic_userns_not_denied"),
        ("before", b"PRIVATE unknown failure", "unattributed_native_preflight"),
    ],
)
def test_unsafe_or_unknown_host_never_loads_policy(host, field, value, reason):
    state, root, _, _ = host
    state[field] = value
    with pytest.raises(ci.Refusal, match=reason):
        ci.prepare(root)
    assert not any("apparmor_parser" in args for args in state["calls"])
    ci.cleanup(root)


def test_unknown_postload_failure_is_not_pass_and_owned_policy_can_be_removed(host):
    state, root, _, _ = host
    state["after"] = 1
    with pytest.raises(ci.Refusal, match="native_preflight_still_refused"):
        ci.prepare(root)
    assert state["names"] == {"bwrap", "unpriv_bwrap"}
    ci.cleanup(root)
    assert not state["names"]


def test_failed_load_refuses_and_never_removes_uncertain_foreign_policy(host):
    state, root, _, _ = host
    state["load_exit"] = 1
    with pytest.raises(ci.Refusal, match="profile_load_unproven"):
        ci.prepare(root)
    with pytest.raises(ci.Refusal, match="cleanup_load_ownership_unproven"):
        ci.cleanup(root)
    assert not any("-R" in args for args in state["calls"])


def test_cleanup_failure_is_not_reported_as_removed(host, capsys):
    state, root, _, _ = host
    ci.prepare(root)
    capsys.readouterr()
    state["remove_exit"] = 1
    with pytest.raises(ci.Refusal, match="host_command_refused"):
        ci.cleanup(root)
    assert "scoped_profile_removed" not in capsys.readouterr().out


@pytest.mark.parametrize("mutation", ["package", "profile", "link"])
def test_package_and_member_integrity_have_no_unverified_fallback(host, mutation):
    _, _, package, archive = host
    if mutation == "package":
        package += b"tampered"
    else:
        content = io.BytesIO()
        with tarfile.open(fileobj=content, mode="w") as files:
            member = tarfile.TarInfo(ci.PROFILE_MEMBER)
            if mutation == "link":
                member.type = tarfile.SYMTYPE
                member.linkname = "/etc/foreign"
                files.addfile(member)
            else:
                member.size = 1936
                files.addfile(member, io.BytesIO(b"y" * 1936))
        archive = content.getvalue()
    with pytest.raises(ci.Refusal):
        ci.verified_profile(package, archive)


def test_published_noble_hashes_and_no_runtime_credential_dependency():
    assert ci.PACKAGE_SHA256 == "90b02aa006eea7702cd4e851343e469e41365dda42145a3cb035de1d6c773b8c"
    assert ci.PROFILE_SHA256 == "11d39094f044f0cda0febb3ad517b830301da6b2ce929664af09ee9e4dd264f9"
    assert ci.RUNNER_PROFILE_SHA256 == (
        "a964037f6cf0df1099f14226b037eaedde6237c86e715188e93eb460b30be859"
    )
    assert ci.PROFILE_MEMBER == "./usr/share/apparmor/extra-profiles/bwrap-userns-restrict"


def test_runner_transform_changes_only_both_named_flags_and_binds_load_cleanup(host):
    state, root, package, archive = host
    original = ci.verified_profile(package, archive)
    expected = original.replace(
        b"flags=(attach_disconnected)", b"flags=(attach_disconnected,mediate_deleted)"
    )
    assert len(expected) == 1968
    assert ci.runner_profile(original) == expected
    assert expected.replace(b",mediate_deleted", b"") == original
    assert b"audit deny capability," in expected
    ci.prepare(root)
    assert (root / "bwrap-userns-restrict").read_bytes() == expected
    receipt = {
        "version": 2,
        "upstream_profile": hashlib.sha256(original).hexdigest(),
        "runner_profile": hashlib.sha256(expected).hexdigest(),
    }
    for name in ("load-intent.json", "loaded.json"):
        assert json.loads((root / name).read_bytes()) == receipt
    ci.cleanup(root)
    assert not state["names"]
    assert sum(args[0] == "unshare" for args in state["calls"]) == 3


@pytest.mark.parametrize("mutation", ["changed", "short", "already_transformed"])
def test_runner_transform_never_accepts_unpinned_input(host, mutation):
    _, _, package, archive = host
    original = ci.verified_profile(package, archive)
    candidates = {
        "changed": original[:-1] + b"y",
        "short": original[:-1],
        "already_transformed": original.replace(
            b"attach_disconnected", b"attach_disconnected,mediate_deleted"
        ),
    }
    with pytest.raises(ci.Refusal, match="^profile_digest_mismatch$"):
        ci.runner_profile(candidates[mutation])


@pytest.mark.parametrize("header", [b"bwrap /usr/bin/bwrap", b"unpriv_bwrap"])
@pytest.mark.parametrize("mutation", ["missing", "duplicate"])
def test_runner_transform_requires_each_exact_named_header(host, monkeypatch, header, mutation):
    _, _, package, archive = host
    original = ci.verified_profile(package, archive)
    if mutation == "missing":
        altered = original.replace(header, b"x" * len(header))
    else:
        extra = b"profile " + header + b" flags=(attach_disconnected) {"
        altered = original[: -len(extra)] + extra
    monkeypatch.setattr(ci, "PROFILE_SHA256", hashlib.sha256(altered).hexdigest())
    with pytest.raises(ci.Refusal, match="^profile_header_mismatch$"):
        ci.runner_profile(altered)


def test_wrong_derived_pin_refuses_before_loading_policy(host, monkeypatch):
    state, root, _, _ = host
    monkeypatch.setattr(ci, "RUNNER_PROFILE_SHA256", "0" * 64)
    with pytest.raises(ci.Refusal, match="^runner_profile_digest_mismatch$"):
        ci.prepare(root)
    assert not any("apparmor_parser" in args for args in state["calls"])
    ci.cleanup(root)


def test_cleanup_original_member_is_not_the_owned_runner_variant(host):
    state, root, package, archive = host
    ci.prepare(root)
    (root / "bwrap-userns-restrict").write_bytes(ci.verified_profile(package, archive))
    with pytest.raises(ci.Refusal, match="^cleanup_profile_identity_refused$"):
        ci.cleanup(root)
    assert state["names"] == {"bwrap", "unpriv_bwrap"}
    assert not any("-R" in args for args in state["calls"])


def test_cleanup_does_not_claim_success_if_generic_userns_became_allowed(host, capsys):
    state, root, _, _ = host
    ci.prepare(root)
    capsys.readouterr()
    state["generic_exit"] = 0
    with pytest.raises(ci.Refusal, match="^generic_userns_not_denied$"):
        ci.cleanup(root)
    assert not state["names"]
    assert "scoped_profile_removed" not in capsys.readouterr().out


@pytest.mark.parametrize("name", ["load-intent.json", "loaded.json"])
@pytest.mark.parametrize("mutation", ["missing", "old_variant", "wrong_pin", "mode", "symlink"])
def test_cleanup_requires_both_owned_exact_variant_receipts(host, name, mutation):
    state, root, _, _ = host
    ci.prepare(root)
    path = root / name
    if mutation == "old_variant":
        path.write_bytes(b'{"version":1}')
    elif mutation == "wrong_pin":
        path.write_bytes(path.read_bytes().replace(ci.RUNNER_PROFILE_SHA256.encode(), b"0" * 64))
    elif mutation == "mode":
        path.chmod(0o644)
    else:
        path.unlink()
        if mutation == "symlink":
            path.symlink_to(root / "profile.deb")
    reason = (
        "cleanup_load_identity_refused"
        if name == "load-intent.json"
        else "cleanup_load_ownership_unproven"
    )
    with pytest.raises(ci.Refusal, match=f"^{reason}$"):
        ci.cleanup(root)
    assert state["names"] == {"bwrap", "unpriv_bwrap"}
    assert not any("-R" in args for args in state["calls"])


def test_script_refuses_non_hosted_execution(monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_ACTIONS", "false")
    monkeypatch.setattr("sys.argv", ["session_host_ci", "prepare"])
    assert ci.main() == 1
    assert "hosted_runner_required" in capsys.readouterr().out


@pytest.mark.parametrize("name", ["bwrap-userns-restrict", "unpriv_bwrap"])
def test_existing_local_policy_include_never_loads_unreviewed_rules(host, monkeypatch, name):
    state, root, _, _ = host
    exists = Path.exists
    monkeypatch.setattr(
        Path,
        "exists",
        lambda path: True if str(path) == f"/etc/apparmor.d/local/{name}" else exists(path),
    )
    with pytest.raises(ci.Refusal, match="local_profile_override_conflict"):
        ci.prepare(root)
    assert not any("apparmor_parser" in args for args in state["calls"])


def test_cleanup_tampered_profile_never_removes_policy(host):
    state, root, _, _ = host
    ci.prepare(root)
    (root / "bwrap-userns-restrict").write_bytes(b"tampered")
    with pytest.raises(ci.Refusal, match="cleanup_profile_identity_refused"):
        ci.cleanup(root)
    assert state["names"] == {"bwrap", "unpriv_bwrap"}
    assert not any("-R" in args for args in state["calls"])


@pytest.mark.parametrize("names", [set(), {"bwrap"}, {"unpriv_bwrap"}])
def test_parser_success_without_complete_observation_never_authorizes_cleanup(host, names):
    state, root, _, _ = host
    state["load_names"] = names
    with pytest.raises(ci.Refusal, match="profile_load_unobserved"):
        ci.prepare(root)
    assert not (root / "loaded.json").exists()
    with pytest.raises(ci.Refusal, match="cleanup_load_ownership_unproven"):
        ci.cleanup(root)
    assert not any("-R" in args for args in state["calls"])
    assert state["names"] == names


@pytest.mark.parametrize("names", [set(), {"bwrap"}, {"unpriv_bwrap"}])
def test_lost_owned_profile_observation_refuses_cleanup(host, names):
    state, root, _, _ = host
    ci.prepare(root)
    state["names"] = names
    with pytest.raises(ci.Refusal, match="cleanup_owned_profiles_unobserved"):
        ci.cleanup(root)
    assert not any("-R" in args for args in state["calls"])
    assert state["names"] == names


def test_unknown_synthetic_preflight_emits_bounded_sanitized_diagnostic_and_refuses(host, capsys):
    state, root, _, _ = host
    state["before"] = b"synthetic denial\x1b\n\xff" + b"x" * 700
    with pytest.raises(ci.Refusal, match="unattributed_native_preflight"):
        ci.prepare(root)
    entry = json.loads(capsys.readouterr().out)
    assert "stderr_preview" in entry
    assert entry["stderr_preview"] == "synthetic denial???" + "x" * 493
    assert len(entry["stderr_preview"]) == 512
    assert entry["stderr_sha256"] == hashlib.sha256(state["before"]).hexdigest()
    assert entry["category"] == "unknown_preflight_failure"
    assert not any("apparmor_parser" in args for args in state["calls"])
    assert not (root / "loaded.json").exists()
    ci.cleanup(root)
    assert not any("-R" in args for args in state["calls"])


_LOOPBACK_REFUSAL = b"bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted\n"
_LOOPBACK_DIGEST = "ed3471f7900377f86150471417911309833a36d664cb5af7409e139103f67ddf"


@pytest.mark.parametrize("after", [31, 1])
def test_exact_loopback_refusal_allows_only_bounded_experiment(host, capsys, after):
    state, root, _, _ = host
    assert hashlib.sha256(_LOOPBACK_REFUSAL).hexdigest() == _LOOPBACK_DIGEST
    assert ci.synthetic_preflight_category(_LOOPBACK_REFUSAL) == "loopback_address_denied"
    # Runtime fixture classification is unchanged; new admission is synthetic-only.
    assert ci.failure_category(_LOOPBACK_REFUSAL) == "unknown_preflight_failure"
    state["before"] = _LOOPBACK_REFUSAL
    state["after"] = after
    if after == 31:
        ci.prepare(root)
    else:
        with pytest.raises(ci.Refusal, match="native_preflight_still_refused"):
            ci.prepare(root)
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert records[0]["category"] == "loopback_address_denied"
    assert records[0]["stderr_sha256"] == _LOOPBACK_DIGEST
    assert any(record.get("status") == "scoped_profile_proven" for record in records) == (
        after == 31
    )
    assert state["restriction"] == b"1"
    assert sum(args[0] == "unshare" for args in state["calls"]) == 2
    assert state["names"] == {"bwrap", "unpriv_bwrap"}
    ci.cleanup(root)
    assert not state["names"]
    assert state["restriction"] == b"1"


@pytest.mark.parametrize(
    "stderr",
    [
        _LOOPBACK_REFUSAL.rstrip(b"\n"),
        _LOOPBACK_REFUSAL + b"extra\n",
        b"warning\n" + _LOOPBACK_REFUSAL,
        _LOOPBACK_REFUSAL.replace(b"\n", b"\r\n"),
        _LOOPBACK_REFUSAL.replace(b"Operation not permitted", b"Permission denied"),
        _LOOPBACK_REFUSAL.replace(b"loopback:", b"loopback: "),
        _LOOPBACK_REFUSAL.replace(b"Failed", b"failed"),
        _LOOPBACK_REFUSAL.replace(b"\n", b"\x00\n"),
    ],
)
def test_near_miss_loopback_refusal_never_loads_policy(host, stderr):
    state, root, _, _ = host
    assert ci.synthetic_preflight_category(stderr) == "unknown_preflight_failure"
    state["before"] = stderr
    with pytest.raises(ci.Refusal, match="unattributed_native_preflight"):
        ci.prepare(root)
    ci.cleanup(root)
    assert not any("apparmor_parser" in args for args in state["calls"])


def _flaky_download(package, failures, error):
    attempts = []

    def urlopen(*args, **kwargs):
        attempts.append(kwargs.get("timeout"))
        if len(attempts) <= failures:
            raise error
        return io.BytesIO(package)

    return urlopen, attempts


@pytest.mark.parametrize("error", [TimeoutError("timed out"), ConnectionResetError("reset")])
def test_a_transient_download_fault_is_retried_and_the_digest_is_still_checked(
    host, monkeypatch, error
):
    state, root, package, _ = host
    urlopen, attempts = _flaky_download(package, 2, error)
    sleeps = []
    monkeypatch.setattr(ci.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(ci.time, "sleep", sleeps.append)
    ci.prepare(root)
    assert state["names"] == {"bwrap", "unpriv_bwrap"}
    assert attempts == [20, 20, 20] and sleeps == [3, 6]


def test_a_download_that_never_succeeds_is_a_named_refusal_after_three_attempts(
    host, monkeypatch, capsys
):
    state, root, package, _ = host
    urlopen, attempts = _flaky_download(package, 99, TimeoutError("secret detail"))
    monkeypatch.setattr(ci.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(ci.time, "sleep", lambda seconds: None)
    with pytest.raises(ci.Refusal, match="^package_download_failed$"):
        ci.prepare(root)
    assert len(attempts) == 3
    assert not any("apparmor_parser" in args for args in state["calls"])
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv("RUNNER_TEMP", str(root.parent))
    monkeypatch.setattr("sys.argv", ["session_host_ci", "prepare"])
    import shutil

    shutil.rmtree(root, ignore_errors=True)
    assert ci.main() == 1
    output = capsys.readouterr().out
    assert "package_download_failed" in output and "secret detail" not in output


def test_a_wrong_download_is_never_retried_into_acceptance(host, monkeypatch):
    state, root, package, _ = host
    calls = []

    def urlopen(*args, **kwargs):
        calls.append(1)
        return io.BytesIO(package + b"x")

    monkeypatch.setattr(ci.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(ci.time, "sleep", lambda seconds: None)
    with pytest.raises(ci.Refusal, match="package_digest_mismatch"):
        ci.prepare(root)
    assert len(calls) == 1
    assert not any("apparmor_parser" in args for args in state["calls"])


def _http_error(code):
    import urllib.error

    return urllib.error.HTTPError(ci.PACKAGE_URL, code, "secret body", {}, None)


@pytest.mark.parametrize(
    "error", [_http_error(503), ConnectionResetError("reset"), TimeoutError("t")]
)
def test_server_errors_connection_faults_and_timeouts_are_retried(host, monkeypatch, error):
    state, root, package, _ = host
    urlopen, attempts = _flaky_download(package, 1, error)
    monkeypatch.setattr(ci.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(ci.time, "sleep", lambda seconds: None)
    ci.prepare(root)
    assert len(attempts) == 2


@pytest.mark.parametrize("code", [403, 404])
def test_a_client_error_is_a_definite_answer_and_is_never_retried(host, monkeypatch, code):
    state, root, package, _ = host
    urlopen, attempts = _flaky_download(package, 99, _http_error(code))
    monkeypatch.setattr(ci.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(ci.time, "sleep", lambda seconds: pytest.fail("no wait for a 4xx"))
    with pytest.raises(ci.Refusal, match="^package_download_failed$"):
        ci.prepare(root)
    assert len(attempts) == 1


def test_a_profile_load_that_cannot_run_is_named_and_not_retried(host, monkeypatch):
    state, root, _, _ = host
    real = ci.run
    loads = []

    def run(args, **kwargs):
        if args[:4] == ["sudo", "-n", "apparmor_parser", "-a"]:
            loads.append(args)
            raise subprocess.TimeoutExpired(args, 15)
        return real(args, **kwargs)

    monkeypatch.setattr(ci, "run", run)
    with pytest.raises(ci.Refusal, match="^profile_load_failed$"):
        ci.prepare(root)
    assert len(loads) == 1


def test_every_refusal_word_is_in_the_closed_set_and_content_blind():
    import ast
    import re

    source = Path(ci.__file__).read_text()
    literals = {
        node.args[0].value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", None) == "Refusal"
        and node.args
        and isinstance(node.args[0], ast.Constant)
    }
    assert literals and literals <= ci.REFUSALS
    assert "profile_load_failed" in ci.REFUSALS and "package_download_failed" in ci.REFUSALS
    assert all(re.fullmatch(r"[a-z_]+", word) for word in ci.REFUSALS)
    # No Refusal is ever built from a variable, so no URL, status or body can enter one.
    dynamic = [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", None) == "Refusal"
        and not (node.args and isinstance(node.args[0], ast.Constant))
    ]
    assert dynamic == []


def test_an_after_preflight_that_cannot_run_is_named_profile_load_failed(host, monkeypatch):
    state, root, _, _ = host
    real = ci.repeat_preflight

    def preflight(path):
        if path.name == "after":
            raise subprocess.TimeoutExpired(["bwrap"], 15)
        return real(path)

    monkeypatch.setattr(ci, "repeat_preflight", preflight)
    with pytest.raises(ci.Refusal, match="^profile_load_failed$"):
        ci.prepare(root)
