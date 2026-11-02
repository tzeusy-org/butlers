"""Predicate-derived knowledge class for memory facts (bu-bagfg).

Profile Facts must hold durable identity/preference knowledge. Event and
transaction facts (purchases, doses, meals, interactions) are owner-class when
the owner authored them, so content authority cannot keep them out; the
predicate does. The class is a pure function of the normalized predicate, so
it is deterministic, needs no schema column or backfill, and applies equally to
legacy rows. Event-class facts remain stored and recallable; only the Profile
Facts pack excludes them. Unknown predicates default to ``durable`` (the
pre-existing behavior).
"""

from __future__ import annotations

from typing import Literal

KnowledgeClass = Literal["durable", "event"]

# Seeded transactional / one-off-happening predicates (see migrations 002/003).
EVENT_PREDICATES: frozenset[str] = frozenset(
    {
        "transaction_debit",
        "transaction_credit",
        "took_dose",
        "symptom",
        "measurement_weight",
        "measurement_blood_pressure",
        "measurement_heart_rate",
        "measurement_blood_sugar",
        "measurement_temperature",
        "meal_breakfast",
        "meal_lunch",
        "meal_dinner",
        "meal_snack",
        "interaction",
        "life_event",
        "contact_note",
        "contact_task",
        "activity",
        "gift",
        "loan",
        "reminder",
    }
)


def knowledge_class(predicate: str) -> KnowledgeClass:
    """Return ``"event"`` for event/transaction predicates, else ``"durable"``."""
    normalized = predicate.lower().replace("-", "_").replace(" ", "_")
    return "event" if normalized in EVENT_PREDICATES else "durable"
