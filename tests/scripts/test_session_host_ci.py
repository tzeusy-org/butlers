"""Offline policy/state controls; fake host operations earn no native credit."""

import hashlib
import io
import subprocess
import tarfile
from pathlib import Path

import pytest

from scripts import session_host_ci as ci


@pytest.fixture
def host(monkeypatch, tmp_path):
    package = b"synthetic-public-package"
    profile = b"x" * 1936
    tar = io.BytesIO()
    with tarfile.open(fileobj=tar, mode="w") as files:
        member = tarfile.TarInfo(ci.PROFILE_MEMBER)
        member.size = len(profile)
        files.addfile(member, io.BytesIO(profile))
    monkeypatch.setattr(ci, "PACKAGE_SHA256", hashlib.sha256(package).hexdigest())
    monkeypatch.setattr(ci, "PROFILE_SHA256", hashlib.sha256(profile).hexdigest())
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
                state["names"] = {"bwrap", "unpriv_bwrap"}
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
    assert ci.PROFILE_MEMBER == "./usr/share/apparmor/extra-profiles/bwrap-userns-restrict"


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
