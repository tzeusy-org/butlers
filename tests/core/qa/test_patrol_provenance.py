"""Shared startup contract selection preserves QA identity and ordering."""

from types import SimpleNamespace

import pytest

from butlers.core.qa.patrol_provenance import (
    QaPatrolContract,
    contract_from_module_config,
    qa_patrol_contract,
)

pytestmark = pytest.mark.unit

_SELECTED = QaPatrolContract(("infra_state", "session_records"), 3)
_OTHER = QaPatrolContract(("log_scanner",), 90)


@pytest.mark.parametrize(
    ("scenario", "configs", "expected"),
    [
        ("absent", [], None),
        (
            "non-QA decoy",
            [
                SimpleNamespace(name="general", qa_patrol_contract=_OTHER),
                SimpleNamespace(name="qa", qa_patrol_contract=_SELECTED),
            ],
            _SELECTED,
        ),
        (
            "first missing",
            [SimpleNamespace(name="qa"), SimpleNamespace(name="qa", qa_patrol_contract=_OTHER)],
            None,
        ),
        (
            "first None",
            [
                SimpleNamespace(name="qa", qa_patrol_contract=None),
                SimpleNamespace(name="qa", qa_patrol_contract=_OTHER),
            ],
            None,
        ),
        (
            "first of duplicates",
            [
                SimpleNamespace(name="qa", qa_patrol_contract=_SELECTED),
                SimpleNamespace(name="qa", qa_patrol_contract=_OTHER),
            ],
            _SELECTED,
        ),
        (
            "disabled module before enabled duplicate",
            [
                SimpleNamespace(
                    name="qa", qa_patrol_contract=contract_from_module_config({"enabled": False})
                ),
                SimpleNamespace(name="qa", qa_patrol_contract=_OTHER),
            ],
            None,
        ),
        ("configured QA", [SimpleNamespace(name="qa", qa_patrol_contract=_SELECTED)], _SELECTED),
    ],
)
def test_qa_patrol_contract_preserves_first_startup_object(
    scenario, configs, expected, monkeypatch
):
    import builtins
    import socket
    import time

    def forbidden(*args, **kwargs):
        raise AssertionError("Startup contract selection must not perform I/O or read a clock")

    # Repeated lookup has no runtime-state input, construction, I/O or clock dependency.
    with monkeypatch.context() as isolated:
        for owner, name in [
            (builtins, "open"),
            (socket, "socket"),
            (time, "time"),
            (time, "monotonic"),
        ]:
            isolated.setattr(owner, name, forbidden)
        assert qa_patrol_contract(iter(configs)) is expected
        assert qa_patrol_contract(configs) is expected

    if scenario == "absent":
        assert (
            qa_patrol_contract([SimpleNamespace(name="general", qa_patrol_contract=_OTHER)]) is None
        )
        with pytest.raises(AttributeError):
            qa_patrol_contract([SimpleNamespace(qa_patrol_contract=_SELECTED)])
        primary = RuntimeError("startup iteration failed")

        def broken_sequence():
            yield SimpleNamespace(name="general")
            raise primary

        with pytest.raises(RuntimeError) as caught:
            qa_patrol_contract(broken_sequence())
        assert caught.value is primary
    else:

        class UnreadLaterConfig:
            @property
            def name(self):
                raise AssertionError("Selection continued after the first QA config")

        assert qa_patrol_contract([*configs, UnreadLaterConfig()]) is expected
