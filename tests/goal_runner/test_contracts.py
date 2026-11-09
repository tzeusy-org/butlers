"""Runner refusals that happen before any model is invoked (bu-dhloi1.208)."""

import pytest

from butlers.core.model_call import NotInvoked
from butlers.goal_runner.contracts import GoalEnded, InvocationBudgetExhausted

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("refusal", [GoalEnded, InvocationBudgetExhausted])
def test_an_open_refusal_is_recorded_as_not_invoked(refusal):
    # The kernel records these as non-model outcomes, so a raced or
    # budget-refused open never counts against the model's breaker or evidence.
    assert issubclass(refusal, NotInvoked)
    # The runner still tells them apart from runtime failures by type.
    assert issubclass(refusal, RuntimeError)
