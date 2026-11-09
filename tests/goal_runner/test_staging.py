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

from butlers.goal_runner.contracts import Launch
from butlers.goal_runner.staging import (
    ControlSessionHost,
    CredentialUnavailable,
    ModelCredentials,
    SessionHostRefused,
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


@pytest.mark.parametrize(
    "reply", [{"status": "refused"}, {"status": "completed", "extra": 1}, ["completed"]]
)
async def test_anything_but_a_bare_completed_reply_is_a_reported_failure(reply):
    outcome = await _host(Store({"cli-auth/openai": _SECRET}), Channel(reply)).launch(_launch())

    assert isinstance(outcome.failure, SessionHostRefused)


async def test_a_channel_failure_is_reported_without_its_text(caplog):
    channel = Channel(error=OSError(f"peer said {_SECRET}"))
    caplog.set_level(logging.DEBUG)

    outcome = await _host(Store({"cli-auth/openai": _SECRET}), channel).launch(_launch())

    assert isinstance(outcome.failure, SessionHostRefused)
    assert _SECRET not in str(outcome.failure)
    assert _SECRET not in caplog.text and _TOKEN not in caplog.text


async def test_terminate_revokes_the_invocation_and_tolerates_an_unknown_one():
    channel = Channel(error=OSError("invocation_unavailable"))
    invocation = uuid4()

    await _host(Store({}), channel).terminate(invocation)

    assert channel.sent == [{"version": 1, "operation": "revoke", "invocation_id": str(invocation)}]
