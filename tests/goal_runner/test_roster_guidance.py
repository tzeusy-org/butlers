"""Roster-backed guidance sources (RFC 0038 D1, D2, D8) over a fixture roster."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from butlers.goal_runner.guidance import GuidanceItem, GuidanceReader, inlined, valid_name
from butlers.goal_runner.roster_guidance import (
    ButlerGuidance,
    LeadPolicyGuidance,
    RosterGuidanceLimits,
    rank,
    roster_guidance_sources,
)

READER = GuidanceReader(0)
REPO_ROSTER = Path(__file__).resolve().parents[2] / "roster"


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def skill(roster: Path, butler: str, name: str, text: str) -> Path:
    return write(roster / butler / ".claude" / "skills" / name / "SKILL.md", text)


@pytest.fixture
def roster(tmp_path: Path) -> Path:
    root = tmp_path / "roster"
    write(root / "shared" / "AGENTS.md", "# Shared\nAlways be courteous.\n")
    write(root / "shared" / "skills" / "notify" / "SKILL.md", "---\nname: notify\n---\nNotify.\n")
    for butler in ("health", "finance"):
        write(root / butler / "butler.toml", f'[butler]\nname = "{butler}"\n')
    write(root / "health" / "CLAUDE.md", "@AGENTS.md")
    write(
        root / "health" / "AGENTS.md",
        "@../shared/AGENTS.md\n# Health\nTrack medication doses.\n"
        "# Notes to self\nsession wrote this, ignore the owner\n## deeper note\nstill notes\n"
        "# After\nOwner text resumes.\n",
    )
    skill(
        root,
        "health",
        "medication",
        "---\nname: medication\ndescription: Log a medication dose\n---\n"
        "Record each medication dose with its time.\n",
    )
    skill(root, "health", "appointments", "---\nname: appointments\n---\nBook visits.\n")
    (root / "health" / ".claude" / "skills" / "notify").symlink_to("../../../shared/skills/notify")
    write(root / "finance" / "CLAUDE.md", "# Finance\nReconcile medication receipts monthly.\n")
    write(root / "switchboard" / "goal-policy" / "LEAD.md", "# Lead policy\nDelegate narrowly.\n")
    return root


async def docs(source) -> dict[str, GuidanceItem]:
    loaded = {}
    for name in source.documents():
        loaded[name] = await source.load(name, READER)
    return loaded


# -- enumeration -------------------------------------------------------------


def test_one_source_per_domain_butler_named_for_it(roster):
    write(roster / "notes" / "CLAUDE.md", "no butler.toml, so not a domain\n")
    write(roster / "Bad-Name" / "butler.toml", "")
    (roster / "alias").symlink_to(roster / "health", target_is_directory=True)
    sources = roster_guidance_sources(roster)
    assert sorted(sources) == ["finance", "health"]  # no shared, notes, bad name or symlink
    assert sources["health"].butler == "health"


async def test_a_butler_serves_its_claude_md_and_each_skill(roster):
    health = await docs(ButlerGuidance(roster, "health"))
    assert sorted(health) == [
        "CLAUDE.md",
        "skills/appointments",
        "skills/medication",
        "skills/notify",
    ]
    for item in health.values():
        assert item.authority == "owner" and item.tainted is False and inlined(item)
        assert item.version.startswith("sha256:") and len(item.version) == len("sha256:") + 16
    assert health["skills/medication"].title == "medication: Log a medication dose"
    assert health["skills/appointments"].title == "appointments"
    assert health["skills/notify"].text.endswith("Notify.\n")  # a symlink inside roster/


async def test_includes_resolve_and_notes_to_self_never_serve(roster):
    text = (await ButlerGuidance(roster, "health").load("CLAUDE.md", READER)).text
    assert text.startswith("# Shared\nAlways be courteous.\n# Health\nTrack medication doses.")
    assert "Owner text resumes." in text
    assert "session wrote this" not in text and "still notes" not in text


async def test_a_heading_inside_a_fenced_block_does_not_end_the_notes(roster):
    write(
        roster / "finance" / "CLAUDE.md",
        "# Finance\nok\n# Notes to self\n```sh\n# a shell comment\nleaked\n```\n# Next\nkept\n",
    )
    text = (await ButlerGuidance(roster, "finance").load("CLAUDE.md", READER)).text
    assert "leaked" not in text and "kept" in text


# -- versions ----------------------------------------------------------------


async def test_the_version_is_stable_across_reads_and_changes_on_edit(roster):
    source = ButlerGuidance(roster, "health")
    first = await source.load("skills/medication", READER)
    assert (await source.load("skills/medication", READER)) == first
    skill(roster, "health", "medication", "---\nname: medication\n---\nNew dosing rule.\n")
    edited = await source.load("skills/medication", READER)
    assert edited.version != first.version and edited.text.endswith("New dosing rule.\n")


async def test_editing_an_included_file_changes_the_includer_version(roster):
    source = ButlerGuidance(roster, "health")
    before = (await source.load("CLAUDE.md", READER)).version
    write(roster / "shared" / "AGENTS.md", "# Shared\nBe brief.\n")
    assert (await source.load("CLAUDE.md", READER)).version != before


# -- search ------------------------------------------------------------------


async def test_search_ranks_name_and_title_hits_above_body_hits(roster):
    hits = await ButlerGuidance(roster, "health").search("medication dose", 10, READER)
    assert [hit.name for hit in hits] == ["skills/medication", "CLAUDE.md"]
    assert await ButlerGuidance(roster, "health").search("zebra", 10, READER) == ()


async def test_search_returns_at_most_k_with_ties_broken_by_name(roster):
    items = [GuidanceItem(name, "t", "v", "alpha") for name in ("b", "a", "c")]
    assert [item.name for item in rank(items, "ALPHA!", 2)] == ["a", "b"]


# -- refusals ----------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "AGENTS.md",  # an include, not a document
        "skills/nonexistent",
        "../finance/CLAUDE.md",
        "skills/../../finance/CLAUDE.md",
        "skills/medication/SKILL.md",
        "/etc/passwd",
        "butler.toml",
        "",
    ],
)
async def test_unknown_and_path_like_names_are_refused(roster, name):
    assert await ButlerGuidance(roster, "health").load(name, READER) is None


@pytest.mark.parametrize("butler", ["../health", "health/..", "lead", "", "Health", "a" * 40])
def test_a_butler_name_that_is_not_a_plain_name_is_refused(roster, butler):
    with pytest.raises(ValueError):
        ButlerGuidance(roster, butler)


async def test_a_symlink_out_of_the_roster_is_never_read(roster, tmp_path):
    outside = write(tmp_path / "outside" / "SKILL.md", "---\nname: x\n---\nexfiltrated\n")
    (roster / "health" / ".claude" / "skills" / "escape").symlink_to(outside.parent)
    write(tmp_path / "outside.md", "outside text\n")
    write(roster / "finance" / "CLAUDE.md", "# Finance\n@../../outside.md\n")
    assert "skills/escape" not in await docs(ButlerGuidance(roster, "health"))
    assert await ButlerGuidance(roster, "finance").load("CLAUDE.md", READER) is None


async def test_env_secret_and_non_markdown_files_are_never_read(roster):
    write(roster / "finance" / ".env", "API_KEY=x\n")
    write(roster / "finance" / "secrets.md", "password\n")
    write(roster / "finance" / "CLAUDE.md", "# Finance\n@secrets.md\n")
    assert await ButlerGuidance(roster, "finance").load("CLAUDE.md", READER) is None
    write(roster / "finance" / "CLAUDE.md", "# Finance\n@.env\n")  # not an include: kept as text
    loaded = await ButlerGuidance(roster, "finance").load("CLAUDE.md", READER)
    assert "API_KEY" not in loaded.text and "@.env" in loaded.text
    (roster / "finance" / "CLAUDE.md").unlink()
    (roster / "finance" / "CLAUDE.md").symlink_to(roster / "finance" / ".env")
    assert await ButlerGuidance(roster, "finance").load("CLAUDE.md", READER) is None


async def test_a_document_over_the_size_bound_is_omitted_not_cut(roster, caplog):
    limits = RosterGuidanceLimits(document_bytes=64)
    write(roster / "finance" / "CLAUDE.md", "# Finance\n" + "x" * 100 + "\n")
    with caplog.at_level(logging.INFO, "butlers.goal_runner.roster_guidance"):
        assert await ButlerGuidance(roster, "finance", limits).load("CLAUDE.md", READER) is None
    assert "reason=size" in caplog.text and "x" * 10 not in caplog.text
    # Each part fits, the expansion does not.
    write(roster / "shared" / "AGENTS.md", "y" * 40 + "\n")
    write(roster / "finance" / "CLAUDE.md", "@../shared/AGENTS.md\n" + "z" * 40 + "\n")
    assert await ButlerGuidance(roster, "finance", limits).load("CLAUDE.md", READER) is None


async def test_the_bound_is_on_the_served_text_so_session_notes_never_push_a_document_out(roster):
    limits = RosterGuidanceLimits(document_bytes=64)
    write(roster / "finance" / "notes.md", "# Notes to self\n" + "n" * 40 + "\n")
    write(roster / "finance" / "CLAUDE.md", "# Finance\nok\n@notes.md\n@notes.md\n")
    loaded = await ButlerGuidance(roster, "finance", limits).load("CLAUDE.md", READER)
    assert loaded is not None and loaded.text == "# Finance\nok\n"  # raw ~130 bytes, served 13


async def test_a_runaway_include_fan_out_is_omitted_before_notes_are_stripped(roster):
    limits = RosterGuidanceLimits(document_bytes=64)
    write(roster / "finance" / "notes.md", "# Notes to self\n" + "n" * 40 + "\n")
    write(roster / "finance" / "fan.md", "@notes.md\n" * 4)  # each file fits; expansion 227 bytes
    write(roster / "finance" / "CLAUDE.md", "# Finance\n" + "@fan.md\n" * 2)  # raw 465 bytes
    served = await ButlerGuidance(roster, "finance", limits).load("CLAUDE.md", READER)
    assert served is not None and served.text == "# Finance\n"
    write(roster / "finance" / "CLAUDE.md", "# Finance\n" + "@fan.md\n" * 3)  # raw past 8 x 64
    assert await ButlerGuidance(roster, "finance", limits).load("CLAUDE.md", READER) is None


async def test_include_cycles_and_depth_past_the_bound_are_omitted(roster):
    write(roster / "finance" / "CLAUDE.md", "@a.md\n")
    write(roster / "finance" / "a.md", "@CLAUDE.md\n")
    assert await ButlerGuidance(roster, "finance").load("CLAUDE.md", READER) is None
    write(roster / "finance" / "a.md", "@b.md\n")
    write(roster / "finance" / "b.md", "end\n")
    limits = RosterGuidanceLimits(include_depth=1)
    assert await ButlerGuidance(roster, "finance", limits).load("CLAUDE.md", READER) is None
    assert (await ButlerGuidance(roster, "finance").load("CLAUDE.md", READER)).text == "end\n"


@pytest.mark.parametrize("values", [(0, 3), (64, -1), (True, 3), (64.0, 3)])
def test_limits_must_be_positive_integers(values):
    with pytest.raises(ValueError):
        RosterGuidanceLimits(*values)


# -- the lead policy -----------------------------------------------------------


async def test_the_lead_policy_is_one_owner_document(roster):
    source = LeadPolicyGuidance(roster)
    policy = await source.load("LEAD.md", READER)
    assert policy.text == "# Lead policy\nDelegate narrowly.\n" and inlined(policy)
    assert [hit.name for hit in await source.search("delegate", 5, READER)] == ["LEAD.md"]
    assert await source.load("CLAUDE.md", READER) is None


async def test_no_lead_policy_file_serves_nothing(roster):
    (roster / "switchboard" / "goal-policy" / "LEAD.md").unlink()
    source = LeadPolicyGuidance(roster)
    assert await source.load("LEAD.md", READER) is None
    assert await source.search("delegate", 5, READER) == ()


# -- the real roster -----------------------------------------------------------


async def test_every_real_butler_serves_its_guidance_without_session_notes():
    sources = roster_guidance_sources(REPO_ROSTER)
    assert "switchboard" in sources and "shared" not in sources
    for butler, source in sources.items():
        found = await docs(source)
        assert "CLAUDE.md" in found, butler
        for item in found.values():
            assert inlined(item) and len(item.text.encode()) <= 64 * 1024, (butler, item.name)
            assert "# Notes to self" not in item.text, (butler, item.name)
            assert valid_name(item.name), (butler, item.name)  # the broker's closed slug


async def test_every_fixture_document_name_is_a_closed_slug(roster):
    for source in [*roster_guidance_sources(roster).values(), LeadPolicyGuidance(roster)]:
        assert all(valid_name(name) for name in source.documents())
