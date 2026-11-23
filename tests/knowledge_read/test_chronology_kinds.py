"""The timeline ``kinds`` filter accepts ``leg`` as an alias of the stored ``trip_leg`` kind."""

from __future__ import annotations

import pytest

from butlers.knowledge_read.chronology import LEG_KIND, normalize_kinds

pytestmark = pytest.mark.unit


def test_leg_alias_matches_the_stored_trip_leg_kind():
    assert normalize_kinds(("leg",)) == [LEG_KIND] == ["trip_leg"]


def test_alias_and_canonical_kind_collapse_and_episode_kinds_pass_through():
    assert normalize_kinds(("leg", "trip_leg", "work")) == ["trip_leg", "work"]


def test_unknown_kinds_are_left_alone_and_absent_means_no_filter():
    assert normalize_kinds(("flight",)) == ["flight"]
    assert normalize_kinds(None) is None
