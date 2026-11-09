"""Real local TLS handshakes, synthetic PKI and content-blind host controls."""

import asyncio
import base64
import json
import ssl
import struct
from uuid import uuid4

import pytest

from butlers.session_host.control import LocalControl, local_command
from butlers.session_host.lifecycle import InvocationOwner


def launch():
    goal = uuid4()
    return dict(
        version=1,
        operation="launch",
        invocation_id=str(uuid4()),
        goal_id=str(goal),
        endpoint=f"https://broker.test/goals/{goal}/mcp",
        token=base64.urlsafe_b64encode(bytes(range(32))).rstrip(b"=").decode(),
        runtime="synthetic",
        model="synthetic",
        credential=base64.b64encode(b"SYNTHETIC_PRIVATE_CREDENTIAL").decode(),
        prompt="SYNTHETIC_PRIVATE_PROMPT",
        timeout_s=2,
    )


class Domain:
    def __init__(
        self, *, block=False, settled=True, usage=None, fail=False, gate=None, delay: float = 0
    ):
        self.entered = asyncio.Event()
        self.block = block
        self.settled = settled
        self.usage_value = usage
        self.fail = fail
        self.gate = gate
        self.delay = delay
        self.material = None
        self.runs = 0
        self.arrivals = asyncio.Queue()

    def usage(self):
        return self.usage_value

    async def wait_runs(self, count):
        for _ in range(count):
            await asyncio.wait_for(self.arrivals.get(), 3)

    async def execute(self, material):
        self.material = material
        assert (
            material.directory / "model-credential"
        ).read_bytes() == b"SYNTHETIC_PRIVATE_CREDENTIAL"
        self.runs += 1
        self.arrivals.put_nowait(None)
        self.entered.set()
        if self.block:
            await asyncio.Event().wait()
        if self.gate is not None:
            await self.gate.wait()
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError("SYNTHETIC_PRIVATE_RUNTIME_ERROR")
        return b"SYNTHETIC_PRIVATE_RESULT"

    async def settle(self):
        return self.settled


@pytest.fixture
def channel(tmp_path, pki):
    root = tmp_path / "stage"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    owner = InvocationOwner(root, cleanup_timeout_s=0.2)
    server, client, pin = pki

    def build(domain, **extra):
        return LocalControl(
            owner=owner,
            factory=lambda runtime, model: domain,
            **server(),
            caller_sha256=pin("runner"),
            server_sha256=pin("server"),
            broker_origin="https://broker.test",
            **extra,
        )

    async def send(port, command, *, name="runner", hostname="host.test", server_pin=None):
        return await local_command(
            port,
            context=client(name),
            server_hostname=hostname,
            server_sha256=server_pin or pin("server"),
            command=command,
        )

    return build, send, owner, root


async def test_mtls_launch_cleanup_and_replay_refusal(channel, caplog):
    build, send, owner, root = channel
    domain = Domain()
    host = build(domain)
    port = await host.start()
    command = launch()
    try:
        reply = await send(port, command)
        assert reply == {"status": "completed"}
        assert domain.entered.is_set() and owner.healthy and not list(root.iterdir())
        assert await send(port, command) == {"status": "refused"}
        assert "SYNTHETIC_PRIVATE" not in json.dumps(reply) + caplog.text
    finally:
        await host.close()


@pytest.mark.parametrize(
    "name,hostname,pin",
    [
        (None, "host.test", None),
        ("outsider", "host.test", None),
        ("other", "host.test", None),
        ("reflection", "host.test", None),
        ("runner", "wrong.test", None),
        ("runner", "host.test", "0" * 64),
    ],
)
async def test_unauthorized_identity_hostname_and_server_pin_never_launch(
    channel, name, hostname, pin
):
    build, send, owner, root = channel
    domain = Domain()
    host = build(domain)
    port = await host.start()
    try:
        try:
            reply = await send(port, launch(), name=name, hostname=hostname, server_pin=pin)
            assert reply == {"status": "refused"}
        except (ssl.SSLError, ConnectionError, asyncio.IncompleteReadError, ValueError):
            pass
        assert not domain.entered.is_set() and owner.healthy and not list(root.iterdir())
        assert await send(port, launch()) == {"status": "completed"}
    finally:
        await host.close()


