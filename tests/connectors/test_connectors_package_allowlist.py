"""The connector package contains transport entries and their owned kit only."""

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
# E: entry/package facade; H:<entry>: private family helper; K: shared kit.
# A new file needs an explicit placement decision in the PR that lands second.
CONNECTOR_FILES = {
    "README.md": "K",
    "__init__.py": "K",
    "activitywatch.py": "E",
    "blob_store.py": "K",
    "bridge_manager.py": "H:whatsapp_user_client",
    "cursor_store.py": "K",
    "db_role.py": "K",
    "discretion.py": "K",
    "email_spans.py": "K",
    "filtered_event_buffer.py": "K",
    "gmail.py": "E",
    "gmail_policy.py": "H:gmail",
    "google_calendar.py": "E",
    "google_drive.py": "E",
    "google_health.py": "E",
    "google_health_client.py": "H:google_health",
    "health_socket.py": "K",
    "heartbeat.py": "K",
    "home_assistant.py": "E",
    "home_assistant_checkpoint.py": "H:home_assistant",
    "home_assistant_envelope.py": "H:home_assistant",
    "home_assistant_filter.py": "H:home_assistant",
    "home_assistant_pipeline.py": "H:home_assistant",
    "home_assistant_rest.py": "H:home_assistant",
    "home_assistant_scrub.py": "H:home_assistant",
    "home_assistant_wellness.py": "H:home_assistant",
    "known_contact_snapshot.py": "K",
    "live_listener/__init__.py": "E",
    "live_listener/__main__.py": "E",
    "live_listener/audio.py": "H:live_listener",
    "live_listener/checkpoint.py": "H:live_listener",
    "live_listener/config.py": "H:live_listener",
    "live_listener/connector.py": "E",
    "live_listener/envelope.py": "H:live_listener",
    "live_listener/filter_gate.py": "H:live_listener",
    "live_listener/metrics.py": "H:live_listener",
    "live_listener/prefilter.py": "H:live_listener",
    "live_listener/session.py": "H:live_listener",
    "live_listener/transcription.py": "K",
    "live_listener/vad.py": "H:live_listener",
    "mcp_client.py": "K",
    "metrics.py": "K",
    "owner_outbound_events.py": "K",
    "owntracks.py": "E",
    "spotify.py": "E",
    "steam.py": "E",
    "telegram_bot.py": "E",
    "telegram_spans.py": "K",
    "telegram_user_client.py": "E",
    "telegram_user_client_consent.py": "H:telegram_user_client",
    "telegram_voice.py": "H:telegram_bot",
    "whatsapp_user_client.py": "E",
}


def test_connectors_package_has_only_entries_private_helpers_and_kit() -> None:
    tracked = subprocess.run(
        ["git", "ls-files", "--", "src/butlers/connectors"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert tracked, "The connector package scan must not silently inspect an empty tree"
    actual = {path.removeprefix("src/butlers/connectors/") for path in tracked}
    assert actual == CONNECTOR_FILES.keys(), {
        "unclassified": sorted(actual - CONNECTOR_FILES.keys()),
        "stale": sorted(CONNECTOR_FILES.keys() - actual),
    }
