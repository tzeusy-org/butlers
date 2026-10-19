"""Persisted tool-call records carry only the capture allowlist view of a tool's input (bu-4mfhdq).

A tool can mark a parameter sensitive by never storing it (Finance ``track_bill``'s
``payment_destination``). The runtime parsers report every raw argument the model sent, and
the merge persisted that raw input whether or not the parser record paired with the daemon
record. These tests drive the real wrapper, capture and merge helpers with a synthetic
sentinel and assert it is absent from everything that would reach ``sessions.tool_calls``.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest
from fastmcp import Client, FastMCP

from butlers.core.sessions import _sanitize_json_value
from butlers.core.spawner import _check_degenerate_tool_loop
from butlers.core.spawner_tool_calls import _merge_tool_call_records
from butlers.core.tool_call_capture import (
    consume_runtime_session_tool_calls,
    discard_runtime_session_tool_calls,
    fingerprint_tool_call_payload,
    reset_current_runtime_session_id,
    set_current_runtime_session_id,
)
from butlers.mcp_wrappers import _SpanWrappingMCP

pytestmark = pytest.mark.unit

_SENTINEL = "SENTINEL-DESTINATION-7731"
_SESSION = "privacy-session"


async def _executed_calls(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Run real wrapped tools through a real fastmcp Client and return the daemon records.

    FastMCP fills declared defaults into the wrapper's kwargs, exactly as in production, so
    the daemon fingerprint always covers parameters a runtime parser never sends.
    """
    server = FastMCP("finance")
    proxy = _SpanWrappingMCP(server, "finance", module_name="finance")

    @proxy.tool()
    async def track_bill(
        amount: int,
        payment_destination: str | None = None,
        currency: str = "USD",
    ) -> dict[str, str]:
        return {"status": "ok"}

    token = set_current_runtime_session_id(_SESSION)
    try:
        async with Client(server) as client:
            for arguments in calls:
                await client.call_tool("track_bill", arguments)
        return consume_runtime_session_tool_calls(_SESSION)
    finally:
        reset_current_runtime_session_id(token)
        discard_runtime_session_tool_calls(_SESSION)


_RUNTIME_NAME_FORMS = [
    "mcp__finance__track_bill",  # Claude Code / Codex with the butler as server alias
    "finance_track_bill",  # OpenCode: <butler>_<tool>
    "track_bill",  # Gemini: bare function name
    "mcp__other__track_bill",  # a server alias that is not the butler name
]


def _parser_record(
    arguments: dict[str, Any], call_id: str = "t1", name: str = "mcp__finance__track_bill"
) -> dict[str, Any]:
    return {"id": call_id, "name": name, "input": arguments}


def _persisted(records: list[dict[str, Any]]) -> str:
    """What the sessions row would contain for these records."""
    return json.dumps(_sanitize_json_value(records), sort_keys=True)


_OMITTING_DEFAULTS = {"amount": 5, "payment_destination": _SENTINEL}
_SPELLING_EVERYTHING = {"amount": 5, "payment_destination": _SENTINEL, "currency": "USD"}


@pytest.mark.parametrize(
    "arguments",
    [_OMITTING_DEFAULTS, _SPELLING_EVERYTHING],
    ids=["defaults-omitted", "defaults-spelled-out"],
)
@pytest.mark.parametrize("name", _RUNTIME_NAME_FORMS[:3])
async def test_one_call_is_one_row_and_the_sentinel_is_never_persisted(
    arguments: dict[str, Any], name: str
) -> None:
    executed = await _executed_calls([arguments])

    merged = _merge_tool_call_records(
        [_parser_record(arguments, name=name)], executed, butler_name="finance"
    )

    assert len(merged) == 1
    assert merged[0]["name"] == "track_bill"
    assert merged[0]["id"] == "t1"
    assert merged[0]["outcome"] == "success"
    assert _SENTINEL not in _persisted(merged)