@pytest.mark.parametrize("settled", [True, False])
async def test_authenticated_revoke_waits_for_cleanup_or_quarantines(channel, settled):
    build, send, owner, root = channel
    domain = Domain(block=True, settled=settled)
    host = build(domain)
    port = await host.start()
    command = launch()
    pending = asyncio.create_task(send(port, command))
    try:
        await asyncio.wait_for(domain.entered.wait(), 2)
        revoke = dict(version=1, operation="revoke", invocation_id=command["invocation_id"])
        assert await send(port, revoke) == {"status": "revoked" if settled else "quarantined"}
        assert await pending == {
            "status": "failed",
            "failure_class": "cancelled" if settled else "containment_failed",
        }
        assert owner.healthy is settled
        assert bool(list(root.iterdir())) is not settled
        assert await send(port, command) == {"status": "refused"}
        assert await send(port, revoke) == {"status": "refused"}
    finally:
        await host.close()


@pytest.mark.parametrize("mutation", ["origin", "version", "unknown", "credential", "prompt"])
async def test_authenticated_malformed_launch_has_no_staging(channel, mutation):
    build, send, owner, root = channel
    domain = Domain()
    host = build(domain)
    port = await host.start()
    command = launch()
    if mutation == "origin":
        command["endpoint"] += "?authority=other"
    elif mutation == "version":
        command["version"] = True
    elif mutation == "unknown":
        command["caller_sha256"] = "0" * 64
    elif mutation == "credential":
        command["credential"] = "A" * 87385
    else:
        command["prompt"] = "x" * (128 * 1024 + 1)
    try:
        assert await send(port, command) == {"status": "refused"}
        assert not domain.entered.is_set() and owner.healthy and not list(root.iterdir())
        assert await send(port, launch()) == {"status": "completed"}
    finally:
        await host.close()


async def test_oversized_frame_and_duplicate_keys_refuse_before_launch(channel, pki):
    build, send, owner, root = channel
    domain = Domain()
    host = build(domain)
    port = await host.start()
    try:
        for raw in (struct.pack("!I", 280 * 1024 + 1), struct.pack("!I", 13) + b'{"a":1,"a":2}'):
            reader, writer = await asyncio.open_connection(
                "127.0.0.1", port, ssl=pki[1](), server_hostname="host.test"
            )
            writer.write(raw)
            await writer.drain()
            size = struct.unpack("!I", await reader.readexactly(4))[0]
            assert json.loads(await reader.readexactly(size)) == {"status": "refused"}
            writer.close()
            await writer.wait_closed()
        assert not domain.entered.is_set() and not list(root.iterdir())
        assert await send(port, launch()) == {"status": "completed"}
    finally:
        await host.close()


def test_reflected_server_identity_is_not_an_admissible_runner(channel, pki):
    from butlers.session_host.contracts import Refused

    _, _, owner, _ = channel
    with pytest.raises(Refused, match="invalid_control_identity"):
        LocalControl(
            owner=owner,
            factory=lambda runtime, model: Domain(),
            **pki[0](),
            caller_sha256=pki[2]("server"),
            server_sha256=pki[2]("server"),
            broker_origin="https://broker.test",
        )


async def test_cancelling_control_handler_still_settles_owned_launch(channel):
    build, _, owner, root = channel
    domain = Domain(block=True)
    host = build(domain)
    task = asyncio.create_task(host._command(launch()))
    await asyncio.wait_for(domain.entered.wait(), 2)
    task.cancel()
    assert await asyncio.wait_for(task, 2) == {"status": "failed", "failure_class": "cancelled"}
    assert owner.healthy and not list(root.iterdir()) and not host._active
    await host.close()


@pytest.mark.parametrize(
    "identity,correct_pin", [("server", False), ("reflection", False), ("reflection", True)]
)
async def test_actual_installed_leaf_rejects_false_pin_and_reflection(
    channel, pki, identity, correct_pin
):
    from butlers.session_host.contracts import Refused

    _, _, owner, root = channel
    domain = Domain()
    host = None
    try:
        with pytest.raises(Refused, match="invalid_control_identity"):
            host = LocalControl(
                owner=owner,
                factory=lambda runtime, model: domain,
                **pki[0](identity),
                caller_sha256=pki[2]("reflection" if identity == "reflection" else "runner"),
                server_sha256=pki[2](identity) if correct_pin else "0" * 64,
                broker_origin="https://broker.test",
            )
            port = await host.start()
            reply = await local_command(
                port,
                context=pki[1]("reflection" if identity == "reflection" else "runner"),
                server_hostname="host.test",
                server_sha256=pki[2](identity),
                command=launch(),
            )
            assert reply == {"status": "completed"} and domain.entered.is_set()
        assert not domain.entered.is_set() and not list(root.iterdir())
    finally:
        if host:
            await host.close()


# -- bu-kiuh8v.13: a launch reply that arrives when the invocation settles ----------------


