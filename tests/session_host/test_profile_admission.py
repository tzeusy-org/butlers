"""Admission precedes credential staging; TLS peers and credentials are synthetic."""

from unittest.mock import patch

import pytest

from butlers.session_host.contracts import Refused
from butlers.session_host.control import LocalControl, local_command
from butlers.session_host.lifecycle import InvocationOwner
from butlers.session_host.material import InvocationMaterial
from tests.session_host.test_control import Domain, launch

pytest_plugins = ("tests.session_host.test_control",)


async def test_ineligible_profile_refuses_before_staging_with_eligible_companion(tmp_path, pki):
    root = tmp_path / "stage"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    owner = InvocationOwner(root, cleanup_timeout_s=0.2)
    server, client, pin = pki
    domain = Domain()
    selections = []

    def select(runtime, model):
        selections.append((runtime, model))
        if model != "eligible-synthetic":
            raise Refused("runtime_ineligible")
        return domain

    host = LocalControl(
        owner=owner,
        factory=select,
        **server(),
        caller_sha256=pin("runner"),
        server_sha256=pin("server"),
        broker_origin="https://broker.test",
    )
    port = await host.start()
    actual_stage = InvocationMaterial.stage

    async def send(command):
        return await local_command(
            port,
            context=client("runner"),
            server_hostname="host.test",
            server_sha256=pin("server"),
            command=command,
        )

    try:
        with patch.object(InvocationMaterial, "stage", wraps=actual_stage) as stage:
            refused = launch()
            refused["model"] = "unproven-synthetic"
            assert await send(refused) == {"status": "refused"}
            assert stage.call_count == 0
            assert await send(refused) == {"status": "refused"}
            assert stage.call_count == 0
            assert not domain.entered.is_set() and not list(root.iterdir())
            accepted = launch()
            accepted["model"] = "eligible-synthetic"
            assert await send(accepted) == {"status": "completed"}
            assert stage.call_count == 1
            assert domain.entered.is_set() and owner.healthy and not list(root.iterdir())
        assert len(selections) == 2
    finally:
        await host.close()


# This report is synthetic. It proves artifact/subject admission only; there is
# deliberately no real runtime/model proof or installed eligible production tuple.
def synthetic_profile(tmp_path):
    from butlers.session_host.bubblewrap import ImageFile
    from butlers.session_host.profiles import Profile

    return Profile(
        runtime="synthetic-only",
        model="synthetic-model",
        image=(
            ImageFile(tmp_path / "synthetic-init", "/runtime/init", "a" * 64),
            ImageFile(tmp_path / "synthetic-cli", "/runtime/cli", "b" * 64),
        ),
        command=("/runtime/cli", "--synthetic-deny-all"),
    )


def synthetic_report(profile):
    return {
        "version": 1,
        "subject": profile.subject,
        "checks": {
            "filesystem_isolation": "pass",
            "process_isolation": "pass",
            "restricted_egress": "pass",
            "builtin_shell_disabled": "pass",
            "builtin_filesystem_disabled": "pass",
            "builtin_web_disabled": "pass",
            "local_mcp_disabled": "pass",
            "provider_search_disabled": "pass",
            "provider_fetch_disabled": "pass",
            "provider_remote_mcp_disabled": "pass",
            "provider_code_disabled": "pass",
            "broker_only_mcp": "pass",
            "catalog_conformance": "pass",
        },
        "execution_sha256": "c" * 64,
        "artifact_sha256": "d" * 64,
    }


def reviewed(profile, report=None, expected=None):
    import hashlib
    import json

    from butlers.session_host.profiles import ReviewedProfile

    raw = json.dumps(synthetic_report(profile)).encode() if report is None else report
    return ReviewedProfile(
        profile,
        report=raw,
        expected_sha256=hashlib.sha256(raw).hexdigest() if expected is None else expected,
    )


