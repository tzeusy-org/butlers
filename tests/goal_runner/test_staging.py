"""Per-invocation model credential staging over the control channel (bu-dhloi1.206).

The runner resolves each launch's model credential when it launches and sends
exactly that one credential with the launch command the session host's control
listener accepts. Nothing else of the store is read or sent, and nothing secret
reaches a log.
"""

from __future__ import annotations

import base64
import logging
from typing import Any
from uuid import uuid4

import pytest

from butlers.core.failover_classifier import FailoverContext, classify_failover_eligibility
from butlers.core.model_call import NotInvoked
from butlers.goal_runner.contracts import Launch
from butlers.goal_runner.staging import (
    ControlSessionHost,
    CredentialUnavailable,
    ModelCredentials,
    SessionHostBusy,
    SessionHostFailed,
    SessionHostRefused,
    SessionHostTimeout,
)
from butlers.session_host.contracts import BrokerBinding, LaunchRequest

pytestmark = pytest.mark.unit

_ORIGIN = "https://control.test"
_TOKEN = base64.urlsafe_b64encode(bytes(range(32))).rstrip(b"=").decode()
_SECRET = "sk-test-openai-key"


class Store:
    def __init__(self, values: dict[str, str | None]) -> None:
        self.values = values
        self.loaded: list[str] = []

    async def load(self, key: str) -> str | None:
        self.loaded.append(key)
        return self.values.get(key)


class Channel:
    def __init__(self, reply: Any = None, error: Exception | None = None) -> None:
        self.sent: list[dict] = []
        self.reply = {"status": "completed"} if reply is None else reply
        self.error = error

    async def __call__(self, command: dict) -> Any:
        self.sent.append(command)
        if self.error is not None:
            raise self.error
        return self.reply


def _launch(runtime: str = "codex") -> Launch:
    return Launch(
        goal_id=uuid4(),
        invocation_id=uuid4(),
        token=_TOKEN,
        runtime=runtime,
        model="gpt-test",
        prompt="goal_id: x",
        timeout_s=120,
    )


def _host(store: Store, channel: Channel) -> ControlSessionHost:
    return ControlSessionHost(channel, ModelCredentials(store), broker_origin=_ORIGIN)


async def test_a_launch_stages_only_its_runtimes_credential_in_a_command_the_host_accepts():
    store = Store({"cli-auth/openai": _SECRET, "cli-auth/claude": "other-secret"})
    channel = Channel()
    launch = _launch()

    outcome = await _host(store, channel).launch(launch)

    assert outcome.failure is None
    assert store.loaded == ["cli-auth/openai"]  # one key, read at launch time
    (command,) = channel.sent
    assert base64.b64decode(command["credential"]) == _SECRET.encode()
    assert "other-secret" not in repr(command)
    # The session host's own contract accepts exactly this command's content.
    request = LaunchRequest(
        invocation_id=launch.invocation_id,
        broker=BrokerBinding(launch.goal_id, command["endpoint"], command["token"], _ORIGIN),
        runtime=command["runtime"],
        model=command["model"],
        model_credential=base64.b64decode(command["credential"]),
        prompt=command["prompt"],
        timeout_s=command["timeout_s"],
        version=command["version"],
    )
    assert request.model_credential == _SECRET.encode()
    assert set(command) == {
        "version",
        "operation",
        "invocation_id",
        "goal_id",
        "endpoint",
        "token",
        "runtime",
        "model",
        "credential",
        "prompt",
        "timeout_s",
    }


async def test_each_launch_reads_the_credential_afresh():
    store = Store({"cli-auth/openai": _SECRET})
    host = _host(store, Channel())

    await host.launch(_launch())
    store.values["cli-auth/openai"] = "rotated-key"
    channel = Channel()
    host._send = channel
    await host.launch(_launch())

    assert base64.b64decode(channel.sent[0]["credential"]) == b"rotated-key"


