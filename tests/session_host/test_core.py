"""Host-core controls; synthetic domains do not establish kernel/native proofs."""

import asyncio
import base64
import json
from dataclasses import replace
from uuid import uuid4

import pytest

from butlers.session_host.contracts import BrokerBinding, LaunchRequest, Refused
from butlers.session_host.lifecycle import InvocationOwner
from butlers.session_host.material import InvocationMaterial


def request():
    goal = uuid4()
    origin = "https://control.test:9101"
    return LaunchRequest(
        invocation_id=uuid4(),
        broker=BrokerBinding(
            goal,
            f"{origin}/goals/{goal}/mcp",
            base64.urlsafe_b64encode(bytes(range(32))).rstrip(b"=").decode(),
            origin,
        ),
        runtime="codex",
        model="test-model",
        model_credential=b"synthetic-credential",
        prompt="synthetic-prompt",
        timeout_s=0.02,
    )


@pytest.fixture
def stage_root(tmp_path):
    root = tmp_path / "private-stage"
    root.mkdir(mode=0o700)
    root.chmod(0o700)  # fixture scratch may inherit setgid from its parent
    return root


class DomainDouble:
    def __init__(self, *, settled=True, error=False, block=False):
        self.settled = settled
        self.error = error
        self.block = block
        self.executed = asyncio.Event()
        self.release = asyncio.Event()
        self.settling = asyncio.Event()
        self.material = None

    async def execute(self, material):
        self.material = material
        self.executed.set()
        if self.block:
            await self.release.wait()
        if self.error:
            raise RuntimeError("private runtime output must not escape")
        return b"untrusted model result"

    async def settle(self):
        self.settling.set()
        if self.block:
            await self.release.wait()
        return self.settled


def test_binding_requires_exact_goal_and_origin():
    req = request()
    binding = req.broker
    for endpoint in (
        binding.endpoint.replace("control.test", "domain.test"),
        binding.endpoint + "?next=database",
        binding.endpoint + "#fragment",
        binding.endpoint.replace(str(binding.goal_id), str(uuid4())),
        binding.endpoint.replace("https://", "https://user@"),
    ):
        with pytest.raises(Refused, match="invalid_broker_binding"):
            replace(binding, endpoint=endpoint)
    for origin in ("http://control.test:9101", "https://control.test/path", "https://u@host"):
        with pytest.raises(Refused, match="invalid_session_origin"):
            replace(binding, trusted_origin=origin)
    with pytest.raises(Refused):
        replace(binding, token=binding.token[:-1] + "B")  # noncanonical pad bits
    assert binding.endpoint.endswith(f"/goals/{binding.goal_id}/mcp")
    assert binding.token not in repr(binding)


def test_launch_is_closed_bounded_and_redacted():
    req = request()
    for changes in (
        {"version": True},
        {"version": 2},
        {"timeout_s": float("nan")},
        {"timeout_s": 301},
        {"model_credential": b""},
        {"prompt": "bad\0prompt"},
    ):
        with pytest.raises(Refused):
            replace(req, **changes)
    assert req.prompt not in repr(req)
    assert req.model_credential.decode() not in repr(req)
    assert req.broker.token not in repr(req)


def test_staging_is_private_separate_and_only_contains_declared_material(stage_root):
    req = request()
    first = InvocationMaterial.stage(stage_root, req)
    second = InvocationMaterial.stage(stage_root, replace(req, invocation_id=uuid4()))
    try:
        assert first.directory != second.directory
        assert first.directory.stat().st_mode & 0o777 == 0o700
        assert {p.name for p in first.directory.iterdir()} == {
            "mcp.json",
            "prompt",
            "model-credential",
        }
        for item in first.directory.iterdir():
            assert item.stat().st_mode & 0o777 == 0o600
        config = json.loads((first.directory / "mcp.json").read_bytes())
        assert set(config["mcpServers"]) == {"broker"}
        assert config["mcpServers"]["broker"]["url"] == req.broker.endpoint
        assert (first.directory / "model-credential").read_bytes() == req.model_credential
        assert str(first.directory) not in repr(first)
    finally:
        first.discard()
        second.discard()
    assert not list(stage_root.iterdir())


