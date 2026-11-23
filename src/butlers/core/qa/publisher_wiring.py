"""Whether the production QA dispatcher injects a trusted publisher (bu-ejow5p.8).

One fact, one place. ``QaModule`` builds its ``QaDispatchConfig`` without a ``publisher``, so every
committed investigation ends ``publication_unavailable`` (REQ-qa-investigation-dispatch-001). The
reset route re-evaluates the ``publisher_unconfigured`` cause from this function, not from the
failure history, so the moment a deploy wires a publisher and flips this to ``True`` a cause-bound
reset naming it is accepted, and until then no reference can buy a reset.

``tests/core/qa/test_publisher_wiring.py`` fails if the module wires a publisher while this still
says ``False`` (or the reverse), so the answer cannot drift from the code.
"""

from __future__ import annotations


def production_publisher_wired() -> bool:
    """True once ``QaModule`` injects a ``QaPublisher`` into ``QaDispatchConfig``."""
    return False
