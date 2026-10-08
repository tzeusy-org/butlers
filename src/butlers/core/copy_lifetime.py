"""Private holder binding shared by registered runtime producers and readers.

The producer remains the actual admission guard. This dependency-free cell
records a native receiving lifetime only; it grants no fact/custody authority.
"""

from contextvars import ContextVar
from typing import Any

_current_copy_invocation: ContextVar[Any] = ContextVar("registered_copy_invocation", default=None)