async def test_a_completed_launch_reports_the_runtimes_usage(channel):
    build, send, owner, root = channel
    usage = {"input_tokens": 120, "output_tokens": 45}
    host = build(Domain(usage=usage))
    port = await host.start()
    try:
        assert await send(port, launch()) == {"status": "completed", "usage": usage}
    finally:
        await host.close()


@pytest.mark.parametrize(
    "bad",
    [
        {"input_tokens": True},
        {"input_tokens": -1},
        {"input_tokens": 2**60},
        {"input_tokens": 1.5},
        {"secret": 1},
        {"input_tokens": "5"},
        ["input_tokens"],
        "SYNTHETIC_PRIVATE",
    ],
)
async def test_usage_outside_the_closed_shape_is_dropped_not_forwarded(channel, bad, caplog):
    build, send, owner, root = channel
    host = build(Domain(usage=bad))
    port = await host.start()
    try:
        reply = await send(port, launch())
        assert reply == {"status": "completed"}
        assert "SYNTHETIC_PRIVATE" not in json.dumps(reply) + caplog.text
    finally:
        await host.close()


async def test_a_usage_report_that_raises_is_dropped(channel):
    build, send, owner, root = channel
    domain = Domain()
    domain.usage = lambda: 1 / 0
    host = build(domain)
    port = await host.start()
    try:
        assert await send(port, launch()) == {"status": "completed"}
    finally:
        await host.close()


async def test_a_runtime_failure_reports_its_class_and_never_its_text(channel, caplog):
    build, send, owner, root = channel
    host = build(Domain(fail=True, usage={"output_tokens": 3}))
    port = await host.start()
    try:
        reply = await send(port, launch())
        assert reply == {
            "status": "failed",
            "failure_class": "runtime_failed",
            "usage": {"output_tokens": 3},
        }
        assert "SYNTHETIC_PRIVATE" not in json.dumps(reply) + caplog.text
        assert owner.healthy and not list(root.iterdir())
    finally:
        await host.close()


async def test_a_launch_that_outlives_its_timeout_reports_timeout(channel):
    build, send, owner, root = channel
    host = build(Domain(delay=5))
    port = await host.start()
    command = launch() | {"timeout_s": 0.2}
    try:
        reply = await send(port, command)
        assert reply == {"status": "failed", "failure_class": "timeout"}
    finally:
        await host.close()


async def test_a_host_that_cannot_resolve_its_profile_refuses_before_any_start(channel, pki):
    build, send, owner, root = channel
    server, _, pin = pki
    host = LocalControl(
        owner=owner,
        factory=lambda runtime, model: (_ for _ in ()).throw(RuntimeError("SYNTHETIC_PRIVATE")),
        **server(),
        caller_sha256=pin("runner"),
        server_sha256=pin("server"),
        broker_origin="https://broker.test",
    )
    port = await host.start()
    try:
        reply = await send(port, launch())
        assert reply == {"status": "refused"}
        assert "SYNTHETIC_PRIVATE" not in json.dumps(reply)
    finally:
        await host.close()


async def test_the_reply_outlasts_the_short_io_bound_up_to_the_launchs_timeout(
    channel, monkeypatch
):
    from butlers.session_host import control

    monkeypatch.setattr(control, "_IO_SECONDS", 0.3)
    build, send, owner, root = channel
    host = build(Domain(delay=1.0))
    port = await host.start()
    try:
        # The invocation takes three times the control channel's own I/O bound.
        assert await send(port, launch() | {"timeout_s": 5}) == {"status": "completed"}
    finally:
        await host.close()


async def test_other_commands_keep_the_short_reply_bound(channel, monkeypatch):
    from butlers.session_host import control

    monkeypatch.setattr(control, "_IO_SECONDS", 0.3)
    assert control._reply_timeout({"operation": "revoke"}) == 0.3
    assert control._reply_timeout({"operation": "codex_auth_status"}) == 0.3


@pytest.mark.parametrize(
    "timeout_s,expected",
    [(2, 37), (0.5, 35.5), (300, 335)],
)
def test_the_client_reply_bound_is_the_launch_timeout_plus_the_cleanup_bound(timeout_s, expected):
    from butlers.session_host import control

    assert control._reply_timeout({"operation": "launch", "timeout_s": timeout_s}) == expected


@pytest.mark.parametrize("bad", [None, "5", True, float("nan"), -1, 0, 301, float("inf")])
def test_a_launch_with_a_nonsense_timeout_gets_the_largest_bound_never_an_unbounded_wait(bad):
    from butlers.session_host import control

    assert control._reply_timeout({"operation": "launch", "timeout_s": bad}) == 335


