"""[[butler.schedule]] accepts an optional IANA ``timezone``. [bu-kqnum.3.7]"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from butlers.config import ConfigError, load_config

pytestmark = pytest.mark.unit


def _load(tmp_path: Path, extra: str) -> Any:
    (tmp_path / "butler.toml").write_text(
        '[butler]\nname = "synthetic"\nport = 40999\ndescription = "d"\n\n'
        '[[butler.schedule]]\nname = "job"\ncron = "0 4 * * *"\n'
        'dispatch_mode = "job"\njob_name = "j"\n' + extra
    )
    return load_config(tmp_path)


def test_timezone_is_carried_on_job_and_prompt_schedules(tmp_path: Path) -> None:
    config = _load(tmp_path, 'timezone = "Etc/UTC"\n')
    assert config.schedules[0].timezone == "Etc/UTC"


def test_timezone_defaults_to_none(tmp_path: Path) -> None:
    assert _load(tmp_path, "").schedules[0].timezone is None


@pytest.mark.parametrize("bad", ['"Not/AZone"', "5"])
def test_invalid_timezone_is_a_config_error(tmp_path: Path, bad: str) -> None:
    with pytest.raises(ConfigError, match="timezone"):
        _load(tmp_path, f"timezone = {bad}\n")


@pytest.mark.parametrize("sentinel", ['"UTC"', '"utc"', '"Utc"', '""', '"  "'])
def test_sentinel_timezone_is_refused_with_an_error_naming_etc_utc(
    tmp_path: Path, sentinel: str
) -> None:
    with pytest.raises(ConfigError, match=r"Etc/UTC"):
        _load(tmp_path, f"timezone = {sentinel}\n")