@pytest.mark.parametrize(
    "selection", ["default-empty", "unknown-runtime", "unknown-model", "exact"]
)
async def test_pinned_profile_factory_controls_real_mtls_admission(
    tmp_path, pki, monkeypatch, selection
):
    import json

    from butlers.session_host.bubblewrap import BubblewrapDomain
    from butlers.session_host.profiles import ProfileFactory

    profile = synthetic_profile(tmp_path)
    factory = (
        ProfileFactory() if selection == "default-empty" else ProfileFactory((reviewed(profile),))
    )
    entered = []
    settled = []

    async def synthetic_execute(domain, material):
        # The real profile factory constructed the actual inert backend; no
        # native process is launched by this software admission control.
        entered.append((domain._image, domain._command))
        config = json.loads((material.directory / "mcp.json").read_bytes())
        assert set(config["mcpServers"]) == {"broker"}
        assert config["mcpServers"]["broker"]["url"].startswith("https://broker.test/goals/")
        assert (
            material.directory / "model-credential"
        ).read_bytes() == b"SYNTHETIC_PRIVATE_CREDENTIAL"
        return b"synthetic result"

    async def synthetic_settle(domain):
        settled.append(domain)
        return True

    monkeypatch.setattr(BubblewrapDomain, "execute", synthetic_execute)
    monkeypatch.setattr(BubblewrapDomain, "settle", synthetic_settle)
    root = tmp_path / "stage"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    owner = InvocationOwner(root, cleanup_timeout_s=0.2)
    server, client, pin = pki
    host = LocalControl(
        owner=owner,
        factory=factory,
        **server(),
        caller_sha256=pin("runner"),
        server_sha256=pin("server"),
        broker_origin="https://broker.test",
    )
    port = await host.start()
    command = launch()
    command.update(runtime=profile.runtime, model=profile.model)
    if selection == "unknown-runtime":
        command["runtime"] = "unknown-runtime"
    elif selection == "unknown-model":
        command["model"] = "unknown-model"
    try:
        with patch.object(InvocationMaterial, "stage", wraps=InvocationMaterial.stage) as stage:
            reply = await local_command(
                port,
                context=client("runner"),
                server_hostname="host.test",
                server_sha256=pin("server"),
                command=command,
            )
            if selection == "exact":
                assert reply == {"status": "completed"}
                assert stage.call_count == 1
                assert entered == [(profile.image, profile.command)] and len(settled) == 1
            else:
                assert reply == {"status": "refused"}
                assert stage.call_count == 0 and not entered and not settled
        assert owner.healthy and not list(root.iterdir())
    finally:
        await host.close()


@pytest.mark.parametrize(
    "change",
    [
        "unreviewed",
        "model",
        "image",
        "settings",
        "missing-check",
        "failed-check",
        "extra-check",
        "bool-version",
        "extra-subject",
        "duplicate-key",
        "nonfinite",
        "over-cap",
    ],
)
def test_profile_requires_exact_reviewed_artifact(tmp_path, change):
    import json
    from dataclasses import replace

    profile = synthetic_profile(tmp_path)
    accepted = reviewed(profile)
    assert accepted.profile == profile
    data = synthetic_report(profile)
    expected = None
    if change == "unreviewed":
        expected = "0" * 64
    elif change == "model":
        profile = replace(profile, model="another-model")
    elif change == "image":
        profile = replace(
            profile, image=(replace(profile.image[0], sha256="f" * 64), profile.image[1])
        )
    elif change == "settings":
        profile = replace(profile, command=(*profile.command, "--enable-untrusted-tool"))
    elif change == "missing-check":
        del data["checks"]["builtin_shell_disabled"]
    elif change == "failed-check":
        data["checks"]["provider_search_disabled"] = "unknown"
    elif change == "extra-check":
        data["checks"]["caller_says_safe"] = "pass"
    elif change == "bool-version":
        data["version"] = True
    elif change == "extra-subject":
        data["subject"]["caller_says_safe"] = True
    raw = json.dumps(data).encode()
    if change == "duplicate-key":
        raw = raw.replace(b'{"version": 1,', b'{"version": 1, "version": 1,', 1)
    elif change == "nonfinite":
        raw = raw.replace(b'"version": 1', b'"version": NaN', 1)
    elif change == "over-cap":
        raw += b" " * 16384
    with pytest.raises(Refused, match="^runtime_proof_refused$"):
        reviewed(profile, raw, expected)


def test_duplicate_tuple_refuses_without_order_sensitive_image_identity(tmp_path):
    from dataclasses import replace

    from butlers.session_host.profiles import ProfileFactory

    profile = synthetic_profile(tmp_path)
    assert replace(profile, image=tuple(reversed(profile.image))).subject == profile.subject
    accepted = reviewed(profile)
    with pytest.raises(Refused, match="^invalid_runtime_profiles$"):
        ProfileFactory((accepted, accepted))
