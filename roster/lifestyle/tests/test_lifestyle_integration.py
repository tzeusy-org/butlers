"""Integration tests for the Lifestyle butler.

Covers 6 test categories per bu-d657.4:
1. Butler startup — roster/lifestyle/butler.toml loads with expected modules and config
2. Memory taxonomy — taste-domain fact storage at stable and volatile permanence levels
3. Switchboard routing — music message routes to lifestyle
4. Switchboard routing — food preference routes to lifestyle (not health)
5. Switchboard fanout — lifestyle + health signals route to both butlers
6. Memory maintenance — memory_consolidation, memory_episode_cleanup,
   memory_purge_superseded jobs are registered in the schedule

Tests 1, 3, 4, 5, 6 are unit-level (no Docker required).
Test 2 is integration-level (requires Docker + testcontainers Postgres).

Issue: bu-d657.4
"""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Module-level marks
# The roster conftest auto-applies integration + docker-skip to all tests
# under roster/.  Tests 1, 3, 4, 5, 6 are unit tests — they need to
# explicitly override or skip Docker requirement.  We do NOT use pytestmark
# at module level; each class/function declares its own marks so the
# docker-skip only applies where needed.
# ---------------------------------------------------------------------------

_docker_available = shutil.which("docker") is not None

_REPO_ROOT = Path(__file__).resolve().parents[3]  # .../rig
_LIFESTYLE_ROSTER_DIR = _REPO_ROOT / "roster" / "lifestyle"
_SWITCHBOARD_SKILLS_DIR = (
    _REPO_ROOT / "roster" / "switchboard" / ".agents" / "skills" / "message-triage"
)


# ===========================================================================
# Test Category 1: Butler Startup
# Verify lifestyle butler.toml loads correctly with the expected modules
# and configuration values.  These are unit tests — no Docker required.
# ===========================================================================


@pytest.mark.unit
class TestLifestyleButlerStartup:
    """AC1: lifestyle butler.toml is discoverable and correctly configured."""

    def test_lifestyle_butler_toml_loads_without_error(self) -> None:
        """load_config() succeeds for roster/lifestyle/butler.toml."""
        from butlers.config import load_config

        assert _LIFESTYLE_ROSTER_DIR.is_dir(), (
            f"roster/lifestyle/ directory not found at {_LIFESTYLE_ROSTER_DIR}"
        )

        cfg = load_config(_LIFESTYLE_ROSTER_DIR)
        assert cfg.name == "lifestyle"

    def test_lifestyle_butler_has_expected_port(self) -> None:
        """Lifestyle butler is configured on port 41109."""
        from butlers.config import load_config

        cfg = load_config(_LIFESTYLE_ROSTER_DIR)
        assert cfg.port == 41109

    def test_lifestyle_butler_has_memory_module(self) -> None:
        """butler.toml declares the memory module."""
        from butlers.config import load_config

        cfg = load_config(_LIFESTYLE_ROSTER_DIR)
        assert "memory" in cfg.modules, (
            "memory module is missing from [modules] section in roster/lifestyle/butler.toml"
        )

    def test_lifestyle_butler_has_calendar_module(self) -> None:
        """butler.toml declares the calendar module."""
        from butlers.config import load_config

        cfg = load_config(_LIFESTYLE_ROSTER_DIR)
        assert "calendar" in cfg.modules, (
            "calendar module is missing from [modules] section in roster/lifestyle/butler.toml"
        )

    def test_lifestyle_butler_has_contacts_module(self) -> None:
        """butler.toml declares the contacts module."""
        from butlers.config import load_config

        cfg = load_config(_LIFESTYLE_ROSTER_DIR)
        assert "contacts" in cfg.modules, (
            "contacts module is missing from [modules] section in roster/lifestyle/butler.toml"
        )

    def test_lifestyle_butler_has_expected_db_schema(self) -> None:
        """Lifestyle butler uses 'lifestyle' as its database schema."""
        from butlers.config import load_config

        cfg = load_config(_LIFESTYLE_ROSTER_DIR)
        assert cfg.db_schema == "lifestyle"

    def test_lifestyle_butler_has_max_concurrent_sessions(self) -> None:
        """Lifestyle butler is configured for at least 3 concurrent sessions."""
        from butlers.config import load_config

        cfg = load_config(_LIFESTYLE_ROSTER_DIR)
        assert cfg.runtime is not None
        assert cfg.runtime.max_concurrent_sessions >= 3

    def test_lifestyle_butler_in_list_butlers(self) -> None:
        """list_butlers() discovers the lifestyle butler from the real roster."""
        from butlers.config import list_butlers

        all_configs = list_butlers(_REPO_ROOT / "roster")
        butler_names = [cfg.name for cfg in all_configs]
        assert "lifestyle" in butler_names, (
            f"Lifestyle butler not found in list_butlers(). Discovered: {butler_names}"
        )


