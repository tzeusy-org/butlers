"""The retained historical RFC has a unique number and a correct index."""

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.contract
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _read(relative_path: str) -> str:
    return (_REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_tracker_projection_rfc_identity_is_unique_and_indexed() -> None:
    """The tracker-exporter packet owns one unique, correctly indexed RFC."""
    rfc_number = "0025"
    rfc_filename = "0025-tracker-host-beads-projection-exporter.md"
    rfc_title = "Tracker-Host Beads Projection Exporter"
    rfc_directory = _REPO_ROOT / "about/legends-and-lore/rfcs"

    numbered_rfcs = sorted(rfc_directory.glob("[0-9][0-9][0-9][0-9]-*.md"))
    rfc_numbers = [path.name[:4] for path in numbered_rfcs]
    assert len(rfc_numbers) == len(set(rfc_numbers))
    assert (rfc_directory / "0023-durable-approval-delivery-intent-recovery.md").is_file()
    assert (rfc_directory / "0024-messenger-private-email-correspondence-ledger.md").is_file()

    tracker_exporter_rfcs = sorted(
        rfc_directory.glob("*-tracker-host-beads-projection-exporter.md")
    )
    expected_rfc = rfc_directory / rfc_filename
    assert tracker_exporter_rfcs == [expected_rfc]
    assert sorted(rfc_directory.glob(f"{rfc_number}-*.md")) == [expected_rfc]

    rfc = expected_rfc.read_text(encoding="utf-8")
    assert re.search(rf"^# RFC {rfc_number}: {re.escape(rfc_title)}$", rfc, re.MULTILINE)
    assert "RFC 0024" not in rfc

    index_rows = re.findall(
        r"^\| \[(\d{4})\]\(rfcs/([^)]*)\) \| ([^|]+) \|",
        _read("about/legends-and-lore/README.md"),
        re.MULTILINE,
    )
    assert [row for row in index_rows if row[0] == rfc_number] == [
        (rfc_number, rfc_filename, rfc_title)
    ]