def test_unsafe_roots_and_replaced_stage_are_refused(stage_root, tmp_path):
    stage_root.chmod(0o755)
    with pytest.raises(Refused, match="unsafe_stage_root"):
        InvocationMaterial.stage(stage_root, request())
    stage_root.chmod(0o700)
    link = tmp_path / "linked-root"
    link.symlink_to(stage_root, target_is_directory=True)
    with pytest.raises(Refused, match="unsafe_stage_root"):
        InvocationMaterial.stage(link, request())
    material = InvocationMaterial.stage(stage_root, request())
    moved = stage_root / "quarantined-original"
    material.directory.rename(moved)
    material.directory.symlink_to(moved, target_is_directory=True)
    with pytest.raises(Refused, match="stage_identity_changed"):
        material.discard()
    assert (moved / "model-credential").is_file()


async def test_result_waits_for_descendant_settlement_and_replay_refuses(stage_root):
    req = request()
    domain = DomainDouble()
    gate = asyncio.Event()

    async def settle():
        domain.settling.set()
        await gate.wait()
        assert (domain.material.directory / "model-credential").exists()
        return True

    domain.settle = settle
    owner = InvocationOwner(stage_root)
    task = asyncio.create_task(owner.run(req, lambda: domain))
    await domain.settling.wait()
    assert not task.done()  # leader work finished, but descendants unproved
    assert domain.material.directory.exists()
    gate.set()
    assert await task == b"untrusted model result"
    assert owner.healthy and not list(stage_root.iterdir())
    with pytest.raises(Refused, match="invocation_already_started"):
        await owner.run(req, lambda: DomainDouble())


@pytest.mark.parametrize("settled", [False, None, 1])
async def test_unproved_cleanup_quarantines_and_refuses_more_work(stage_root, settled):
    owner = InvocationOwner(stage_root)
    domain = DomainDouble(settled=settled)
    with pytest.raises(Refused, match="domain_cleanup_unproven"):
        await owner.run(request(), lambda: domain)
    assert not owner.healthy
    assert (domain.material.directory / "model-credential").exists()
    with pytest.raises(Refused, match="host_unavailable"):
        await owner.run(request(), lambda: DomainDouble())


async def test_runtime_error_and_timeout_never_publish_but_cleanup_runs(stage_root):
    owner = InvocationOwner(stage_root)
    domain = DomainDouble(error=True)
    with pytest.raises(Refused, match="runtime_failed") as error:
        await owner.run(request(), lambda: domain)
    assert "private runtime" not in str(error.value)
    assert domain.settling.is_set() and not list(stage_root.iterdir())
    domain = DomainDouble()

    async def wait_for_cancel(_material):
        domain.executed.set()
        await asyncio.Event().wait()

    domain.execute = wait_for_cancel
    with pytest.raises(Refused, match="runtime_failed"):
        await owner.run(request(), lambda: domain)
    assert domain.settling.is_set() and owner.healthy
    assert not list(stage_root.iterdir())


async def test_repeated_cancellation_cannot_skip_cleanup(stage_root):
    owner = InvocationOwner(stage_root)
    domain = DomainDouble(block=True)
    task = asyncio.create_task(owner.run(request(), lambda: domain))
    await domain.executed.wait()
    task.cancel()
    await domain.settling.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    assert domain.material.directory.exists()
    domain.release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert owner.healthy and not list(stage_root.iterdir())


async def test_cleanup_timeout_is_unhealthy_and_never_resets_deadline(stage_root):
    owner = InvocationOwner(stage_root, cleanup_timeout_s=0.02)
    domain = DomainDouble()
    unfinished = asyncio.Event()

    async def stubborn_cleanup():
        while not unfinished.is_set():
            try:
                await unfinished.wait()
            except asyncio.CancelledError:
                pass
        return True

    domain.settle = stubborn_cleanup
    try:
        with pytest.raises(Refused, match="domain_cleanup_unproven"):
            await owner.run(request(), lambda: domain)
        assert not owner.healthy and domain.material.directory.exists()
        assert owner.pending_count == 1
    finally:
        unfinished.set()
        for _ in range(5):
            await asyncio.sleep(0)
    assert owner.pending_count == 0


async def test_broken_backend_cannot_escape_cleanup_or_leak_error(stage_root):
    domain = DomainDouble()

    def broken_execute(_material):
        raise RuntimeError("synthetic secret error")

    domain.execute = broken_execute
    owner = InvocationOwner(stage_root)
    with pytest.raises(Refused, match="runtime_failed") as error:
        await owner.run(request(), lambda: domain)
    assert str(error.value) == "runtime_failed"
    assert domain.settling.is_set() and not list(stage_root.iterdir())

    def broken_settle():
        raise RuntimeError("synthetic observer secret")

    domain.settle = broken_settle
    with pytest.raises(Refused, match="domain_cleanup_unproven"):
        await owner.run(request(), lambda: domain)
    assert not owner.healthy and list(stage_root.iterdir())