@pytest.mark.parametrize(
    ("runtime", "values", "code"),
    [
        ("claude", {"cli-auth/claude": _SECRET}, "credential_runtime_unsupported"),
        ("codex", {}, "credential_missing"),
        ("codex", {"cli-auth/openai": ""}, "credential_missing"),
    ],
)
async def test_no_resolvable_credential_refuses_before_anything_is_sent(runtime, values, code):
    channel = Channel()

    with pytest.raises(CredentialUnavailable) as refused:
        await _host(Store(values), channel).launch(_launch(runtime))

    assert str(refused.value) == code
    assert channel.sent == []


async def test_an_unreachable_store_is_a_refusal_not_a_crash():
    class Broken:
        async def load(self, key: str) -> str | None:
            raise ConnectionError(_SECRET)

    with pytest.raises(CredentialUnavailable) as refused:
        await ControlSessionHost(
            Channel(), ModelCredentials(Broken()), broker_origin=_ORIGIN
        ).launch(_launch())

    assert str(refused.value) == "credential_store_unavailable"
    assert _SECRET not in repr(refused.value)


_USAGE = {"input_tokens": 1200, "output_tokens": 300, "cached_input_tokens": 1000}


async def _outcome(reply: Any):
    return await _host(Store({"cli-auth/openai": _SECRET}), Channel(reply)).launch(_launch())


def _eligible(failure: BaseException) -> bool:
    return classify_failover_eligibility(FailoverContext(exception=failure)).eligible


@pytest.mark.parametrize("usage", [None, _USAGE, {"output_tokens": 0}, {"input_tokens": 2**53}])
async def test_a_completed_reply_ends_cleanly_with_its_counts(usage):
    reply = {"status": "completed"} if usage is None else {"status": "completed", "usage": usage}

    outcome = await _outcome(reply)

    assert outcome.failure is None
    assert outcome.usage == usage


@pytest.mark.parametrize(
    ("failure_class", "kind", "retryable"),
    [
        ("timeout", SessionHostTimeout, True),
        ("runtime_failed", SessionHostFailed, False),
        ("containment_failed", SessionHostFailed, False),
        ("cancelled", SessionHostFailed, False),
        ("provider_quota", SessionHostFailed, False),  # unknown: default-closed
    ],
)
async def test_a_failed_reply_maps_its_class_onto_the_runtime_taxonomy(
    failure_class, kind, retryable
):
    outcome = await _outcome({"status": "failed", "failure_class": failure_class, "usage": _USAGE})

    assert type(outcome.failure) is kind
    assert _eligible(outcome.failure) is retryable
    expected = failure_class if failure_class != "provider_quota" else "failure_class_unknown"
    assert str(outcome.failure) == expected  # a fixed code, never the host's text
    assert outcome.usage == _USAGE  # a failed run's usage is still charged


async def test_a_bare_refusal_is_a_non_retryable_failure():
    outcome = await _outcome({"status": "refused"})

    assert type(outcome.failure) is SessionHostRefused
    assert str(outcome.failure) == "session_host_refused"
    assert not _eligible(outcome.failure)


@pytest.mark.parametrize(
    "reply",
    [
        ["completed"],
        {"status": "completed", "extra": 1},
        {"status": "completed", "usage": {}},
        {"status": "completed", "usage": {"input_tokens": True}},
        {"status": "completed", "usage": {"input_tokens": -1}},
        {"status": "completed", "usage": {"input_tokens": 2**53 + 1}},
        {"status": "completed", "usage": {"input_tokens": 1.0}},
        {"status": "completed", "usage": {"input_tokens": 1, "cost": 1}},
        {"status": "completed", "usage": [1, 2]},
        {"status": "failed"},
        {"status": "failed", "failure_class": 7},
        {"status": "failed", "failure_class": "timeout", "detail": _SECRET},
        {"status": "refused", "reason": "staging_refused"},
        {"status": "refused", "reason": "host_busy", "usage": _USAGE},
        {"status": "revoked"},
        {"status": True},
    ],
)
async def test_any_other_reply_is_malformed_and_never_logged(reply, caplog):
    caplog.set_level(logging.DEBUG)

    outcome = await _outcome(reply)

    assert type(outcome.failure) is SessionHostRefused
    assert str(outcome.failure) == "session_host_malformed_reply"
    assert outcome.usage is None
    assert not _eligible(outcome.failure)
    assert _SECRET not in caplog.text and "staging_refused" not in caplog.text


