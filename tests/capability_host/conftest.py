from pathlib import Path

import pytest

from tests.capability_host.helpers import Env


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return Env(tmp_path)
