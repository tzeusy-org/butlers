import pytest

pytestmark = pytest.mark.unit


def test_alls_green_canary_fails_on_purpose() -> None:
    raise AssertionError("deliberate failure: alls-green must go red (bu-29wkwj.3 AC1)")
