"""D18 settings/output controls; synthetic observations confer no eligibility."""

import json

import pytest

from butlers.session_host.contracts import Refused
from butlers.session_host.model_function import OpenCodeModelFunctionSettings, structured_result
from butlers.session_host.opencode import OpenCodeSettings


def text_events(text):
    return b"\n".join(
        json.dumps(event).encode()
        for event in (
            {"type": "step_start", "part": {"type": "step-start"}},
            {"type": "text", "part": {"type": "text", "text": text}},
            {"type": "step_finish", "part": {"type": "step-finish", "reason": "stop"}},
        )
    )


def test_model_function_preset_has_no_goal_authority_and_denies_every_tool():
    settings = OpenCodeModelFunctionSettings("openai/test-model")
    rendered = settings.render(b"SYNTHETIC-API-KEY")
    config = json.loads(rendered)
    assert config["mcp"] == {}
    assert config["permission"] == {"*": "deny"}
    assert config["tools"] == {"*": False}
    assert config["agent"] == {
        "model_function": {
            "mode": "primary",
            "steps": 1,
            "permission": {"*": "deny"},
            "tools": {"*": False},
        }
    }
    assert config["plugin"] == config["instructions"] == []
    assert config["enabled_providers"] == ["openai"]
    assert config["provider"] == {
        "openai": {"options": {"apiKey": "{file:/invocation/model-credential}"}}
    }
    assert b"SYNTHETIC-API-KEY" not in rendered
    assert b"broker" not in rendered
    assert "model_function" in settings.command
    assert settings.command[settings.command.index("--title") + 1] == "Model function"
    assert config["compaction"] == {"auto": False}
    assert settings.identity != OpenCodeSettings(settings.model).identity
    assert settings.identity["dynamic_slots"] == []
    assert json.loads(settings.identity["template_utf8"]) == config
    assert dict(settings.environment) == {
        "PATH": "/runtime",
        "HOME": "/tmp/home",
        "BUTLERS_MODEL_CREDENTIAL": "/invocation/model-credential",
        "OPENCODE_CONFIG": "/invocation/opencode.jsonc",
    }
    # A result cannot mutate the next invocation's preset.
    config["permission"]["*"] = "allow"
    assert json.loads(settings.render(b"ANOTHER-SYNTHETIC-KEY"))["permission"] == {"*": "deny"}


@pytest.mark.parametrize("credential", [b"", b"{}", b" x", b"x\n", b"\xff"])
def test_model_function_refuses_non_api_credentials(credential):
    with pytest.raises(Refused, match="unsupported_opencode_credential"):
        OpenCodeModelFunctionSettings("openai/test-model").render(credential)


def test_model_function_requires_an_explicit_supported_model():
    with pytest.raises(Refused, match="unsupported_opencode_settings"):
        OpenCodeModelFunctionSettings("anthropic/unproven")


def test_model_function_accepts_only_structured_text_output():
    assert structured_result(text_events('{"verdict":"IGNORE"}')) == {"verdict": "IGNORE"}
    start, text, finish = text_events('{"verdict":"IGNORE"}').splitlines()
    # Native 1.17.7 publishes its completed text part after step_finish.
    assert structured_result(b"\n".join((start, finish, text))) == {"verdict": "IGNORE"}
    with pytest.raises(Refused, match="invalid_model_function_output"):
        structured_result(b"\n".join((start, finish, start, text, finish)))


@pytest.mark.parametrize(
    "event",
    [
        {"type": "tool_use", "part": {"tool": "bash"}},
        {"type": "text", "part": {"type": "tool", "tool": "read", "text": "{}"}},
        {"type": "unknown", "part": {}},
        {"type": "step_finish", "part": {"type": "step-finish", "reason": "tool-calls"}},
        {"type": "error", "error": {"message": "SYNTHETIC-PRIVATE-CONTENT"}},
    ],
)
def test_model_function_refuses_tool_or_unknown_events_even_with_valid_text(event):
    output = text_events("{}") + b"\n" + json.dumps(event).encode()
    with pytest.raises(Refused, match="invalid_model_function_output") as caught:
        structured_result(output)
    assert "SYNTHETIC-PRIVATE-CONTENT" not in str(caught.value)


@pytest.mark.parametrize("text", ["plain text", "[]", '{"v":NaN}', '{"v":1,"v":2}', "{} {}"])
def test_model_function_refuses_non_structured_or_ambiguous_results(text):
    with pytest.raises(Refused, match="invalid_model_function_output"):
        structured_result(text_events(text))


def test_model_function_bounds_output_before_parsing():
    for output in (b"", b"x" * (1024 * 1024 + 1), b"\xff", b'{"type":"text","type":"error"}'):
        with pytest.raises(Refused, match="invalid_model_function_output"):
            structured_result(output)
    maximum = json.dumps({"text": "x" * 65524})
    assert len(maximum.encode()) == 65536
    assert structured_result(text_events(maximum)) == {"text": "x" * 65524}
    with pytest.raises(Refused, match="invalid_model_function_output"):
        structured_result(text_events(json.dumps({"text": "x" * 65525})))


@pytest.mark.parametrize("missing", [0, 2])
def test_model_function_requires_one_complete_step(missing):
    events = text_events("{}").splitlines()
    assert structured_result(b"\n".join(events)) == {}
    events.pop(missing)
    with pytest.raises(Refused, match="invalid_model_function_output"):
        structured_result(b"\n".join(events))