async def test_two_calls_with_omitted_defaults_pair_in_call_order() -> None:
    first = {"amount": 5, "payment_destination": _SENTINEL}
    second = {"amount": 9, "payment_destination": _SENTINEL + "-b"}
    executed = await _executed_calls([first, second])

    merged = _merge_tool_call_records(
        [_parser_record(first, "t1"), _parser_record(second, "t2")],
        executed,
        butler_name="finance",
    )

    assert [call["id"] for call in merged] == ["t1", "t2"]
    assert all(call["outcome"] == "success" for call in merged)
    assert _SENTINEL not in _persisted(merged)


@pytest.mark.parametrize("name", _RUNTIME_NAME_FORMS)
def test_a_parser_record_with_no_daemon_counterpart_is_also_stripped(name: str) -> None:
    arguments = {"amount": 5, "payment_destination": _SENTINEL, "butler": "finance"}

    merged = _merge_tool_call_records(
        [_parser_record(arguments, name=name)],
        [{"name": "other_tool", "outcome": "success"}],
        butler_name="finance",
    )
    parser_only = _merge_tool_call_records(
        [_parser_record(arguments, name=name)], [], butler_name="finance"
    )

    for records in (merged, parser_only):
        assert _SENTINEL not in _persisted(records)
        parsed = next(call for call in records if call["name"].endswith("track_bill"))
        assert parsed["input"] == {"butler": "finance"}


@pytest.mark.parametrize("key", ["input", "args", "arguments", "parameters"])
def test_every_raw_argument_key_a_parser_may_use_is_stripped(key: str) -> None:
    record = {"id": "t1", "name": "mcp__finance__track_bill", key: {"payment": _SENTINEL}}

    merged = _merge_tool_call_records([record], [], butler_name="finance")

    assert _SENTINEL not in _persisted(merged)
    assert key not in merged[0] or merged[0][key] == {}


def test_a_string_encoded_parser_input_is_stripped_too() -> None:
    record = _parser_record({})
    record["input"] = json.dumps({"payment_destination": _SENTINEL, "butler": "finance"})

    merged = _merge_tool_call_records([record], [], butler_name="finance")

    assert _SENTINEL not in _persisted(merged)
    assert merged[0]["input"] == {"butler": "finance"}


def test_allowlisted_fields_survive_on_a_parser_record() -> None:
    arguments = {"target_butler": "health", "prompt": "check in", "other": _SENTINEL}

    merged = _merge_tool_call_records(
        [{"id": "t1", "name": "mcp__switchboard__route_to_butler", "input": arguments}],
        [],
        butler_name="switchboard",
    )

    assert merged[0]["input"] == {"target_butler": "health", "prompt": "check in"}


def test_the_per_tool_allowlist_applies_to_the_parser_record() -> None:
    arguments = {"date_label": "2026-03-08", "timezone": "UTC", "private_context": _SENTINEL}

    merged = _merge_tool_call_records(
        [{"id": "t1", "name": "mcp__chronicler__chronicler_day_close_bundle", "input": arguments}],
        [],
        butler_name="chronicler",
    )

    assert merged[0]["input"] == {"date_label": "2026-03-08", "timezone": "UTC"}
    assert _SENTINEL not in _persisted(merged)


def test_runtime_shell_commands_keep_their_raw_input() -> None:
    record = {"id": "c1", "name": "command_execution", "input": {"command": "ls -1"}}

    merged = _merge_tool_call_records([record], [], butler_name="finance")

    assert merged == [record]


def test_the_persisted_fingerprint_is_keyed_not_a_plain_sha256() -> None:
    payload = {"amount": 5, "payment_destination": _SENTINEL}
    plain = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    fingerprint = fingerprint_tool_call_payload(payload)

    assert fingerprint != plain
    assert fingerprint == fingerprint_tool_call_payload(dict(reversed(list(payload.items()))))
    assert fingerprint != fingerprint_tool_call_payload({**payload, "amount": 6})


