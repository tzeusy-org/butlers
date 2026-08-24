"""``save_cursor``'s call boundary must force an ownership decision (bu-ogs8x).

The behavioural half of this regression lives in
``tests/integration/test_cursor_store_parent_declaration.py``, which needs a
real Postgres. This half needs neither Docker nor a pool: it pins the *shape* of
the call boundary, which is where the defect actually originated.

``parent_endpoint_identity`` used to default to ``None``. Five of the six
connectors that save cursors never passed it, so their rows were born
``operational_role = 'checkpoint'`` with a NULL parent and refilled the
dashboard's ``unparented_checkpoints`` bucket after sw_031's one-shot backfill
emptied it. Nothing about that was visible at the call site — the argument was
simply absent. Removing the default converts a silent omission into a
``TypeError`` the first time a new connector saves a cursor.
"""

from __future__ import annotations

import inspect

import pytest

from butlers.connectors.cursor_store import NO_PARENT, save_cursor

pytestmark = pytest.mark.unit


def test_parent_declaration_has_no_default() -> None:
    """A new call site cannot inherit the NULL by leaving the argument out."""
    param = inspect.signature(save_cursor).parameters["parent_endpoint_identity"]
    assert param.kind is inspect.Parameter.KEYWORD_ONLY
    assert param.default is inspect.Parameter.empty, (
        "parent_endpoint_identity must stay required: an optional ownership "
        "declaration is what let five connectors silently write unparented rows."
    )


async def test_omitting_the_declaration_raises_at_the_call_boundary() -> None:
    """The failure is a loud TypeError, not a quietly mislabelled registry row."""
    with pytest.raises(TypeError, match="parent_endpoint_identity"):
        await save_cursor(None, "synthetic_connector", "synthetic:endpoint", "cursor")  # type: ignore[call-arg]


def test_no_parent_is_the_named_spelling_of_the_null_decision() -> None:
    """``NO_PARENT`` exists so a deliberate "no parent" reads as a decision.

    It is ``None`` on the wire — the column is nullable and stays that way — but
    a reader scanning a connector sees a named choice rather than a bare
    ``None`` indistinguishable from an oversight.
    """
    assert NO_PARENT is None
