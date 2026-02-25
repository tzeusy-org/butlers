"""Tests for relationship/CRM Pydantic models."""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

import pytest

# Load relationship models module dynamically
_roster_root = Path(__file__).resolve().parents[2] / "roster"
_models_path = _roster_root / "relationship" / "api" / "models.py"
spec = importlib.util.spec_from_file_location("relationship_api_models", _models_path)
if spec is None or spec.loader is None:
    raise ValueError(f"Could not load spec from {_models_path}")
relationship_models = importlib.util.module_from_spec(spec)
sys.modules["relationship_api_models"] = relationship_models
spec.loader.exec_module(relationship_models)

ContactDetail = relationship_models.ContactDetail
ContactListResponse = relationship_models.ContactListResponse
ContactSummary = relationship_models.ContactSummary
Gift = relationship_models.Gift
Group = relationship_models.Group
GroupListResponse = relationship_models.GroupListResponse
Interaction = relationship_models.Interaction
Label = relationship_models.Label
Loan = relationship_models.Loan
Note = relationship_models.Note
UpcomingDate = relationship_models.UpcomingDate

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Label
# ---------------------------------------------------------------------------


def test_label_minimal():
    """Label with required fields only."""
    label_id = uuid4()
    label = Label(id=label_id, name="Friend")
    assert label.id == label_id
    assert label.name == "Friend"
    assert label.color is None


def test_label_with_color():
    """Label with color."""
    label = Label(id=uuid4(), name="Work", color="blue")
    assert label.color == "blue"


# ---------------------------------------------------------------------------
# ContactSummary
# ---------------------------------------------------------------------------


def test_contact_summary_minimal():
    """ContactSummary with required fields only."""
    cid = uuid4()
    contact = ContactSummary(id=cid, full_name="Alice Smith")
    assert contact.id == cid
    assert contact.full_name == "Alice Smith"
    assert contact.nickname is None
    assert contact.email is None
    assert contact.phone is None
    assert contact.labels == []
    assert contact.last_interaction_at is None


def test_contact_summary_with_labels():
    """ContactSummary with labels."""
    label = Label(id=uuid4(), name="Friend", color="green")
    contact = ContactSummary(
        id=uuid4(),
        full_name="Bob Jones",
        labels=[label],
        last_interaction_at=datetime(2025, 1, 15, tzinfo=UTC),
    )
    assert len(contact.labels) == 1
    assert contact.labels[0].name == "Friend"


# ---------------------------------------------------------------------------
# ContactDetail
# ---------------------------------------------------------------------------


def test_contact_detail():
    """ContactDetail includes all fields."""
    cid = uuid4()
    now = datetime(2025, 1, 1, tzinfo=UTC)
    contact = ContactDetail(
        id=cid,
        full_name="Charlie Davis",
        nickname="CD",
        email="charlie@example.com",
        phone="555-1234",
        labels=[],
        last_interaction_at=now,
        notes="Met at conference",
        birthday=date(1990, 5, 15),
        company="Acme Inc",
        job_title="Engineer",
        address="123 Main St",
        metadata={"linkedin": "charlie-davis"},
        created_at=now,
        updated_at=now,
    )
    assert contact.full_name == "Charlie Davis"
    assert contact.birthday == date(1990, 5, 15)
    assert contact.metadata["linkedin"] == "charlie-davis"


# ---------------------------------------------------------------------------
# Group
# ---------------------------------------------------------------------------


def test_group():
    """Group with member count."""
    gid = uuid4()
    now = datetime(2025, 1, 1, tzinfo=UTC)
    group = Group(
        id=gid,
        name="Family",
        description="Close family members",
        member_count=5,
        labels=[],
        created_at=now,
        updated_at=now,
    )
    assert group.name == "Family"
    assert group.member_count == 5


# ---------------------------------------------------------------------------
# Note
# ---------------------------------------------------------------------------


def test_note():
    """Note model."""
    nid = uuid4()
    cid = uuid4()
    now = datetime(2025, 1, 1, tzinfo=UTC)
    note = Note(
        id=nid,
        contact_id=cid,
        content="Remember to follow up",
        created_at=now,
        updated_at=now,
    )
    assert note.content == "Remember to follow up"


# ---------------------------------------------------------------------------
# Interaction
# ---------------------------------------------------------------------------


def test_interaction():
    """Interaction model."""
    iid = uuid4()
    cid = uuid4()
    occurred = datetime(2025, 1, 10, tzinfo=UTC)
    created = datetime(2025, 1, 10, 12, 0, tzinfo=UTC)
    interaction = Interaction(
        id=iid,
        contact_id=cid,
        type="email",
        summary="Checked in about project",
        details="Discussed Q1 goals",
        occurred_at=occurred,
        created_at=created,
    )
    assert interaction.type == "email"
    assert interaction.summary == "Checked in about project"


# ---------------------------------------------------------------------------
# Gift
# ---------------------------------------------------------------------------


def test_gift():
    """Gift model."""
    gid = uuid4()
    cid = uuid4()
    now = datetime(2025, 1, 15, tzinfo=UTC)
    gift = Gift(
        id=gid,
        contact_id=cid,
        description="Coffee mug",
        occasion="Birthday",
        status="idea",
        created_at=now,
        updated_at=now,
    )
    assert gift.description == "Coffee mug"
    assert gift.status == "idea"
    assert gift.occasion == "Birthday"


# ---------------------------------------------------------------------------
# Loan
# ---------------------------------------------------------------------------


def test_loan():
    """Loan model."""
    lid = uuid4()
    cid = uuid4()
    loan = Loan(
        id=lid,
        contact_id=cid,
        amount=35.0,
        direction="lent",
        description="Book: Clean Code",
        settled=False,
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
    )
    assert loan.description == "Book: Clean Code"
    assert loan.amount == 35.0
    assert loan.settled is False


# ---------------------------------------------------------------------------
# UpcomingDate
# ---------------------------------------------------------------------------


def test_upcoming_date():
    """UpcomingDate model."""
    cid = uuid4()
    upcoming = UpcomingDate(
        contact_id=cid,
        contact_name="Alice",
        date_type="birthday",
        date=date(2025, 2, 20),
        days_until=5,
    )
    assert upcoming.contact_name == "Alice"
    assert upcoming.date_type == "birthday"
    assert upcoming.days_until == 5


# ---------------------------------------------------------------------------
# ContactListResponse
# ---------------------------------------------------------------------------


def test_contact_list_response():
    """ContactListResponse pagination."""
    contacts = [
        ContactSummary(id=uuid4(), full_name="Alice"),
        ContactSummary(id=uuid4(), full_name="Bob"),
    ]
    response = ContactListResponse(contacts=contacts, total=10)
    assert len(response.contacts) == 2
    assert response.total == 10


# ---------------------------------------------------------------------------
# GroupListResponse
# ---------------------------------------------------------------------------


def test_group_list_response():
    """GroupListResponse pagination."""
    now = datetime(2025, 1, 1, tzinfo=UTC)
    groups = [
        Group(
            id=uuid4(),
            name="Work",
            description=None,
            member_count=5,
            labels=[],
            created_at=now,
            updated_at=now,
        )
    ]
    response = GroupListResponse(groups=groups, total=1)
    assert len(response.groups) == 1
    assert response.total == 1