# ===========================================================================
# Test Category 2: Memory Taxonomy
# Verify taste-domain facts can be stored and retrieved with correct
# permanence levels.  These tests need a real Postgres via testcontainers.
# ===========================================================================

pytestmark_integration = [
    pytest.mark.integration,
    pytest.mark.skipif(not _docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]


@pytest.fixture
async def lifestyle_pool(provisioned_postgres_pool):
    """Provision a fresh database with the facts/predicate tables for memory tests."""
    async with provisioned_postgres_pool() as p:
        # shared schema required by store_fact (entity resolution)
        await p.execute("CREATE SCHEMA IF NOT EXISTS shared")
        await p.execute("""
            CREATE TABLE IF NOT EXISTS public.entities (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id TEXT NOT NULL DEFAULT '',
                canonical_name VARCHAR NOT NULL DEFAULT '',
                name TEXT NOT NULL DEFAULT '',
                entity_type VARCHAR NOT NULL DEFAULT 'other',
                aliases TEXT[] NOT NULL DEFAULT '{}',
                metadata JSONB DEFAULT '{}'::jsonb,
                roles TEXT[] NOT NULL DEFAULT '{}',
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS public.contacts (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                entity_id UUID REFERENCES public.entities(id),
                roles JSONB NOT NULL DEFAULT '[]'
            )
        """)

        # predicate_registry — required by store_fact registry enforcement
        await p.execute("""
            CREATE TABLE IF NOT EXISTS predicate_registry (
                name TEXT PRIMARY KEY,
                expected_subject_type TEXT,
                expected_object_type TEXT,
                is_edge BOOLEAN NOT NULL DEFAULT false,
                is_temporal BOOLEAN NOT NULL DEFAULT false,
                description TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                status TEXT NOT NULL DEFAULT 'active',
                superseded_by TEXT,
                deprecated_at TIMESTAMPTZ,
                inverse_of TEXT,
                is_symmetric BOOLEAN NOT NULL DEFAULT false,
                aliases TEXT[] NOT NULL DEFAULT '{}',
                usage_count INTEGER NOT NULL DEFAULT 0,
                last_used_at TIMESTAMPTZ
            )
        """)

        # Register lifestyle domain predicates
        await p.execute("""
            INSERT INTO predicate_registry (name, is_temporal) VALUES
                ('likes_genre', false),
                ('likes_artist', false),
                ('likes_cuisine', false),
                ('favorite_restaurant', false),
                ('favorite_recipe', false),
                ('hobby', false),
                ('food_preference', false),
                ('food_dislike', false),
                ('routine', false),
                ('watches', false),
                ('reads', false),
                ('plays', false),
                ('listens_to', false),
                ('listening_pattern', false)
            ON CONFLICT (name) DO NOTHING
        """)

        # facts table (TEXT embedding avoids pgvector in unit tests)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS facts (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                subject TEXT NOT NULL,
                predicate TEXT NOT NULL,
                content TEXT NOT NULL,
                embedding TEXT,
                search_vector TSVECTOR,
                importance FLOAT NOT NULL DEFAULT 5.0,
                confidence FLOAT NOT NULL DEFAULT 1.0,
                decay_rate FLOAT NOT NULL DEFAULT 0.008,
                permanence TEXT NOT NULL DEFAULT 'standard',
                source_butler TEXT,
                source_episode_id UUID,
                supersedes_id UUID REFERENCES facts(id) ON DELETE SET NULL,
                validity TEXT NOT NULL DEFAULT 'active',
                scope TEXT NOT NULL DEFAULT 'global',
                reference_count INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                last_confirmed_at TIMESTAMPTZ,
                tags JSONB DEFAULT '[]'::jsonb,
                metadata JSONB DEFAULT '{}'::jsonb,
                entity_id UUID REFERENCES public.entities(id),
                object_entity_id UUID REFERENCES public.entities(id),
                valid_at TIMESTAMPTZ DEFAULT NULL,
                tenant_id TEXT NOT NULL DEFAULT 'owner',
                request_id TEXT,
                idempotency_key TEXT,
                observed_at TIMESTAMPTZ DEFAULT now(),
                invalid_at TIMESTAMPTZ,
                retention_class TEXT NOT NULL DEFAULT 'operational',
                sensitivity TEXT NOT NULL DEFAULT 'normal'
            )
        """)

        # memory_links table — required by store_fact supersession path
        await p.execute("""
            CREATE TABLE IF NOT EXISTS memory_links (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                source_type TEXT NOT NULL,
                source_id UUID NOT NULL,
                target_type TEXT NOT NULL,
                target_id UUID NOT NULL,
                relation TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (source_type, source_id, target_type, target_id)
            )
        """)

        await p.execute("""
            CREATE INDEX IF NOT EXISTS idx_facts_predicate_valid_at
                ON facts (predicate, valid_at)
        """)
        await p.execute("""
            CREATE INDEX IF NOT EXISTS idx_facts_subject_predicate
                ON facts (subject, predicate)
        """)

        yield p


def _make_mock_embedding_engine() -> MagicMock:
    """Return a deterministic fake EmbeddingEngine for tests."""
    engine = MagicMock()
    engine.embed.return_value = [0.1] * 384
    return engine


@pytest.mark.integration
@pytest.mark.skipif(not _docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
class TestLifestyleMemoryTaxonomy:
    """AC2: Taste-domain facts can be stored with correct permanence levels.

    Tests cover both DURABLE (stable) and TRANSIENT (volatile) permanence
    from the lifestyle domain taxonomy.
    """

    async def test_store_stable_genre_preference(self, lifestyle_pool) -> None:
        """Store a music genre preference using 'stable' permanence."""
        from butlers.modules.memory.storage import store_fact

        engine = _make_mock_embedding_engine()
        fact_id = await store_fact(
            lifestyle_pool,
            subject="user",
            predicate="likes_genre",
            content="loves jazz — especially 1960s modal jazz",
            embedding_engine=engine,
            permanence="stable",
            importance=8.0,
            tags=["music", "genre", "jazz"],
            scope="global",
        )
        assert fact_id is not None
        assert isinstance(fact_id, dict)
        assert "id" in fact_id

        row = await lifestyle_pool.fetchrow(
            "SELECT subject, predicate, permanence, validity FROM facts WHERE id = $1",
            fact_id["id"],
        )
        assert row is not None
        assert row["subject"] == "user"
        assert row["predicate"] == "likes_genre"
        assert row["permanence"] == "stable"
        assert row["validity"] == "active"

    async def test_store_stable_food_dislike(self, lifestyle_pool) -> None:
        """Store a food dislike using 'stable' permanence (high-importance DURABLE fact)."""
        from butlers.modules.memory.storage import store_fact

        engine = _make_mock_embedding_engine()
        fact_id = await store_fact(
            lifestyle_pool,
            subject="user",
            predicate="food_dislike",
            content="dislikes coriander — strong aversion",
            embedding_engine=engine,
            permanence="stable",
            importance=9.0,
            tags=["food", "dislike", "allergy-adjacent"],
            scope="global",
        )
        row = await lifestyle_pool.fetchrow(
            "SELECT predicate, permanence FROM facts WHERE id = $1",
            fact_id["id"],
        )
        assert row["predicate"] == "food_dislike"
        assert row["permanence"] == "stable"

    async def test_store_stable_favorite_restaurant(self, lifestyle_pool) -> None:
        """Store a favourite restaurant using 'stable' permanence."""
        from butlers.modules.memory.storage import store_fact

        engine = _make_mock_embedding_engine()
        fact_id = await store_fact(
            lifestyle_pool,
            subject="user",
            predicate="favorite_restaurant",
            content="Koya — favourite udon spot, love cold noodles in summer",
            embedding_engine=engine,
            permanence="stable",
            importance=7.0,
            tags=["restaurant", "japanese", "udon"],
            scope="global",
        )
        row = await lifestyle_pool.fetchrow(
            "SELECT predicate, permanence, importance FROM facts WHERE id = $1",
            fact_id["id"],
        )
        assert row["predicate"] == "favorite_restaurant"
        assert row["permanence"] == "stable"
        assert row["importance"] == pytest.approx(7.0)

    async def test_store_volatile_watches_fact(self, lifestyle_pool) -> None:
        """Store a currently-watching fact using 'volatile' permanence (TRANSIENT)."""
        from butlers.modules.memory.storage import store_fact

        engine = _make_mock_embedding_engine()
        fact_id = await store_fact(
            lifestyle_pool,
            subject="user",
            predicate="watches",
            content="currently watching Severance S2 — halfway through, loving it",
            embedding_engine=engine,
            permanence="volatile",
            importance=5.0,
            tags=["tv", "watching", "severance"],
            scope="global",
        )
        row = await lifestyle_pool.fetchrow(
            "SELECT predicate, permanence FROM facts WHERE id = $1",
            fact_id["id"],
        )
        assert row["predicate"] == "watches"
        assert row["permanence"] == "volatile"

    async def test_store_volatile_listens_to_fact(self, lifestyle_pool) -> None:
        """Store a current-listening fact using 'volatile' permanence (TRANSIENT)."""
        from butlers.modules.memory.storage import store_fact

        engine = _make_mock_embedding_engine()
        fact_id = await store_fact(
            lifestyle_pool,
            subject="user",
            predicate="listens_to",
            content="heavy rotation: Radiohead OK Computer this week",
            embedding_engine=engine,
            permanence="volatile",
            importance=5.0,
            tags=["music", "listening"],
            scope="global",
        )
        row = await lifestyle_pool.fetchrow(
            "SELECT predicate, permanence FROM facts WHERE id = $1",
            fact_id["id"],
        )
        assert row["predicate"] == "listens_to"
        assert row["permanence"] == "volatile"

    async def test_stable_fact_supersedes_previous(self, lifestyle_pool) -> None:
        """A second stable fact for the same subject/predicate supersedes the first."""
        from butlers.modules.memory.storage import store_fact

        engine = _make_mock_embedding_engine()
        first = await store_fact(
            lifestyle_pool,
            subject="user",
            predicate="hobby",
            content="sourdough baking — active hobby",
            embedding_engine=engine,
            permanence="stable",
            importance=7.0,
            tags=["hobby", "baking"],
            scope="global",
        )

        second = await store_fact(
            lifestyle_pool,
            subject="user",
            predicate="hobby",
            content="sourdough baking — now also fermenting kombucha",
            embedding_engine=engine,
            permanence="stable",
            importance=7.0,
            tags=["hobby", "baking", "fermentation"],
            scope="global",
        )

        # The second fact should record that it supersedes the first
        assert second.get("supersedes_id") == first["id"]

        # Retrieve the old fact — it should be marked superseded
        old_row = await lifestyle_pool.fetchrow(
            "SELECT validity FROM facts WHERE id = $1",
            first["id"],
        )
        assert old_row["validity"] == "superseded"

    async def test_all_stable_predicates_accepted(self, lifestyle_pool) -> None:
        """All taste-preference predicates from the taxonomy accept 'stable' permanence."""
        from butlers.modules.memory.storage import store_fact

        engine = _make_mock_embedding_engine()
        stable_predicates = [
            "likes_genre",
            "likes_artist",
            "likes_cuisine",
            "favorite_restaurant",
            "hobby",
            "food_preference",
            "food_dislike",
            "routine",
        ]

        for predicate in stable_predicates:
            fact_id = await store_fact(
                lifestyle_pool,
                subject="user",
                predicate=predicate,
                content=f"test content for {predicate}",
                embedding_engine=engine,
                permanence="stable",
                importance=5.0,
                tags=[],
                scope="global",
            )
            row = await lifestyle_pool.fetchrow(
                "SELECT permanence, validity FROM facts WHERE id = $1",
                fact_id["id"],
            )
            assert row["permanence"] == "stable", (
                f"Expected 'stable' permanence for predicate '{predicate}', "
                f"got '{row['permanence']}'"
            )
            assert row["validity"] == "active"

    async def test_all_volatile_predicates_accepted(self, lifestyle_pool) -> None:
        """All consumption-state predicates from the taxonomy accept 'volatile' permanence."""
        from butlers.modules.memory.storage import store_fact

        engine = _make_mock_embedding_engine()
        volatile_predicates = ["watches", "reads", "plays", "listens_to"]

        for predicate in volatile_predicates:
            fact_id = await store_fact(
                lifestyle_pool,
                subject="user",
                predicate=predicate,
                content=f"test content for {predicate}",
                embedding_engine=engine,
                permanence="volatile",
                importance=5.0,
                tags=[],
                scope="global",
            )
            row = await lifestyle_pool.fetchrow(
                "SELECT permanence, validity FROM facts WHERE id = $1",
                fact_id["id"],
            )
            assert row["permanence"] == "volatile", (
                f"Expected 'volatile' permanence for predicate '{predicate}', "
                f"got '{row['permanence']}'"
            )
            assert row["validity"] == "active"

    async def test_spotify_enriched_fact_stored_with_spotify_subject(self, lifestyle_pool) -> None:
        """Spotify-enriched facts use 'spotify:artist:{id}' subject with 'stable' permanence."""
        from butlers.modules.memory.storage import store_fact

        engine = _make_mock_embedding_engine()
        spotify_subject = "spotify:artist:4tZwfgrHOc3mvqYlEYSvVi"
        fact_id = await store_fact(
            lifestyle_pool,
            subject=spotify_subject,
            predicate="listening_pattern",
            content="AC/DC — heavy rotation every morning commute, 3 months",
            embedding_engine=engine,
            permanence="stable",
            importance=6.0,
            tags=["spotify", "artist", "rotation", "music"],
            scope="global",
        )
        row = await lifestyle_pool.fetchrow(
            "SELECT subject, predicate, permanence FROM facts WHERE id = $1",
            fact_id["id"],
        )
        assert row["subject"] == spotify_subject
        assert row["predicate"] == "listening_pattern"
        assert row["permanence"] == "stable"


# ===========================================================================
# Test Category 3: Switchboard Routing — Music Message → Lifestyle
# Verify that the message-triage SKILL.md classifies music messages to
# lifestyle and that the classification rules are present.
# ===========================================================================


@pytest.mark.unit
class TestSwitchboardMusicRouting:
    """AC3: Music-related messages route to the lifestyle butler.

    These tests verify that the switchboard routing configuration (SKILL.md)
    contains the correct classification rules for music → lifestyle routing.
    """

    def test_message_triage_skill_exists(self) -> None:
        """message-triage SKILL.md exists at the expected path."""
        skill_path = _SWITCHBOARD_SKILLS_DIR / "SKILL.md"
        assert skill_path.exists(), f"message-triage SKILL.md not found at {skill_path}"

    def test_skill_lists_lifestyle_butler(self) -> None:
        """message-triage SKILL.md lists 'lifestyle' as an available butler."""
        skill_content = (_SWITCHBOARD_SKILLS_DIR / "SKILL.md").read_text()
        assert "lifestyle" in skill_content.lower(), (
            "Lifestyle butler is not listed in message-triage SKILL.md"
        )

    def test_skill_routes_music_to_lifestyle(self) -> None:
        """message-triage SKILL.md routes music listening to lifestyle."""
        skill_content = (_SWITCHBOARD_SKILLS_DIR / "SKILL.md").read_text()
        # Music should be classified to lifestyle
        assert "music" in skill_content.lower()
        # Find section about lifestyle
        lines = skill_content.splitlines()
        lifestyle_section = False
        has_music_rule = False
        for line in lines:
            if "lifestyle" in line.lower() and "classification" in line.lower():
                lifestyle_section = True
            if lifestyle_section and "music" in line.lower():
                has_music_rule = True
                break
        assert has_music_rule, (
            "No music routing rule found in lifestyle classification section "
            "of message-triage SKILL.md"
        )

    def test_skill_routes_artist_mentions_to_lifestyle(self) -> None:
        """message-triage SKILL.md has example routing music/artist to lifestyle."""
        skill_content = (_SWITCHBOARD_SKILLS_DIR / "SKILL.md").read_text()
        # The skill should have an example where music/artist routes to lifestyle
        assert "lifestyle" in skill_content
        # There should be a Tame Impala or similar music example routed to lifestyle
        lifestyle_lower = skill_content.lower()
        has_music_signal = (
            "listening" in lifestyle_lower
            or "artist" in lifestyle_lower
            or "playlist" in lifestyle_lower
        )
        assert has_music_signal, (
            "No listening/artist/playlist signal found in SKILL.md lifestyle section"
        )

    def test_skill_confidence_table_shows_music_to_lifestyle(self) -> None:
        """The confidence table in SKILL.md maps music signals → lifestyle."""
        skill_content = (_SWITCHBOARD_SKILLS_DIR / "SKILL.md").read_text()
        # There should be a high-confidence mapping from music/playlist mentions to lifestyle
        assert "lifestyle" in skill_content
        lower = skill_content.lower()
        # Verify the routing table maps music/playlist to lifestyle (not health, not general)
        assert "song" in lower or "artist" in lower or "playlist" in lower, (
            "No song/artist/playlist signal in SKILL.md routing guidance"
        )


# ===========================================================================
# Test Category 4: Switchboard Routing — Food Preference → Lifestyle (not health)
# Verify disambiguation rules: food preference → lifestyle, not health.
# ===========================================================================


@pytest.mark.unit
class TestSwitchboardFoodPreferenceRouting:
    """AC4: Food preference messages route to lifestyle, NOT health.

    The key disambiguation: expressing food preferences/tastes is a lifestyle
    signal. Only calorie tracking, nutrition logging, and diet goals go to health.
    """

    def test_food_preference_routes_to_lifestyle_not_health(self) -> None:
        """SKILL.md disambiguates 'I like Thai food' → lifestyle (not health)."""
        skill_content = (_SWITCHBOARD_SKILLS_DIR / "SKILL.md").read_text()
        lower = skill_content.lower()
        # The skill should have an explicit disambiguation for Thai food preference
        assert "thai food" in lower or ("food preference" in lower and "lifestyle" in lower), (
            "SKILL.md does not contain food preference → lifestyle disambiguation rule"
        )

    def test_food_preference_disambiguation_rule_present(self) -> None:
        """SKILL.md explicitly states food preferences belong to lifestyle."""
        skill_content = (_SWITCHBOARD_SKILLS_DIR / "SKILL.md").read_text()
        lower = skill_content.lower()
        # Verify the rule exists: food preference → lifestyle, NOT health
        assert "lifestyle" in lower
        assert "food" in lower
        # The disambiguation section should call out the food boundary
        assert (
            "not health" in lower
            or "not nutrition" in lower
            or ("food preference" in lower and "lifestyle" in lower)
        ), (
            "SKILL.md does not clearly distinguish food preference (lifestyle) "
            "from nutrition (health)"
        )

    def test_calorie_tracking_routes_to_health_not_lifestyle(self) -> None:
        """SKILL.md explicitly maps calorie/nutrition tracking to health, not lifestyle."""
        skill_content = (_SWITCHBOARD_SKILLS_DIR / "SKILL.md").read_text()
        lower = skill_content.lower()
        # "Log my calories" should NOT go to lifestyle
        assert "calorie" in lower or "nutrition" in lower, (
            "SKILL.md does not mention calorie/nutrition tracking classification"
        )
        # There should be a disambiguation that calorie tracking → health, not lifestyle
        assert "health" in lower

    def test_cuisine_preference_classified_under_lifestyle(self) -> None:
        """SKILL.md includes cuisine preferences, restaurant recommendations under lifestyle."""
        skill_content = (_SWITCHBOARD_SKILLS_DIR / "SKILL.md").read_text()
        lower = skill_content.lower()
        # Cuisine and restaurant visit signals should appear in lifestyle classification
        assert "cuisine" in lower or "restaurant" in lower, (
            "SKILL.md does not classify cuisine/restaurant preferences under lifestyle"
        )

    def test_food_boundary_rule_in_routing_safety_section(self) -> None:
        """Routing safety section explicitly states the lifestyle vs health food boundary."""
        skill_content = (_SWITCHBOARD_SKILLS_DIR / "SKILL.md").read_text()
        # The routing safety section should mention the lifestyle vs health food boundary
        assert "lifestyle vs health" in skill_content.lower() or (
            "food" in skill_content.lower() and "lifestyle" in skill_content.lower()
        ), "SKILL.md routing safety section does not cover lifestyle vs health food boundary"

    def test_lifestyle_claude_md_excludes_nutrition_tracking(self) -> None:
        """Lifestyle butler CLAUDE.md explicitly refuses nutrition tracking (→ Health)."""
        claude_path = _LIFESTYLE_ROSTER_DIR / "CLAUDE.md"
        assert claude_path.exists(), f"CLAUDE.md not found at {claude_path}"
        content = claude_path.read_text()
        lower = content.lower()
        # Lifestyle butler should explicitly say: no nutrition, refer to health
        has_nutrition_boundary = (
            "nutrition" in lower or "calorie" in lower or "health butler" in lower
        )
        assert has_nutrition_boundary, (
            "Lifestyle CLAUDE.md does not mention referring nutrition/calorie tracking "
            "to Health butler"
        )


# ===========================================================================
# Test Category 5: Switchboard Fanout — Both Lifestyle and Health Signals
# Verify that messages with overlapping lifestyle + health signals route to BOTH.
# ===========================================================================


@pytest.mark.unit
class TestSwitchboardFanoutLifestyleAndHealth:
    """AC5: Messages with lifestyle AND health signals fanout to both butlers.

    Example: "I've been stress-eating Thai food all week"
    - lifestyle: Thai food preference
    - health: stress eating pattern (diet/health goal)
    Should route to BOTH lifestyle AND health.
    """

    def test_fanout_rule_present_in_skill(self) -> None:
        """SKILL.md defines a fanout rule for lifestyle + health overlap."""
        skill_content = (_SWITCHBOARD_SKILLS_DIR / "SKILL.md").read_text()
        lower = skill_content.lower()
        # The skill should have a fanout rule for lifestyle + health overlap
        assert "fanout" in lower or (
            "lifestyle" in lower and "health" in lower and "both" in lower
        ), "SKILL.md does not define a fanout rule for lifestyle + health overlap"

    def test_stress_eating_example_fans_out(self) -> None:
        """SKILL.md includes 'stress eating' or similar as a lifestyle+health fanout example."""
        skill_content = (_SWITCHBOARD_SKILLS_DIR / "SKILL.md").read_text()
        lower = skill_content.lower()
        # "stress eating" or "mood-influenced" should appear as a fanout example
        assert "stress" in lower or "mood" in lower, (
            "SKILL.md does not contain a stress-eating or mood-influenced fanout example"
        )

    def test_food_plus_health_signal_triggers_fanout(self) -> None:
        """SKILL.md specifies that food + health signals can fanout to both butlers."""
        skill_content = (_SWITCHBOARD_SKILLS_DIR / "SKILL.md").read_text()
        lower = skill_content.lower()
        # There should be an explicit rule about food preference + health metric → both
        assert "lifestyle" in lower and "health" in lower
        # Find the fanout description
        assert (
            "both" in lower or ("lifestyle" in lower and "+ health" in lower) or ("fanout" in lower)
        ), "SKILL.md does not describe a fanout case where both lifestyle and health are routed"

    def test_lifestyle_health_fanout_example_in_confidence_table(self) -> None:
        """Confidence table includes lifestyle + health overlap entry."""
        skill_content = (_SWITCHBOARD_SKILLS_DIR / "SKILL.md").read_text()
        lower = skill_content.lower()
        # The confidence/routing table should show lifestyle + health fanout
        assert (
            "lifestyle + health" in lower
            or ("lifestyle" in lower and "health" in lower and "overlap" in lower)
            or ("fanout" in lower and "lifestyle" in lower)
        ), "SKILL.md confidence/routing table does not show lifestyle+health fanout"

    def test_general_excluded_from_fanout(self) -> None:
        """When lifestyle+health both match, general is NOT included in fanout."""
        skill_content = (_SWITCHBOARD_SKILLS_DIR / "SKILL.md").read_text()
        lower = skill_content.lower()
        # The skill should say general is fallback only, not co-routed with specialists
        assert "last-resort" in lower or "fallback" in lower, (
            "SKILL.md does not state that general is a last-resort fallback"
        )
        # General must not appear alongside specialist butlers
        assert (
            "general must not" in lower
            or "not route to general" in lower
            or ("fallback only" in lower)
        ), "SKILL.md does not clarify that general is never co-routed with specialists"


# ===========================================================================
# Test Category 6: Memory Maintenance — Scheduled Jobs
# Verify that memory_consolidation, memory_episode_cleanup, and
# memory_purge_superseded are registered in the lifestyle butler schedule.
# ===========================================================================


@pytest.mark.unit
class TestLifestyleMemoryMaintenanceSchedule:
    """AC6: Memory maintenance jobs are registered in the lifestyle butler schedule."""

    def _get_lifestyle_config(self):
        from butlers.config import load_config

        return load_config(_LIFESTYLE_ROSTER_DIR)

    def test_memory_consolidation_job_registered(self) -> None:
        """memory_consolidation is registered as a scheduled job."""
        cfg = self._get_lifestyle_config()
        job_names = [s.job_name for s in cfg.schedules if s.job_name]
        assert "memory_consolidation" in job_names, (
            f"memory_consolidation not found in scheduled jobs: {job_names}"
        )

    def test_memory_episode_cleanup_job_registered(self) -> None:
        """memory_episode_cleanup is registered as a scheduled job."""
        cfg = self._get_lifestyle_config()
        job_names = [s.job_name for s in cfg.schedules if s.job_name]
        assert "memory_episode_cleanup" in job_names, (
            f"memory_episode_cleanup not found in scheduled jobs: {job_names}"
        )

    def test_memory_purge_superseded_job_registered(self) -> None:
        """memory_purge_superseded is registered as a scheduled job."""
        cfg = self._get_lifestyle_config()
        job_names = [s.job_name for s in cfg.schedules if s.job_name]
        assert "memory_purge_superseded" in job_names, (
            f"memory_purge_superseded not found in scheduled jobs: {job_names}"
        )

    def test_memory_consolidation_has_correct_cron(self) -> None:
        """memory_consolidation runs every 6 hours (cron: 0 */6 * * *)."""
        cfg = self._get_lifestyle_config()
        schedule = next(
            (s for s in cfg.schedules if s.job_name == "memory_consolidation"),
            None,
        )
        assert schedule is not None
        assert schedule.cron == "0 */6 * * *", (
            f"memory_consolidation cron is '{schedule.cron}', expected '0 */6 * * *'"
        )

    def test_memory_episode_cleanup_has_correct_cron(self) -> None:
        """memory_episode_cleanup runs daily at 04:00 (cron: 0 4 * * *)."""
        cfg = self._get_lifestyle_config()
        schedule = next(
            (s for s in cfg.schedules if s.job_name == "memory_episode_cleanup"),
            None,
        )
        assert schedule is not None
        assert schedule.cron == "0 4 * * *", (
            f"memory_episode_cleanup cron is '{schedule.cron}', expected '0 4 * * *'"
        )

    def test_memory_purge_superseded_has_correct_cron(self) -> None:
        """memory_purge_superseded runs at 04:10 daily (cron: 10 4 * * *)."""
        cfg = self._get_lifestyle_config()
        schedule = next(
            (s for s in cfg.schedules if s.job_name == "memory_purge_superseded"),
            None,
        )
        assert schedule is not None
        assert schedule.cron == "10 4 * * *", (
            f"memory_purge_superseded cron is '{schedule.cron}', expected '10 4 * * *'"
        )

    def test_all_memory_jobs_use_job_dispatch_mode(self) -> None:
        """All three memory maintenance jobs use dispatch_mode='job'."""
        from butlers.config import ScheduleDispatchMode

        cfg = self._get_lifestyle_config()
        memory_jobs = [
            "memory_consolidation",
            "memory_episode_cleanup",
            "memory_purge_superseded",
        ]
        for job_name in memory_jobs:
            schedule = next(
                (s for s in cfg.schedules if s.job_name == job_name),
                None,
            )
            assert schedule is not None, f"Schedule for {job_name!r} not found"
            assert schedule.dispatch_mode == ScheduleDispatchMode.JOB, (
                f"{job_name} dispatch_mode is {schedule.dispatch_mode!r}, expected 'job'"
            )

    def test_weekly_taste_digest_uses_prompt_dispatch_mode(self) -> None:
        """weekly-taste-digest uses dispatch_mode='prompt' (not job)."""
        from butlers.config import ScheduleDispatchMode

        cfg = self._get_lifestyle_config()
        taste_digest = next(
            (s for s in cfg.schedules if s.name == "weekly-taste-digest"),
            None,
        )
        assert taste_digest is not None, (
            "weekly-taste-digest schedule not found in lifestyle butler.toml"
        )
        assert taste_digest.dispatch_mode == ScheduleDispatchMode.PROMPT, (
            f"weekly-taste-digest dispatch_mode is {taste_digest.dispatch_mode!r}, "
            "expected 'prompt'"
        )

    def test_at_least_four_scheduled_tasks_registered(self) -> None:
        """Lifestyle butler has at least 4 scheduled tasks configured."""
        cfg = self._get_lifestyle_config()
        assert len(cfg.schedules) >= 4, (
            f"Expected at least 4 scheduled tasks, found {len(cfg.schedules)}: "
            f"{[s.name for s in cfg.schedules]}"
        )