class Busy(Channel):
    """Refuses ``host_busy`` the first ``busy`` launches, then completes."""

    def __init__(self, busy: int) -> None:
        super().__init__()
        self.busy = busy

    async def __call__(self, command: dict) -> Any:
        self.sent.append(command)
        if command["operation"] == "launch" and self.busy:
            self.busy -= 1
            return {"status": "refused", "reason": "host_busy"}
        return {"status": "completed", "usage": _USAGE}


async def test_a_busy_host_is_offered_the_same_launch_again():
    channel = Busy(busy=2)
    host = ControlSessionHost(
        channel, ModelCredentials(Store({"cli-auth/openai": _SECRET})), broker_origin=_ORIGIN
    )
    host.busy_backoff_s = 0.0

    outcome = await host.launch(_launch())

    assert outcome.failure is None and outcome.usage == _USAGE
    launches = [c for c in channel.sent if c["operation"] == "launch"]
    assert len(launches) == 3
    assert all(c == launches[0] for c in launches)  # same invocation id: it was not burned
    assert not [c for c in channel.sent if c["operation"] == "revoke"]


async def test_a_host_that_stays_busy_refuses_before_any_model_runs():
    channel = Busy(busy=99)
    host = ControlSessionHost(
        channel, ModelCredentials(Store({"cli-auth/openai": _SECRET})), broker_origin=_ORIGIN
    )
    host.busy_backoff_s = 0.0

    with pytest.raises(SessionHostBusy) as busy:
        await host.launch(_launch())

    assert isinstance(busy.value, NotInvoked)  # suppressed by the kernel, never a relaunch
    assert str(busy.value) == "session_host_busy"
    assert len(channel.sent) == host.busy_attempts


async def test_a_channel_failure_is_reported_without_its_text(caplog):
    channel = Channel(error=OSError(f"peer said {_SECRET}"))
    caplog.set_level(logging.DEBUG)

    outcome = await _host(Store({"cli-auth/openai": _SECRET}), channel).launch(_launch())

    assert isinstance(outcome.failure, SessionHostRefused)
    assert _SECRET not in str(outcome.failure)
    assert _SECRET not in caplog.text and _TOKEN not in caplog.text


@pytest.mark.parametrize("revoke_fails", [False, True])
async def test_a_launch_whose_reply_is_lost_is_revoked_on_the_host(revoke_fails):
    # A send that fails (a dropped connection, or a reply that outlived even the
    # long launch wait) may leave the host running the launch with its credential
    # and a concurrency slot; the runner must revoke it (r4513585120).
    class TimedOut(Channel):
        async def __call__(self, command: dict) -> Any:
            self.sent.append(command)
            if command["operation"] == "launch" or revoke_fails:
                raise TimeoutError("read timed out")
            return {"status": "completed"}

    channel = TimedOut()
    launch = _launch()

    outcome = await _host(Store({"cli-auth/openai": _SECRET}), channel).launch(launch)

    assert isinstance(outcome.failure, SessionHostRefused)
    assert str(outcome.failure) == "session_host_unavailable"
    assert [command["operation"] for command in channel.sent] == ["launch", "revoke"]
    assert channel.sent[1] == {
        "version": 1,
        "operation": "revoke",
        "invocation_id": str(launch.invocation_id),
    }


async def test_terminate_revokes_the_invocation_and_tolerates_an_unknown_one():
    channel = Channel(error=OSError("invocation_unavailable"))
    invocation = uuid4()

    await _host(Store({}), channel).terminate(invocation)

    assert channel.sent == [{"version": 1, "operation": "revoke", "invocation_id": str(invocation)}]
