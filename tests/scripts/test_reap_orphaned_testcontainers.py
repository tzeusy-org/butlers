"""Tests for scripts/reap_orphaned_testcontainers.py.

Regression guard for bu-3zu5l: 12 pgvector testcontainers were found running
with no owning pytest session, the oldest three weeks old. The trap the reaper
has to avoid is keying on age, which cannot tell "leaked by a dead run" from
"kept on purpose for an investigation". These tests pin the real signal (the
owning session's Ryuk sidecar is gone) and every protection that overrides it,
using synthetic ``docker inspect`` payloads: no Docker, no containers, no
network.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
import reap_orphaned_testcontainers as reaper  # noqa: E402

pytestmark = pytest.mark.unit

NOW = datetime(2026, 8, 23, 12, 0, 0, tzinfo=UTC)
SESSION_ID = "98fffc88-5c69-49a4-a5e4-83a2c3c62f13"

TESTCONTAINERS_LABELS = {
    "org.testcontainers": "true",
    "org.testcontainers.lang": "python",
    "org.testcontainers.session-id": SESSION_ID,
    "org.testcontainers.version": "4.14.2",
}


def make_container(
    *,
    name: str = "angry_dubinsky",
    labels: dict[str, str] | None = None,
    age_hours: float = 72.0,
    image: str = "pgvector/pgvector:pg17",
) -> reaper.Container:
    return reaper.Container(
        id="d14bb21f2cc4" + "0" * 52,
        name=name,
        image=image,
        created=NOW - timedelta(hours=age_hours),
        labels=dict(TESTCONTAINERS_LABELS if labels is None else labels),
    )


def classify(container: reaper.Container, *, ryuk: set[str] | None = None) -> reaper.Verdict:
    return reaper.classify(
        container,
        live_ryuk_session_ids=ryuk or set(),
        now=NOW,
        min_age_hours=reaper.DEFAULT_MIN_AGE_HOURS,
    )


def test_leaked_testcontainer_with_no_live_ryuk_is_reapable() -> None:
    verdict = classify(make_container())

    assert verdict.reapable is True
    assert SESSION_ID in verdict.reasons[0]


def test_live_session_is_spared_because_its_ryuk_is_running() -> None:
    verdict = classify(make_container(), ryuk={SESSION_ID})

    assert verdict.reapable is False
    assert any("owning pytest session is alive" in reason for reason in verdict.reasons)


def test_hand_started_container_without_testcontainers_labels_is_spared() -> None:
    # The real container that motivated this bead: started by hand for a PR
    # investigation, so it carries no labels at all.
    verdict = classify(make_container(name="codex-pr3708-acl-repro-11668", labels={}))

    assert verdict.reapable is False
    assert any("not created by testcontainers" in reason for reason in verdict.reasons)
    assert any("chosen by a human" in reason for reason in verdict.reasons)


def test_human_named_container_is_spared_even_with_testcontainers_labels() -> None:
    verdict = classify(make_container(name="pr3708-repro"))

    assert verdict.reapable is False
    assert any("chosen by a human" in reason for reason in verdict.reasons)


def test_compose_managed_container_is_spared() -> None:
    verdict = classify(
        make_container(
            name="lucid_mcnulty",
            labels={**TESTCONTAINERS_LABELS, "com.docker.compose.project": "butlers-dev"},
        )
    )

    assert verdict.reapable is False
    assert any("docker compose project" in reason for reason in verdict.reasons)


def test_keep_label_pins_a_container_regardless_of_value() -> None:
    verdict = classify(
        make_container(labels={**TESTCONTAINERS_LABELS, reaper.LABEL_KEEP: "investigating bu-x"})
    )

    assert verdict.reapable is False
    assert any(reaper.LABEL_KEEP in reason for reason in verdict.reasons)


def test_recent_container_is_spared_by_the_age_backstop() -> None:
    # A full backend gate runs ~40 minutes, so anything inside the backstop may
    # still belong to a run whose Ryuk was explicitly disabled.
    verdict = classify(make_container(age_hours=0.5))

    assert verdict.reapable is False
    assert any("backstop" in reason for reason in verdict.reasons)


def test_age_alone_never_makes_a_container_reapable() -> None:
    # The trap this bead names: three weeks old, but hand-named, so still safe.
    verdict = classify(make_container(name="codex-pr3708-acl-repro-11668", age_hours=24 * 21))

    assert verdict.reapable is False


def test_unparseable_creation_time_is_spared() -> None:
    container = reaper.Container(
        id="abc123",
        name="angry_dubinsky",
        image="pgvector/pgvector:pg17",
        created=None,
        labels=dict(TESTCONTAINERS_LABELS),
    )

    verdict = classify(container)

    assert verdict.reapable is False
    assert any("creation time" in reason for reason in verdict.reasons)


def test_missing_session_id_label_is_spared() -> None:
    labels = {k: v for k, v in TESTCONTAINERS_LABELS.items() if k != reaper.LABEL_SESSION_ID}

    verdict = classify(make_container(labels=labels))

    assert verdict.reapable is False


@pytest.mark.parametrize("name", ["angry_dubinsky", "codex_van_kleeck"])
def test_docker_generated_names_are_recognised(name: str) -> None:
    assert reaper.GENERATED_NAME_RE.match(name)


@pytest.mark.parametrize(
    "name",
    ["codex-pr3708-acl-repro-11668", "butlers-dev-migrations-1", "repro2", "acl_repro_pr_3708"],
)
def test_human_authored_names_are_not_mistaken_for_generated_ones(name: str) -> None:
    assert reaper.GENERATED_NAME_RE.match(name) is None


def test_live_ryuk_session_ids_extracts_ids_from_running_names() -> None:
    names = [
        f"testcontainers-ryuk-{SESSION_ID}",
        "angry_dubinsky",
        "butlers-dev-dashboard-api-hotreload-1",
        "testcontainers-ryuk-",
    ]

    assert reaper.live_ryuk_session_ids(names) == {SESSION_ID}


@pytest.mark.parametrize(
    "raw",
    ["2026-08-01T05:54:40.830172688Z", "2026-08-01T05:54:40.830172Z", "2026-08-01T05:54:40Z"],
)
def test_parse_created_handles_docker_nanosecond_timestamps(raw: str) -> None:
    parsed = reaper.parse_created(raw)

    assert parsed is not None
    assert parsed.tzinfo is not None
    assert parsed.replace(microsecond=0) == datetime(2026, 8, 1, 5, 54, 40, tzinfo=UTC)


@pytest.mark.parametrize("raw", ["", "not-a-timestamp"])
def test_parse_created_returns_none_for_unusable_input(raw: str) -> None:
    assert reaper.parse_created(raw) is None


def test_build_report_classifies_every_container() -> None:
    orphan = make_container()
    live = make_container(
        name="brave_gates",
        labels={**TESTCONTAINERS_LABELS, reaper.LABEL_SESSION_ID: "live-session"},
    )

    report = reaper.build_report(
        [orphan, live],
        ryuk_session_ids={"live-session"},
        now=NOW,
        min_age_hours=reaper.DEFAULT_MIN_AGE_HOURS,
    )

    assert [verdict.reapable for _, verdict in report] == [True, False]
    assert [container.name for container, _ in report] == ["angry_dubinsky", "brave_gates"]


def test_main_reports_without_removing_and_exits_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    removed: list[str] = []
    monkeypatch.setattr(reaper, "running_container_names", lambda: ["angry_dubinsky"])
    monkeypatch.setattr(reaper, "inspect_containers", lambda names: [make_container()])
    monkeypatch.setattr(reaper, "remove_container", lambda cid: removed.append(cid))

    assert reaper.main([]) == 1
    assert removed == []
    assert "angry_dubinsky" in capsys.readouterr().out


def test_main_removes_candidates_only_under_reap(monkeypatch: pytest.MonkeyPatch) -> None:
    removed: list[str] = []
    orphan = make_container()
    monkeypatch.setattr(reaper, "running_container_names", lambda: ["angry_dubinsky"])
    monkeypatch.setattr(reaper, "inspect_containers", lambda names: [orphan])
    monkeypatch.setattr(reaper, "remove_container", lambda cid: removed.append(cid))

    assert reaper.main(["--reap"]) == 0
    assert removed == [orphan.id]


def test_main_exits_two_when_docker_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom() -> list[str]:
        raise FileNotFoundError("docker")

    monkeypatch.setattr(reaper, "running_container_names", _boom)

    assert reaper.main([]) == 2


def test_main_spares_a_live_session_container(monkeypatch: pytest.MonkeyPatch) -> None:
    removed: list[str] = []
    monkeypatch.setattr(
        reaper,
        "running_container_names",
        lambda: ["angry_dubinsky", f"testcontainers-ryuk-{SESSION_ID}"],
    )
    monkeypatch.setattr(reaper, "inspect_containers", lambda names: [make_container()])
    monkeypatch.setattr(reaper, "remove_container", lambda cid: removed.append(cid))

    assert reaper.main(["--reap"]) == 0
    assert removed == []
