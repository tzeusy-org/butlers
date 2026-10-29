from uuid import uuid4

import pytest

from butlers.core.confirm_models import ConfirmCard, ConfirmOption, ConfirmPrompt, Recipient

pytestmark = pytest.mark.unit


def test_immutable_prompt_validation_and_inert_card():
    recipient = Recipient(entity_id=uuid4(), sender_id="42", chat_id="-100", owner=True)
    prompt = ConfirmPrompt(
        question="Which day?",
        options=(ConfirmOption("yes", "Yes"), ConfirmOption("no", "No")),
        recipient=recipient,
        card=ConfirmCard(
            facts=("Day: Tuesday",),
            claims=("Likely work",),
            quotes=("https://host.example/x <script>",),
        ),
    )
    assert prompt.timeout_seconds == 600
    text = prompt.render_text()
    assert "Day: Tuesday" in text and "Assistant claim: Likely work" in text
    assert "host[.]example" in text and "https://" not in text
    for changes in (
        {"options": (ConfirmOption("yes", "Yes"),)},
        {"options": (ConfirmOption("yes", "Yes"), ConfirmOption("yes", "Again"))},
        {"timeout_seconds": 29},
        {"timeout_seconds": True},
        {"timeout_seconds": 86401},
        {"on_timeout_value": "missing"},
        {"question": ""},
    ):
        from dataclasses import replace

        with pytest.raises(ValueError):
            replace(prompt, **changes)
