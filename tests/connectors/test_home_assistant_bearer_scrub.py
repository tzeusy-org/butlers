"""Prospective ordinary-consumer regressions; synthetic data only.

Spec: REQ-connector-home-assistant-001, REQ-connector-home-assistant-002.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import sys
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers import ingestion_bearer_scrub
from butlers.connectors.discretion import DiscretionEvaluator
from butlers.connectors.home_assistant import (
    HAConnector,
    HAConnectorConfig,
    HAEventReorderBuffer,
    HAWebSocketClient,
    _emit_history_wellness_measurement,
    _make_event_dispatcher,
    persist_ha_history,
)
from butlers.connectors.home_assistant_filter import HAFilterPersistence
from butlers.connectors.home_assistant_pipeline import HAFilterPipeline, HAFilterPipelineConfig
from butlers.connectors.home_assistant_rest import (
    EntityStateSnapshot,
    HAStateCache,
    build_rest_state_changed_event,
)
from butlers.connectors.home_assistant_wellness import WellnessClassifier
from butlers.testing.clock import FakeClock

pytestmark = pytest.mark.unit

_ENDPOINT = "home_assistant:homeassistant.test:8123"
_TIME = "2026-10-15T09:00:00+00:00"
_CODE = "482913"
_URL = "https://acct.example.com/reset-password?token=abc123def456"
_ORDINARY_COPY_CONTROLS = {
    "amount_text": "482913.00",
    "amount_value": 482913.0,
    "fraction": ".482913",
    "fraction_prose": "Ratio .482913",
    "fraction_quoted": '".482913"',
    "fraction_bracketed": "(.482913)",
    "fraction_signed": "-.482913",
    "fraction_positive": "+.482913",
    "fraction_comma": ",482913",
    "ratio_component": ":482913",
    "fraction_exponent": ".482913e2",
    "amount_percent": "482913%",
    "signed_value": "+482913",
    "threshold_value": "<482913",
    "order_id": "INV-482913-2026",
    "unicode_id": "α482913β",
    "underscored_id": "item_482913_value",
    "date": "2026-10-15",
    "postal_note": "Zip code 90210",
}


def _event(entity="sensor.phone_last_notification", state="Your code is 482913"):
    return {
        "event_type": "state_changed",
        "time_fired": _TIME,
        "context": {"id": "synthetic-context"},
        "data": {
            "entity_id": entity,
            "old_state": {"state": "Your login code is 771122", "attributes": {"note": _URL}},
            "new_state": {
                "state": state,
                "attributes": {
                    "friendly_name": "Synthetic notification",
                    "nested": [{"note": "Your code is 482913", "link": _URL}],
                },
            },
        },
    }


def _wire(*, db_pool=None, evaluator=None):
    connector = HAConnector(HAConnectorConfig(switchboard_mcp_url="http://switchboard.test/mcp"))
    connector._set_endpoint_identity("http://homeassistant.test:8123")
    connector._mcp_client = MagicMock(call_tool=AsyncMock(return_value={"status": "accepted"}))
    connector._starting = False
    persistence = HAFilterPersistence(_ENDPOINT, None, AsyncMock())
    dispatch = _make_event_dispatcher(
        connector=connector,
        config=connector._config,
        db_pool=db_pool,
        pipeline=HAFilterPipeline(
            config=HAFilterPipelineConfig(domain_allowlist=connector._config.domain_allowlist),
            evaluator=evaluator,
            metrics=connector._ha_metrics,
        ),
        wellness_classifier=WellnessClassifier(),
        endpoint_identity=_ENDPOINT,
        resume_ts=None,
        ha_filter_persistence=persistence,
        global_ingestion_policy=None,
    )
    return connector, persistence, dispatch


def _no_bearer(value):
    rendered = json.dumps(value, sort_keys=True, default=str)
    for secret in (_CODE, "771122", _URL, "abc123def456"):
        assert secret not in rendered


async def _ordinary_consumer_outputs():
    """Capture real consumer outputs using the retained pre-fix owner fixtures."""
    from tests.connectors.test_home_assistant_global_skip import (
        _build_connector,
        _make_binary_sensor_event,
        _make_person_event,
        _make_weight_event,
    )
    from tests.connectors.test_home_assistant_global_skip import (
        _wire as wire_original,
    )

    clock = FakeClock(datetime(2026, 10, 15, 9, tzinfo=UTC))

    class FixedClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls.fromtimestamp(clock.now().timestamp(), tz=UTC).astimezone(tz)

    cases = {
        "decimal": _make_binary_sensor_event(
            entity_id="sensor.synthetic_temperature",
            old_state="21.50",
            new_state="22.00",
            device_class="temperature",
        ),
        "enum": _make_binary_sensor_event(entity_id="binary_sensor.synthetic_door"),
        "presence": _make_person_event(entity_id="person.synthetic"),
        "measurement": _make_weight_event(
            entity_id="sensor.synthetic_weight", old_state="72.0", new_state="72.4"
        ),
    }
    outputs = {}
    for name, event in cases.items():
        pool = MagicMock(execute=AsyncMock())
        connector = _build_connector()
        dispatch, _ = wire_original(connector, None, dispatcher_db_pool=pool)
        await dispatch("state_changed", event)
        filtered = HAFilterPersistence(_ENDPOINT, None, AsyncMock())
        data = event["data"]
        args = dict(
            entity_id=data["entity_id"],
            ha_event=event,
            time_fired=event["time_fired"],
            old_state=data["old_state"],
            new_state=data["new_state"],
        )
        with patch("butlers.connectors.filtered_event_buffer.datetime", FixedClock):
            filtered.record_domain_excluded(**args, domain="synthetic")
            filtered.record_insignificant_delta(**args, device_class="temperature", delta=0.1)
            filtered.record_discretion_ignore(**args)
            filtered.record_global_skip(**args, rule_type="source_channel")
        # Compare complete envelopes and actual SQL value/representation, not
        # an expected rendering reimplemented beside the producer.
        outputs[name] = {
            "event": event,
            "submissions": [
                dict(args=call.args, kwargs=call.kwargs)
                for call in connector._mcp_client.call_tool.call_args_list
            ],
            "database_calls": [
                dict(args=call.args, kwargs=call.kwargs) for call in pool.execute.call_args_list
            ],
            "filtered_rows": filtered._buffer._rows,
        }
    return outputs


@pytest.mark.asyncio
async def test_ha_content_is_withheld_at_submission_and_history_consumers():
    """Both dispatch transports, recorder wellness, person history, numeric control."""
    assert (
        ingestion_bearer_scrub.scrub_json(
            _ORDINARY_COPY_CONTROLS, provider_domain="home_assistant"
        )[0]
        == _ORDINARY_COPY_CONTROLS
    )
    # Neither the friendly name nor bare state is independently recognized.
    # The actual normalization context supplies the existing detector's keyword.
    for old_value, new_value in (
        ("unknown", "482913"),
        ("771122", "482913"),
        ("unknown", "ABC-482913"),
        ("unknown", "482 913"),
        ("unknown", "482-913"),
    ):
        llm = AsyncMock()
        evaluator = DiscretionEvaluator(_ENDPOINT, llm)
        connector, _, dispatch = _wire(evaluator=evaluator)
        split = _event(state=new_value)
        split["time_fired"] = datetime.now(UTC).isoformat()
        split["data"]["old_state"] = {"state": old_value, "attributes": {}}
        split["data"]["new_state"]["attributes"] = {
            "friendly_name": "Your verification code",
            "echo": new_value,
            "quoted_echo": f'"{new_value}"',
            "bracketed_echo": f"[{new_value}]",
            "nested_echo": f"“({new_value})”",
            "prose_echo": f"Echo ({new_value}).",
            "repeated_echo": f"{new_value} {new_value}",
        }
        original = copy.deepcopy(split)
        await dispatch("state_changed", split)
        assert len(evaluator.window.entries) == 1
        _no_bearer([entry.text for entry in evaluator.window.entries])
        llm.assert_not_awaited()  # Real HA weight bypass still retains safe context.
        assert connector._mcp_client.call_tool.await_count == 1
        _no_bearer(connector._mcp_client.call_tool.call_args.args[1])
        assert new_value not in json.dumps(connector._mcp_client.call_tool.call_args.args[1])
        assert split == original

        queued = []

        async def capture_split(event_type, event, transport, *, queued=queued):
            queued.append(event)

        reorder = HAEventReorderBuffer(submit=capture_split, window_s=0, max_size=100)
        await reorder.add("state_changed", split)
        await reorder.flush_all()
        assert len(queued) == 1
        _no_bearer(queued)

        pool = MagicMock(execute=AsyncMock())
        assert await persist_ha_history(
            pool,
            entity_id="person.synthetic",
            state=new_value,
            attributes=split["data"]["new_state"]["attributes"],
            recorded_at=datetime(2026, 10, 15, 9, tzinfo=UTC),
        )
        _no_bearer(pool.execute.call_args.args)

    # Each state owns its label. New metadata cannot erase a recognized old
    # context, including when the entity has been removed altogether.
    for new_state in ({"state": "unknown", "attributes": {"friendly_name": "Ordinary"}}, None):
        old_context = _event()
        old_context["time_fired"] = datetime.now(UTC).isoformat()
        old_context["data"]["old_state"] = {
            "state": "482913",
            "attributes": {"friendly_name": "Your verification code", "echo": "482913"},
        }
        old_context["data"]["new_state"] = new_state
        frozen_old_context = copy.deepcopy(old_context)
        llm = AsyncMock()
        evaluator = DiscretionEvaluator(_ENDPOINT, llm)
        connector, _, dispatch = _wire(evaluator=evaluator)
        await dispatch("state_changed", old_context)
        assert len(evaluator.window.entries) == 1
        _no_bearer([entry.text for entry in evaluator.window.entries])
        llm.assert_not_awaited()
        assert connector._mcp_client.call_tool.await_count == 1
        _no_bearer(connector._mcp_client.call_tool.call_args.args[1])
        delivered_old_context = AsyncMock()
        old_queue = HAEventReorderBuffer(submit=delivered_old_context, window_s=0, max_size=100)
        await old_queue.add("state_changed", old_context)
        await old_queue.flush_all()
        assert delivered_old_context.await_count == 1
        _no_bearer(delivered_old_context.call_args.args[1])
        assert old_context == frozen_old_context

    # Selected-code echoes are copies; a longer ordinary identifier containing
    # the same digits is not. Drive the real MCP and direct history consumers.
    mixed = _event(state="482913")
    mixed["time_fired"] = datetime.now(UTC).isoformat()
    mixed["data"]["old_state"] = {"state": "unknown", "attributes": {}}
    ordinary_fields = {
        "order_number": "14829130",
        "order_note": "Order 14829130",
        "numeric": 14829130,
        "postal": "4829130",
        **_ORDINARY_COPY_CONTROLS,
    }
    mixed["data"]["new_state"]["attributes"] = {
        "friendly_name": "Your verification code",
        "echo": "482913",
        "echo_note": "Echo 482913.",
        **ordinary_fields,
    }
    original_mixed = copy.deepcopy(mixed)
    connector, _, dispatch = _wire()
    await dispatch("state_changed", mixed)
    submitted = connector._mcp_client.call_tool.call_args.args[1]
    for state in (
        submitted["payload"]["raw"]["new_state"],
        submitted["payload"]["raw"]["ha_event"]["data"]["new_state"],
    ):
        _no_bearer([state["state"], state["attributes"]["echo"], state["attributes"]["echo_note"]])
        assert {key: state["attributes"][key] for key in ordinary_fields} == ordinary_fields
    mixed_pool = MagicMock(execute=AsyncMock())
    assert await persist_ha_history(
        mixed_pool,
        entity_id="person.synthetic",
        state="482913",
        attributes=mixed["data"]["new_state"]["attributes"],
        recorded_at=datetime(2026, 10, 15, 9, tzinfo=UTC),
    )
    stored_attributes = mixed_pool.execute.call_args.args[3]
    assert {key: stored_attributes[key] for key in ordinary_fields} == ordinary_fields
    _no_bearer([mixed_pool.execute.call_args.args[2], stored_attributes["echo"]])
    mixed_reordered = []

    async def receive_mixed(event_type, safe, transport):
        mixed_reordered.append(safe)

    mixed_queue = HAEventReorderBuffer(submit=receive_mixed, window_s=0, max_size=100)
    await mixed_queue.add("state_changed", mixed)
    await mixed_queue.flush_all()
    assert len(mixed_reordered) == 1
    queued_state = mixed_reordered[0]["data"]["new_state"]
    _no_bearer([queued_state["state"], queued_state["attributes"]["echo"]])
    assert {key: queued_state["attributes"][key] for key in ordinary_fields} == ordinary_fields
    assert mixed == original_mixed

    # Count actual interpreter work through the real reorder consumer. This
    # checks scaling rather than a machine-dependent elapsed-time threshold.
    # Distinct reset-link domains are ordinary accepted provider input, not
    # pre-made placeholders. Repetitive text also exercises alignment work.
    async def measured_work(count):
        event = _event(state="482913")
        event["data"]["old_state"] = {"state": "unknown", "attributes": {}}
        event["data"]["new_state"]["attributes"] = {
            "friendly_name": " ".join(
                f"https://p{i}.example/reset?token=synthetic" for i in range(count)
            )
            + " repeated " * count
            + " Your verification code",
        }
        delivered = []

        async def receive_work(event_type, safe, transport):
            delivered.append(safe)

        queue = HAEventReorderBuffer(submit=receive_work, window_s=0, max_size=100)
        work = 0

        def count_work(frame, event, arg):
            nonlocal work
            if event == "line":
                work += 1
            return count_work

        previous = sys.gettrace()
        try:
            sys.settrace(count_work)
            await queue.add("state_changed", event)
            await queue.flush_all()
        finally:
            sys.settrace(previous)
        assert len(delivered) == 1
        _no_bearer(delivered)
        assert event["data"]["new_state"]["state"] == "482913"
        return work

    small_work = await measured_work(64)
    large_work = await measured_work(128)
    assert large_work < 3 * small_work, (small_work, large_work)

    # An unrelated bare numeric sensor still reaches the real context/MCP unchanged.
    evaluator = DiscretionEvaluator(_ENDPOINT, AsyncMock())
    connector, _, dispatch = _wire(evaluator=evaluator)
    numeric = _event("sensor.synthetic_count", "482913")
    numeric["time_fired"] = datetime.now(UTC).isoformat()
    numeric["data"]["old_state"] = {"state": "482912", "attributes": {}}
    numeric["data"]["new_state"]["attributes"] = {"friendly_name": "Ordinary count"}
    await dispatch("state_changed", numeric)
    assert evaluator.window.entries[0].text == "Ordinary count: 482912 -> 482913"
    assert (
        connector._mcp_client.call_tool.call_args.args[1]["payload"]["raw"]["new_state"]["state"]
        == "482913"
    )

    for transport in (None, "rest_fallback"):
        connector, _, dispatch = _wire()
        original = _event()
        if transport is not None:
            data = original["data"]
            previous = EntityStateSnapshot.from_ha_state(
                {
                    **data["old_state"],
                    "entity_id": data["entity_id"],
                }
            )
            current = EntityStateSnapshot.from_ha_state(
                {
                    **data["new_state"],
                    "entity_id": data["entity_id"],
                }
            )
            cache = HAStateCache()
            cache.apply([previous])
            assert len(cache.diff([current])) == 1
            original = build_rest_state_changed_event(previous, current, _TIME)
        frozen = copy.deepcopy(original)
        if transport is None:
            client = HAWebSocketClient(
                ha_base_url="http://homeassistant.test:8123",
                ha_access_token="synthetic",
                dispatch=dispatch,
            )
            await client._dispatch_message({"type": "event", "event": original})
        else:
            await dispatch("state_changed", original, transport)
        calls = connector._mcp_client.call_tool.call_args_list
        assert len(calls) == 1 and calls[0].args[0] == "ingest"
        submitted = calls[0].args[1]
        _no_bearer(submitted)
        assert submitted["control"]["bearer_scrubbed"] is True
        assert submitted["sender"]["identity"] == original["data"]["entity_id"]
        assert submitted["event"]["observed_at"] == _TIME
        assert original == frozen

    # The same state/attribute protection is required before the reorder queue,
    # without mutating the provider-owned object or collapsing event membership.
    delivered = []

    async def receive(event_type, event, transport):
        delivered.append((event_type, event, transport))

    reorder = HAEventReorderBuffer(submit=receive, window_s=0, max_size=100)
    original = _event()
    await reorder.add("state_changed", original, "rest_fallback")
    await reorder.flush_all()
    assert len(delivered) == 1 and delivered[0][2] == "rest_fallback"
    _no_bearer(delivered[0][1])
    assert original["data"]["new_state"]["state"] == "Your code is 482913"

    from butlers.connectors.home_assistant_scrub import scrub_ha_event

    ordinary = await _ordinary_consumer_outputs()
    encoded = json.dumps(ordinary, sort_keys=True, default=str, separators=(",", ":")).encode()
    # Original source 08d03fa7: full consumer snapshot retained in implementation evidence.
    assert (
        hashlib.sha256(encoded).hexdigest()
        == "125cbcc8d7d0a809659f5d68bcf760b392efc488cc729cd42b1e89dde0bff053"
    )

    for state in (
        "21.5",
        "22.00",
        "482913",
        "home",
        "not_home",
        "on",
        "off",
        "unknown",
        "unavailable",
    ):
        normal = _event("sensor.normal", state)
        normal["data"]["old_state"] = {"state": state, "attributes": {"unit": "kg"}}
        normal["data"]["new_state"]["attributes"] = {
            "friendly_name": "Ordinary 2026-10-15",
            "value": 72.4,
            "note": "Zip code 90210; order 12345678",
        }
        before = json.dumps(normal, sort_keys=True, separators=(",", ":"))
        assert json.dumps(scrub_ha_event(normal), sort_keys=True, separators=(",", ":")) == before
        assert scrub_ha_event(scrub_ha_event(normal)) == normal

    pool = MagicMock(execute=AsyncMock())
    person = _event("person.synthetic", "home")
    _, _, person_dispatch = _wire(db_pool=pool)
    await person_dispatch("state_changed", person)
    history_calls = [
        call.args
        for call in pool.execute.call_args_list
        if "INSERT INTO connectors.home_assistant_history" in call.args[0]
    ]
    assert len(history_calls) == 1
    history_call = history_calls[0]
    assert history_call[1:3] == ("person.synthetic", "home")
    _no_bearer(history_call[3])

    weight = _event("sensor.body_weight", "72.4")
    weight["data"]["new_state"]["attributes"].update(
        device_class="weight", unit_of_measurement="kg"
    )
    live_connector, _, live_dispatch = _wire()
    await live_dispatch("state_changed", weight)
    live_envelopes = [call.args[1] for call in live_connector._mcp_client.call_tool.call_args_list]
    assert [envelope["source"]["channel"] for envelope in live_envelopes] == [
        "home_assistant",
        "wellness",
    ]
    for envelope in live_envelopes:
        _no_bearer(envelope)
    client = MagicMock(call_tool=AsyncMock(return_value={"status": "accepted"}))
    assert await _emit_history_wellness_measurement(
        mcp_client=client,
        classifier=WellnessClassifier(),
        endpoint_identity=_ENDPOINT,
        event=weight,
        metrics=None,
    )
    wellness = client.call_tool.call_args.args[1]
    _no_bearer(wellness)
    assert wellness["source"]["channel"] == "wellness"
    assert wellness["payload"]["raw"]["wellness_measurement"]["value"] == 72.4


@pytest.mark.asyncio
async def test_ha_filter_persistence_and_replay_withhold_all_content_copies():
    """Drive the real HA adapter and shared buffer; do not mock the sanitizer."""
    split = _event(state="482913")
    split["data"]["old_state"] = {"state": "unknown", "attributes": {}}
    split["data"]["new_state"]["attributes"] = {"friendly_name": "Your verification code"}
    split_persistence = HAFilterPersistence(_ENDPOINT, None, AsyncMock())
    args = dict(
        entity_id=split["data"]["entity_id"],
        ha_event=split,
        time_fired=_TIME,
        friendly_name="Your verification code",
        old_state=split["data"]["old_state"],
        new_state=split["data"]["new_state"],
    )
    split_persistence.record_domain_excluded(**args, domain="sensor")
    split_persistence.record_insignificant_delta(**args, device_class="temperature", delta=0.1)
    split_persistence.record_discretion_ignore(**args)
    split_persistence.record_global_skip(**args, rule_type="source_channel")
    assert len(split_persistence) == 4
    for row in split_persistence._buffer._rows:
        _no_bearer(row)

    mixed = copy.deepcopy(split)
    ordinary_fields = {
        "order_number": "14829130",
        "order_note": "Order 14829130",
        **_ORDINARY_COPY_CONTROLS,
    }
    mixed["data"]["new_state"]["attributes"].update(echo="482913", **ordinary_fields)
    mixed_submit = AsyncMock()
    mixed_persistence = HAFilterPersistence(_ENDPOINT, None, mixed_submit)
    mixed_args = {**args, "ha_event": mixed, "new_state": mixed["data"]["new_state"]}
    mixed_persistence.record_domain_excluded(**mixed_args, domain="sensor")
    mixed_persistence.record_insignificant_delta(
        **mixed_args, device_class="temperature", delta=0.1
    )
    mixed_persistence.record_discretion_ignore(**mixed_args)
    mixed_persistence.record_global_skip(**mixed_args, rule_type="source_channel")
    assert len(mixed_persistence) == 4
    for row in mixed_persistence._buffer._rows:
        state = row[9]["payload"]["raw"]["new_state"]
        assert {key: state["attributes"][key] for key in ordinary_fields} == ordinary_fields
        _no_bearer([state["state"], state["attributes"]["echo"]])
    mixed_payload = mixed_persistence._buffer._rows[0][9]
    await mixed_persistence._submit_scrubbed_replay(mixed_payload)
    for payload in (mixed_payload, mixed_submit.call_args.args[0]):
        for state in (
            payload["payload"]["raw"]["new_state"],
            payload["payload"]["raw"]["ha_event"]["data"]["new_state"],
        ):
            _no_bearer([state["state"], state["attributes"]["echo"]])
            assert {key: state["attributes"][key] for key in ordinary_fields} == ordinary_fields
    assert mixed["data"]["new_state"]["attributes"]["echo"] == "482913"

    for new_state in ({"state": "unknown", "attributes": {"friendly_name": "Ordinary"}}, None):
        old_context = _event()
        old_context["data"]["old_state"] = {
            "state": "482913",
            "attributes": {"friendly_name": "Your verification code", "echo": "482913"},
        }
        old_context["data"]["new_state"] = new_state
        frozen_old_context = copy.deepcopy(old_context)
        old_submit = AsyncMock()
        old_persistence = HAFilterPersistence(_ENDPOINT, None, old_submit)
        old_args = {
            "entity_id": old_context["data"]["entity_id"],
            "ha_event": old_context,
            "time_fired": _TIME,
            "friendly_name": "Ordinary",
            "old_state": old_context["data"]["old_state"],
            "new_state": new_state,
        }
        old_persistence.record_domain_excluded(**old_args, domain="sensor")
        old_persistence.record_insignificant_delta(
            **old_args, device_class="temperature", delta=0.1
        )
        old_persistence.record_discretion_ignore(**old_args)
        old_persistence.record_global_skip(**old_args, rule_type="source_channel")
        assert len(old_persistence) == 4
        for row in old_persistence._buffer._rows:
            _no_bearer(row)
            await old_persistence._submit_scrubbed_replay(row[9])
        assert old_submit.await_count == 4
        for call in old_submit.call_args_list:
            _no_bearer(call.args[0])
        assert old_context == frozen_old_context

    submit = AsyncMock()
    persistence = HAFilterPersistence(_ENDPOINT, None, submit)
    event = _event()
    common = {
        "entity_id": event["data"]["entity_id"],
        "ha_event": event,
        "time_fired": _TIME,
        "old_state": event["data"]["old_state"],
        "new_state": event["data"]["new_state"],
        "friendly_name": "Your code is 482913",
    }
    persistence.record_domain_excluded(**common, domain="sensor")
    persistence.record_insignificant_delta(**common, device_class="temperature", delta=0.1)
    persistence.record_discretion_ignore(**common)
    persistence.record_global_skip(**common, rule_type="source_channel")
    assert len(persistence) == 4
    assert [row[7] for row in persistence._buffer._rows] == [
        "domain_excluded:sensor",
        "insignificant_delta:temperature:0.1",
        "discretion_ignore",
        "global_rule:skip:source_channel",
    ]
    for row in persistence._buffer._rows:
        _no_bearer(row)
        assert row[9]["control"]["bearer_scrubbed"] is True
    payload = copy.deepcopy(persistence._buffer._rows[0][9])
    payload["payload"]["normalized_text"] = "Your code is 482913"
    await persistence._submit_scrubbed_replay({"schema_version": "ingest.v1", **payload})
    assert submit.await_count == 1
    _no_bearer(submit.call_args.args[0])
    assert "482913" in payload["payload"]["normalized_text"]


@pytest.mark.asyncio
async def test_ha_scrubber_failure_withholds_raw_content_and_logs_no_exception(monkeypatch, caplog):
    """A raising real shared dependency must fail closed at every direct seam."""
    original_text_scrubber = ingestion_bearer_scrub.scrub_text
    evaluator = DiscretionEvaluator(_ENDPOINT, AsyncMock())
    context_pool = MagicMock(execute=AsyncMock())
    context_connector, context_persistence, context_dispatch = _wire(
        db_pool=context_pool, evaluator=evaluator
    )
    split = _event(state="482913")
    split["time_fired"] = datetime.now(UTC).isoformat()
    split["data"]["old_state"] = {"state": "unknown", "attributes": {}}
    split["data"]["new_state"]["attributes"] = {"friendly_name": "Your verification code"}

    def fail_assembled_text(text, **kwargs):
        if " -> " in text:
            raise RuntimeError("Your code is 482913; " + _URL)
        return original_text_scrubber(text, **kwargs)

    with monkeypatch.context() as patcher:
        patcher.setattr(ingestion_bearer_scrub, "scrub_text", fail_assembled_text)
        await context_dispatch("state_changed", split)
        assert evaluator.window.entries == []
        context_connector._mcp_client.call_tool.assert_not_awaited()
        context_pool.execute.assert_not_awaited()
        assert not context_persistence
        delivered = AsyncMock()
        reorder = HAEventReorderBuffer(submit=delivered, window_s=0, max_size=100)
        await reorder.add("state_changed", split)
        assert len(reorder) == 0
        delivered.assert_not_awaited()

    checkpoint_pool = MagicMock(execute=AsyncMock())
    connector, persistence, dispatch = _wire(db_pool=checkpoint_pool)
    pool = MagicMock(execute=AsyncMock())
    event = _event()
    original = ingestion_bearer_scrub.scrub_json

    def broken(*args, **kwargs):
        raise RuntimeError("Your code is 482913; " + _URL)

    with monkeypatch.context() as patcher:
        patcher.setattr(ingestion_bearer_scrub, "scrub_json", broken)
        await dispatch("state_changed", event)
        connector._mcp_client.call_tool.assert_not_awaited()
        assert not persistence
        checkpoint_pool.execute.assert_not_awaited()
        from butlers.connectors.home_assistant_scrub import HABearerScrubError

        assert (
            await persist_ha_history(
                pool,
                entity_id="person.synthetic",
                state="home",
                attributes=event["data"]["new_state"]["attributes"],
                recorded_at=datetime(2026, 10, 15, 9, tzinfo=UTC),
            )
            is False
        )
        pool.execute.assert_not_awaited()
        with pytest.raises(HABearerScrubError, match="Home Assistant content scrub failed"):
            persistence.record_discretion_ignore(
                entity_id=event["data"]["entity_id"], ha_event=event, time_fired=_TIME
            )
        assert not persistence
        assert (
            await _emit_history_wellness_measurement(
                mcp_client=connector._mcp_client,
                classifier=WellnessClassifier(),
                endpoint_identity=_ENDPOINT,
                event=event,
                metrics=None,
            )
            is False
        )
    assert ingestion_bearer_scrub.scrub_json is original
    _no_bearer(caplog.text)
    assert all(record.exc_info is None for record in caplog.records)
    # Recovery is positive evidence that a fail-closed test did not disable HA.
    await dispatch("state_changed", _event("sensor.healthy", "A normal notification"))
    assert connector._mcp_client.call_tool.await_count == 1
    assert len(persistence) == 0
    failing_submit = AsyncMock(side_effect=RuntimeError("Your code is 482913"))
    replay = HAFilterPersistence(_ENDPOINT, None, failing_submit)
    with pytest.raises(HABearerScrubError) as refusal:
        await replay._submit_scrubbed_replay({"payload": {"raw": {}, "normalized_text": "safe"}})
    assert str(refusal.value) == "Home Assistant replay submission failed"
    assert refusal.value.__suppress_context__ is True

    cancelled = HAFilterPersistence(
        _ENDPOINT, None, AsyncMock(side_effect=asyncio.CancelledError())
    )
    with pytest.raises(asyncio.CancelledError):
        await cancelled._submit_scrubbed_replay({"payload": {"raw": {}, "normalized_text": "safe"}})

    import aiohttp

    ws = HAWebSocketClient(
        ha_base_url="http://homeassistant.test:8123",
        ha_access_token="synthetic",
        dispatch=AsyncMock(),
    )
    ws._ws_connection = SimpleNamespace(
        closed=False,
        receive=AsyncMock(
            side_effect=[
                SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data="{broken Your code is 482913"),
                SimpleNamespace(type=aiohttp.WSMsgType.CLOSED),
            ]
        ),
    )
    await ws._message_loop()
    _no_bearer(caplog.text)
    assert "invalid JSON message withheld" in caplog.text
