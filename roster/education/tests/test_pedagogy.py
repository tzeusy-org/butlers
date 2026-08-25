"""Unit tests for education pedagogy: technique selection, citations, reading pathways.

All tests mock the asyncpg pool — no live database required.

Coverage:
- select_technique / technique_for_node:
    - one distinct technique per concept type
    - Socratic default when the concept type is unset or unrecognized
    - every technique carries the principle the session cites for transparency
- teaching_cite_source:
    - provenance is required and recorded verbatim (bu-oxdtt)
    - "referenced" is only accepted when the named source is registered
    - a model-recalled ref stays model-recalled even against a registered source
    - dedupe on (source_id, location), node-not-found, malformed input
- teaching_reading_pathways:
    - one pathway per ref whose source still resolves
    - nothing suggested when no registered source covers the concept
"""

from __future__ import annotations

import json
import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.unit

_SOURCE_ID = "8d0f2f1e-3f6a-4a1b-9d5f-2c9a1b7e4d33"
_OTHER_SOURCE_ID = "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"


class _MockRecord:
    """Minimal asyncpg.Record-like object backed by a dict."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __iter__(self):
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def keys(self):
        return self._data.keys()

    def values(self):
        return self._data.values()

    def items(self):
        return self._data.items()

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)


def _node_pool(node: dict[str, Any] | None) -> AsyncMock:
    """Build a pool whose node lookup returns *node* (or nothing)."""
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None if node is None else _MockRecord(node))
    pool.execute = AsyncMock(return_value="UPDATE 1")
    return pool


def _source(source_id: str = _SOURCE_ID, **overrides: Any) -> dict[str, Any]:
    record = {
        "source_id": source_id,
        "title": "Structure and Interpretation of Computer Programs",
        "authors": ["Abelson", "Sussman"],
        "type": "book",
        "registered_at": "2026-01-01T00:00:00+00:00",
    }
    record.update(overrides)
    return record


def _written_metadata(pool: AsyncMock) -> dict[str, Any]:
    """Return the metadata patch handed to the single UPDATE."""
    assert pool.execute.await_count == 1, "expected exactly one metadata write"
    args = pool.execute.await_args.args
    return json.loads(args[2])


def _registry(*sources: dict[str, Any]):
    return patch(
        "butlers.tools.education.pedagogy.source_material_list",
        AsyncMock(return_value=list(sources)),
    )


# ---------------------------------------------------------------------------
# Technique selection
# ---------------------------------------------------------------------------


class TestSelectTechnique:
    def test_each_concept_type_gets_its_own_technique(self) -> None:
        from butlers.tools.education.pedagogy import select_technique

        chosen = {
            concept_type: select_technique(concept_type)["id"]
            for concept_type in ("factual", "procedural", "conceptual", "creative")
        }
        assert chosen["factual"] == "retrieval-practice"
        assert chosen["procedural"] == "worked-example"
        assert chosen["conceptual"] == "socratic-analogy"
        assert chosen["creative"] == "divergent-then-critique"
        assert len(set(chosen.values())) == 4, "technique must vary by concept type"

    def test_unset_concept_type_falls_back_to_socratic(self) -> None:
        from butlers.tools.education.pedagogy import DEFAULT_TECHNIQUE_ID, select_technique

        technique = select_technique(None)
        assert technique["id"] == DEFAULT_TECHNIQUE_ID == "socratic"
        assert technique["concept_type"] is None

    def test_unrecognized_concept_type_falls_back_to_socratic(self) -> None:
        """An unknown word must not silently pick a technique it does not name."""
        from butlers.tools.education.pedagogy import select_technique

        assert select_technique("kinaesthetic")["id"] == "socratic"

    def test_every_technique_states_its_pedagogical_principle(self) -> None:
        """Transparency ("why this approach?") is answered from the record itself."""
        from butlers.tools.education.pedagogy import select_technique

        for concept_type in (None, "factual", "procedural", "conceptual", "creative"):
            technique = select_technique(concept_type)
            assert technique["principle"].strip(), f"{concept_type} has no principle"
            assert technique["label"].strip()
            assert technique["moves"], f"{concept_type} has no teaching moves"

    def test_returned_record_is_a_copy(self) -> None:
        from butlers.tools.education.pedagogy import select_technique

        first = select_technique("factual")
        first["principle"] = "mutated"
        assert select_technique("factual")["principle"] != "mutated"


class TestTechniqueForNode:
    def test_reads_concept_type_from_node_metadata(self) -> None:
        from butlers.tools.education.pedagogy import technique_for_node

        node = {"id": "n1", "metadata": {"concept_type": "procedural"}}
        assert technique_for_node(node)["id"] == "worked-example"

    def test_node_without_metadata_gets_the_default(self) -> None:
        from butlers.tools.education.pedagogy import technique_for_node

        assert technique_for_node({"id": "n1"})["id"] == "socratic"
        assert technique_for_node(None)["id"] == "socratic"


# ---------------------------------------------------------------------------
# Citations
# ---------------------------------------------------------------------------


class TestTeachingCiteSource:
    async def test_ref_read_from_a_registered_source_is_stored_as_referenced(self) -> None:
        """A ref written after actually reading a registered source earns "referenced".

        The dashboard renders exactly this stored value (frontend
        NodeDetailPanel.test.tsx, "renders a registered, source-read ref as a
        citation"), so the writer is what decides whether the owner sees a
        citation at all.
        """
        from butlers.tools.education.pedagogy import teaching_cite_source

        node_id = str(uuid.uuid4())
        pool = _node_pool({"label": "Recursion", "metadata": {}})

        with _registry(_source()):
            result = await teaching_cite_source(
                pool,
                node_id,
                location="chapter 1.2",
                provenance="referenced",
                source_id=_SOURCE_ID,
            )

        assert result["source_ref"] == {
            "source_id": _SOURCE_ID,
            "location": "chapter 1.2",
            "provenance": "referenced",
        }
        assert result["created"] is True
        assert _written_metadata(pool)["source_refs"] == [result["source_ref"]]

    async def test_model_recalled_ref_stays_recalled_when_its_source_resolves(self) -> None:
        """Recall against a registered source is still recall.

        The registry confirms the *source* exists; it says nothing about
        whether anything read the *location*. A writer that let a resolvable
        `source_id` imply "referenced" would hand the display a citation the
        butler never earned.
        """
        from butlers.tools.education.pedagogy import teaching_cite_source

        node_id = str(uuid.uuid4())
        pool = _node_pool({"label": "Recursion", "metadata": {}})

        with _registry(_source()):
            result = await teaching_cite_source(
                pool,
                node_id,
                location="chapter 1.2",
                provenance="model-recalled",
                source_id=_SOURCE_ID,
            )

        assert result["source_ref"]["provenance"] == "model-recalled"
        assert _written_metadata(pool)["source_refs"][0]["provenance"] == "model-recalled"

    async def test_provenance_must_be_stated(self) -> None:
        """There is no default: the writer knows what it did and must say so."""
        from butlers.tools.education.pedagogy import teaching_cite_source

        pool = _node_pool({"label": "Recursion", "metadata": {}})
        with pytest.raises(TypeError):
            await teaching_cite_source(  # type: ignore[call-arg]
                pool,
                str(uuid.uuid4()),
                location="chapter 1.2",
                source_id=_SOURCE_ID,
            )

    async def test_unknown_provenance_is_rejected(self) -> None:
        from butlers.tools.education.pedagogy import teaching_cite_source

        pool = _node_pool({"label": "Recursion", "metadata": {}})
        with _registry(_source()), pytest.raises(ValueError, match="provenance"):
            await teaching_cite_source(
                pool,
                str(uuid.uuid4()),
                location="chapter 1.2",
                provenance="verified",
                source_id=_SOURCE_ID,
            )
        pool.execute.assert_not_awaited()

    async def test_referenced_requires_a_registered_source(self) -> None:
        """You cannot claim to have read a source the owner never registered."""
        from butlers.tools.education.pedagogy import teaching_cite_source

        pool = _node_pool({"label": "Recursion", "metadata": {}})
        with _registry(_source(_OTHER_SOURCE_ID)), pytest.raises(ValueError, match="registered"):
            await teaching_cite_source(
                pool,
                str(uuid.uuid4()),
                location="chapter 1.2",
                provenance="referenced",
                source_id=_SOURCE_ID,
            )
        pool.execute.assert_not_awaited()

    async def test_referenced_without_a_source_id_is_rejected(self) -> None:
        from butlers.tools.education.pedagogy import teaching_cite_source

        pool = _node_pool({"label": "Recursion", "metadata": {}})
        with _registry(), pytest.raises(ValueError, match="source_id"):
            await teaching_cite_source(
                pool,
                str(uuid.uuid4()),
                location="chapter 1.2",
                provenance="referenced",
            )
        pool.execute.assert_not_awaited()

    async def test_model_recalled_citation_without_a_registered_source(self) -> None:
        """A well-known book the owner never registered is cited with a null ID."""
        from butlers.tools.education.pedagogy import teaching_cite_source

        pool = _node_pool({"label": "Recursion", "metadata": {}})
        with _registry():
            result = await teaching_cite_source(
                pool,
                str(uuid.uuid4()),
                location="the standard proof in Knuth vol. 1",
                provenance="model-recalled",
                note="from memory — check the page",
            )

        assert result["source_ref"]["source_id"] is None
        assert result["source_ref"]["provenance"] == "model-recalled"
        assert result["source_ref"]["note"] == "from memory — check the page"

    async def test_unregistered_source_id_is_rejected(self) -> None:
        """A source_id nothing resolves is a dangling ref, not a citation."""
        from butlers.tools.education.pedagogy import teaching_cite_source

        pool = _node_pool({"label": "Recursion", "metadata": {}})
        with _registry(_source(_OTHER_SOURCE_ID)), pytest.raises(ValueError, match="registered"):
            await teaching_cite_source(
                pool,
                str(uuid.uuid4()),
                location="chapter 1.2",
                provenance="model-recalled",
                source_id=_SOURCE_ID,
            )

    async def test_blank_location_is_rejected(self) -> None:
        from butlers.tools.education.pedagogy import teaching_cite_source

        pool = _node_pool({"label": "Recursion", "metadata": {}})
        with _registry(_source()), pytest.raises(ValueError, match="location"):
            await teaching_cite_source(
                pool,
                str(uuid.uuid4()),
                location="   ",
                provenance="referenced",
                source_id=_SOURCE_ID,
            )

    async def test_existing_ref_is_not_duplicated_and_provenance_is_refreshed(self) -> None:
        """Re-citing the same location after reading it upgrades the planner's guess."""
        from butlers.tools.education.pedagogy import teaching_cite_source

        existing = {
            "source_id": _SOURCE_ID,
            "location": "chapter 1.2",
            "provenance": "model-recalled",
        }
        pool = _node_pool({"label": "Recursion", "metadata": {"source_refs": [existing]}})

        with _registry(_source()):
            result = await teaching_cite_source(
                pool,
                str(uuid.uuid4()),
                location="chapter 1.2",
                provenance="referenced",
                source_id=_SOURCE_ID,
            )

        refs = _written_metadata(pool)["source_refs"]
        assert len(refs) == 1
        assert refs[0]["provenance"] == "referenced"
        assert result["created"] is False

    async def test_existing_refs_are_preserved(self) -> None:
        from butlers.tools.education.pedagogy import teaching_cite_source

        existing = {
            "source_id": _OTHER_SOURCE_ID,
            "location": "chapter 4",
            "provenance": "referenced",
        }
        pool = _node_pool({"label": "Recursion", "metadata": {"source_refs": [existing]}})

        with _registry(_source(), _source(_OTHER_SOURCE_ID, title="Another book")):
            await teaching_cite_source(
                pool,
                str(uuid.uuid4()),
                location="chapter 1.2",
                provenance="referenced",
                source_id=_SOURCE_ID,
            )

        refs = _written_metadata(pool)["source_refs"]
        assert len(refs) == 2
        assert refs[0] == existing

    async def test_missing_node_raises(self) -> None:
        from butlers.tools.education.pedagogy import teaching_cite_source

        pool = _node_pool(None)
        with _registry(_source()), pytest.raises(ValueError, match="Node not found"):
            await teaching_cite_source(
                pool,
                str(uuid.uuid4()),
                location="chapter 1.2",
                provenance="referenced",
                source_id=_SOURCE_ID,
            )
        pool.execute.assert_not_awaited()

    async def test_string_encoded_metadata_is_decoded(self) -> None:
        """Defensive: a pool without the JSONB codec hands back a string."""
        from butlers.tools.education.pedagogy import teaching_cite_source

        existing = {
            "source_id": _SOURCE_ID,
            "location": "chapter 4",
            "provenance": "referenced",
        }
        pool = _node_pool(
            {"label": "Recursion", "metadata": json.dumps({"source_refs": [existing]})}
        )

        with _registry(_source()):
            await teaching_cite_source(
                pool,
                str(uuid.uuid4()),
                location="chapter 1.2",
                provenance="referenced",
                source_id=_SOURCE_ID,
            )

        assert len(_written_metadata(pool)["source_refs"]) == 2


# ---------------------------------------------------------------------------
# Reading pathways
# ---------------------------------------------------------------------------


class TestReadingPathways:
    async def test_registered_refs_become_pathways(self) -> None:
        from butlers.tools.education.pedagogy import teaching_reading_pathways

        node = {
            "label": "Recursion",
            "metadata": {
                "source_refs": [
                    {
                        "source_id": _SOURCE_ID,
                        "location": "chapter 1.2, pp. 45–52",
                        "provenance": "referenced",
                        "note": "the substitution model",
                    }
                ]
            },
        }
        pool = _node_pool(node)

        with _registry(_source(url="https://example.test/sicp")):
            result = await teaching_reading_pathways(pool, str(uuid.uuid4()))

        assert result["node_label"] == "Recursion"
        assert result["pathways"] == [
            {
                "source_id": _SOURCE_ID,
                "title": "Structure and Interpretation of Computer Programs",
                "authors": ["Abelson", "Sussman"],
                "type": "book",
                "url": "https://example.test/sicp",
                "location": "chapter 1.2, pp. 45–52",
                "provenance": "referenced",
                "note": "the substitution model",
            }
        ]

    async def test_no_pathways_when_no_source_covers_the_concept(self) -> None:
        from butlers.tools.education.pedagogy import teaching_reading_pathways

        pool = _node_pool({"label": "Recursion", "metadata": {}})
        with _registry(_source()):
            result = await teaching_reading_pathways(pool, str(uuid.uuid4()))

        assert result["pathways"] == []

    async def test_dangling_ref_is_skipped_not_suggested(self) -> None:
        """A removed source cannot be read, so it is not a pathway."""
        from butlers.tools.education.pedagogy import teaching_reading_pathways

        node = {
            "label": "Recursion",
            "metadata": {
                "source_refs": [
                    {"source_id": _SOURCE_ID, "location": "chapter 1.2", "provenance": "referenced"}
                ]
            },
        }
        pool = _node_pool(node)

        with _registry(_source(_OTHER_SOURCE_ID)):
            result = await teaching_reading_pathways(pool, str(uuid.uuid4()))

        assert result["pathways"] == []
        assert result["skipped_unregistered"] == 1

    async def test_recalled_ref_without_a_source_is_not_a_pathway(self) -> None:
        from butlers.tools.education.pedagogy import teaching_reading_pathways

        node = {
            "label": "Recursion",
            "metadata": {
                "source_refs": [{"source_id": None, "location": "the standard proof"}],
            },
        }
        pool = _node_pool(node)

        with _registry(_source()):
            result = await teaching_reading_pathways(pool, str(uuid.uuid4()))

        assert result["pathways"] == []
        assert result["recalled_without_source"] == 1

    async def test_recalled_location_in_a_registered_source_is_flagged(self) -> None:
        """The pathway is still worth suggesting — but not as a verified location."""
        from butlers.tools.education.pedagogy import teaching_reading_pathways

        node = {
            "label": "Recursion",
            "metadata": {
                "source_refs": [
                    {
                        "source_id": _SOURCE_ID,
                        "location": "chapter 1 somewhere",
                        "provenance": "model-recalled",
                    }
                ]
            },
        }
        pool = _node_pool(node)

        with _registry(_source()):
            result = await teaching_reading_pathways(pool, str(uuid.uuid4()))

        assert len(result["pathways"]) == 1
        assert result["pathways"][0]["provenance"] == "model-recalled"

    async def test_malformed_refs_are_ignored(self) -> None:
        from butlers.tools.education.pedagogy import teaching_reading_pathways

        node = {
            "label": "Recursion",
            "metadata": {"source_refs": ["chapter 1", None, {}, {"source_id": _SOURCE_ID}]},
        }
        pool = _node_pool(node)

        with _registry(_source()):
            result = await teaching_reading_pathways(pool, str(uuid.uuid4()))

        assert result["pathways"] == []

    async def test_missing_node_raises(self) -> None:
        from butlers.tools.education.pedagogy import teaching_reading_pathways

        pool = _node_pool(None)
        with _registry(_source()), pytest.raises(ValueError, match="Node not found"):
            await teaching_reading_pathways(pool, str(uuid.uuid4()))