async def test_two_invocations_run_at_once_and_each_gets_its_own_reply(channel):
    build, send, owner, root = channel
    gate = asyncio.Event()
    domain = Domain(gate=gate)
    host = build(domain, max_concurrent=2)
    port = await host.start()
    try:
        first, second = launch(), launch()
        one = asyncio.create_task(send(port, first))
        two = asyncio.create_task(send(port, second))
        await domain.wait_runs(2)
        assert len(host._active) == 2
        gate.set()
        assert await one == {"status": "completed"} and await two == {"status": "completed"}
        assert not host._active and owner.healthy and not list(root.iterdir())
    finally:
        await host.close()


async def test_a_revoke_ends_only_its_own_invocation(channel):
    build, send, owner, root = channel
    gate = asyncio.Event()
    domain = Domain(gate=gate)
    host = build(domain, max_concurrent=2)
    port = await host.start()
    try:
        first, second = launch(), launch()
        one = asyncio.create_task(send(port, first))
        two = asyncio.create_task(send(port, second))
        await domain.wait_runs(2)
        revoke = dict(version=1, operation="revoke", invocation_id=first["invocation_id"])
        assert await send(port, revoke) == {"status": "revoked"}
        assert await one == {"status": "failed", "failure_class": "cancelled"}
        assert not two.done() and len(host._active) == 1
        gate.set()
        assert await two == {"status": "completed"}
    finally:
        await host.close()


async def test_beyond_the_bound_a_launch_is_refused_busy_and_its_id_is_not_burned(channel):
    build, send, owner, root = channel
    gate = asyncio.Event()
    domain = Domain(gate=gate)
    host = build(domain, max_concurrent=1)
    port = await host.start()
    try:
        running = asyncio.create_task(send(port, launch()))
        await domain.wait_runs(1)
        waiting = launch()
        assert await send(port, waiting) == {"status": "refused", "reason": "host_busy"}
        assert domain.runs == 1
        gate.set()
        assert await running == {"status": "completed"}
        assert await send(port, waiting) == {"status": "completed"}  # the same id, now admitted
    finally:
        await host.close()


@pytest.mark.parametrize("bad", [0, -1, 65, True, 1.5, None, "2"])
def test_the_concurrency_bound_must_be_a_small_positive_integer(channel, bad):
    from butlers.session_host.contracts import Refused

    build, *_ = channel
    with pytest.raises(Refused):
        build(Domain(), max_concurrent=bad)


async def test_close_settles_every_running_invocation(channel):
    build, send, owner, root = channel
    domain = Domain(block=True)
    host = build(domain, max_concurrent=2)
    port = await host.start()
    one, two = (asyncio.create_task(send(port, launch())) for _ in range(2))
    await domain.wait_runs(2)
    await host.close()
    for reply in await asyncio.gather(one, two, return_exceptions=True):
        assert reply == {"status": "failed", "failure_class": "cancelled"} or isinstance(
            reply, Exception
        )
    assert owner.healthy and not list(root.iterdir())


async def test_a_revoke_that_lands_before_its_launch_tombstones_the_id(channel):
    build, send, owner, root = channel
    domain = Domain()
    host = build(domain)
    port = await host.start()
    try:
        early = launch()
        revoke = dict(version=1, operation="revoke", invocation_id=early["invocation_id"])
        assert await send(port, revoke) == {"status": "revoked"}
        assert await send(port, revoke) == {"status": "revoked"}  # idempotent
        assert await send(port, early) == {"status": "refused"}
        assert not domain.entered.is_set() and domain.runs == 0
        assert owner.healthy and not list(root.iterdir())
        assert await send(port, launch()) == {"status": "completed"}  # others unaffected
    finally:
        await host.close()


async def test_a_revoke_of_a_finished_invocation_is_still_refused(channel):
    build, send, owner, root = channel
    host = build(Domain())
    port = await host.start()
    try:
        done = launch()
        assert await send(port, done) == {"status": "completed"}
        revoke = dict(version=1, operation="revoke", invocation_id=done["invocation_id"])
        assert await send(port, revoke) == {"status": "refused"}
    finally:
        await host.close()


async def test_tombstones_are_bounded(channel, monkeypatch):
    from butlers.session_host import control

    monkeypatch.setattr(control, "_MAX_SEEN", 2)
    build, send, owner, root = channel
    host = build(Domain())
    port = await host.start()
    try:
        for _ in range(2):
            revoke = dict(version=1, operation="revoke", invocation_id=str(uuid4()))
            assert await send(port, revoke) == {"status": "revoked"}
        extra = dict(version=1, operation="revoke", invocation_id=str(uuid4()))
        assert await send(port, extra) == {"status": "refused"}
        assert await send(port, launch()) == {"status": "refused"}  # no room to remember it
    finally:
        await host.close()
