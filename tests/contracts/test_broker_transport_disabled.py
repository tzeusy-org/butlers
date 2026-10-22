"""Dark private transport factories never touch deployment resources."""

import pytest

from butlers.capability_broker.transport.client import build_client
from butlers.capability_broker.transport.config import TransportConfig
from butlers.capability_broker.transport.server import build_server

pytestmark = pytest.mark.unit


def test_absent_false_and_config_disabled_construct_no_resource():
    def forbidden(*args, **kwargs):
        pytest.fail("disabled transport constructed a resource")

    for raw in (None, {}, {"enabled": False, "key": forbidden, "endpoint": object()}):
        config = TransportConfig.from_mapping(raw)
        assert config is None
        assert build_client(config, forbidden) is None
        assert build_server(config, forbidden) is None
    with pytest.raises(ValueError):
        TransportConfig.from_mapping({"enabled": True, "endpoint": "http://invalid"})