async def test_factory_cleanup_failure_poisoning_and_recovery_control(stage_root, monkeypatch):
    owner = InvocationOwner(stage_root)
    original_discard = InvocationMaterial.discard

    def broken_factory():
        raise RuntimeError("synthetic private factory error")

    def failed_discard(_material):
        raise Refused("stage_cleanup_failed")

    monkeypatch.setattr(InvocationMaterial, "discard", failed_discard)
    with pytest.raises(Refused, match="stage_cleanup_failed"):
        await owner.run(request(), broken_factory)
    assert not owner.healthy
    assert len(list(stage_root.iterdir())) == 1
    called = False

    def another_factory():
        nonlocal called
        called = True
        return DomainDouble()

    with pytest.raises(Refused, match="host_unavailable"):
        await owner.run(request(), another_factory)
    assert not called and len(list(stage_root.iterdir())) == 1
    monkeypatch.setattr(InvocationMaterial, "discard", original_discard)
    # Successful cleanup of a failed inert factory is the positive companion:
    # it removes the new material and leaves a different owner usable.
    clean_root = stage_root.with_name("clean-stage")
    clean_root.mkdir(mode=0o700)
    clean_root.chmod(0o700)
    other = InvocationOwner(clean_root)
    with pytest.raises(Refused, match="domain_unavailable"):
        await other.run(request(), broken_factory)
    assert other.healthy and len(list(stage_root.iterdir())) == 1
    assert await other.run(request(), DomainDouble) == b"untrusted model result"
    assert not list(clean_root.iterdir())


async def test_restart_refuses_stale_material_without_deleting_it(stage_root):
    material = InvocationMaterial.stage(stage_root, request())
    owner = InvocationOwner(stage_root)
    called = False

    def factory():
        nonlocal called
        called = True
        return DomainDouble()

    with pytest.raises(Refused, match="host_unavailable"):
        await owner.run(request(), factory)
    assert not owner.healthy and not called
    assert (material.directory / "model-credential").is_file()
    material.discard()  # test-owned synthetic data, no domain ever launched
    fresh = InvocationOwner(stage_root)
    assert await fresh.run(request(), DomainDouble) == b"untrusted model result"


async def test_partial_staging_failure_retains_material_and_refuses_launch(stage_root, monkeypatch):
    owner = InvocationOwner(stage_root)
    opened = 0
    original_open = __import__("os").open

    def failed_second_write(*args, **kwargs):
        nonlocal opened
        opened += 1
        if opened == 2:
            raise OSError("synthetic write failure")
        return original_open(*args, **kwargs)

    def failed_discard(_material):
        raise Refused("stage_cleanup_failed")

    monkeypatch.setattr("butlers.session_host.material.os.open", failed_second_write)
    monkeypatch.setattr(InvocationMaterial, "discard", failed_discard)
    factory_called = False

    def factory():
        nonlocal factory_called
        factory_called = True
        return DomainDouble()

    with pytest.raises(Refused, match="stage_cleanup_failed"):
        await owner.run(request(), factory)
    assert not owner.healthy and not factory_called
    [partial] = list(stage_root.iterdir())
    assert (partial / "model-credential").read_bytes() == b"synthetic-credential"
    assert len(owner._quarantined) == 1
    with pytest.raises(Refused, match="host_unavailable"):
        await owner.run(request(), factory)
    assert not factory_called and len(list(stage_root.iterdir())) == 1


@pytest.mark.parametrize("failing_start", [1, 2])
async def test_task_creation_failure_quarantines_in_both_phases(
    stage_root, monkeypatch, failing_start
):
    owner = InvocationOwner(stage_root)
    original_create = asyncio.create_task
    created = []
    calls = 0

    def failed_start(coroutine):
        nonlocal calls
        calls += 1
        if calls == failing_start:
            raise RuntimeError("synthetic loop failure")
        task = original_create(coroutine)
        created.append(task)
        return task

    monkeypatch.setattr("butlers.session_host.lifecycle.asyncio.create_task", failed_start)
    with pytest.raises(Refused, match="domain_task_unavailable"):
        await owner.run(request(), DomainDouble)
    assert not owner.healthy
    assert len(list(stage_root.iterdir())) == 1
    assert len(owner._quarantined) == 1
    with pytest.raises(Refused, match="host_unavailable"):
        await owner.run(request(), DomainDouble)
    if created:
        await asyncio.gather(*created, return_exceptions=True)
    await asyncio.sleep(0)
    assert owner.pending_count == 0