async def test_a_daemon_record_fingerprint_is_not_the_plain_sha256_of_the_full_input() -> None:
    executed = await _executed_calls([_OMITTING_DEFAULTS])
    full_input = {**_OMITTING_DEFAULTS, "currency": "USD"}
    plain = hashlib.sha256(
        json.dumps(full_input, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    assert executed[0]["input_fingerprint"] != plain
    assert _SENTINEL not in json.dumps(executed)


def test_stripped_parser_records_with_different_inputs_are_not_a_degenerate_loop() -> None:
    records = [
        {"id": f"t{i}", "name": "mcp__finance__track_bill", "input": {"payment": f"{_SENTINEL}{i}"}}
        for i in range(8)
    ]

    merged = _merge_tool_call_records(records, [], butler_name="finance")

    assert _check_degenerate_tool_loop(merged, consecutive_threshold=3) is None


def test_identical_stripped_parser_records_still_count_as_a_loop() -> None:
    records = [
        {"id": f"t{i}", "name": "mcp__finance__track_bill", "input": {"payment": _SENTINEL}}
        for i in range(4)
    ]

    merged = _merge_tool_call_records(records, [], butler_name="finance")

    assert _check_degenerate_tool_loop(merged, consecutive_threshold=3) is not None


def test_calls_to_different_tools_never_pair_on_order_alone() -> None:
    merged = _merge_tool_call_records(
        [_parser_record({"amount": 5})],
        [{"name": "list_bills", "input_fingerprint": "unrelated", "outcome": "success"}],
        butler_name="finance",
    )

    assert sorted(call["name"] for call in merged) == ["list_bills", "track_bill"]
    assert next(call for call in merged if call["name"] == "track_bill").get("outcome") is None


def test_a_tool_whose_bare_name_starts_with_the_butler_name_keeps_it_when_paired() -> None:
    # The parser reports mcp__chronicler__chronicler_day_close_bundle; the daemon records the
    # bare name chronicler_day_close_bundle. Normalizing the bare name again would strip the
    # tool's own butler prefix, and the day-close writer looks the tool up by that bare name.
    parser = {
        "id": "t1",
        "name": "mcp__chronicler__chronicler_day_close_bundle",
        "input": {"date_label": "2026-03-08", "timezone": "UTC"},
    }
    executed = {
        "name": "chronicler_day_close_bundle",
        "input": {"date_label": "2026-03-08", "timezone": "UTC"},
        "input_fingerprint": "includes-bound-defaults",
        "outcome": "success",
    }

    paired = _merge_tool_call_records([parser], [executed], butler_name="chronicler")
    parser_only = _merge_tool_call_records([parser], [], butler_name="chronicler")

    assert [call["name"] for call in paired] == ["chronicler_day_close_bundle"]
    assert paired[0]["id"] == "t1"
    assert paired[0]["outcome"] == "success"
    assert [call["name"] for call in parser_only] == ["chronicler_day_close_bundle"]


async def test_pass_one_pairs_by_fingerprint_for_a_tool_named_with_its_butler_prefix() -> None:
    """``finance_track_bill`` keeps its prefix as a bare name, so one normalization pass leaves
    the daemon and parser names different; fingerprint pairing must still find the right call
    when the daemon completes the calls in the opposite order to the parser's emission."""
    server = FastMCP("finance")
    proxy = _SpanWrappingMCP(server, "finance", module_name="finance")

    @proxy.tool()
    async def finance_track_bill(amount: int, currency: str = "USD") -> dict[str, str]:
        return {"status": "ok"}

    first = {"amount": 5, "currency": "USD"}
    second = {"amount": 9, "currency": "USD"}
    token = set_current_runtime_session_id(_SESSION)
    try:
        async with Client(server) as client:
            await client.call_tool("finance_track_bill", second)
            await client.call_tool("finance_track_bill", first)
        executed = consume_runtime_session_tool_calls(_SESSION)
    finally:
        reset_current_runtime_session_id(token)
        discard_runtime_session_tool_calls(_SESSION)

    parsed = [
        {"id": "t1", "name": "mcp__finance__finance_track_bill", "input": first},
        {"id": "t2", "name": "mcp__finance__finance_track_bill", "input": second},
    ]
    merged = _merge_tool_call_records(parsed, executed, butler_name="finance")

    assert [call["id"] for call in merged] == ["t2", "t1"]
    assert all(call["name"] == "finance_track_bill" for call in merged)
