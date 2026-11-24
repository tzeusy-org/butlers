"""Exact existing sites admitted for S1a only; never regenerate on a gate run.

Remove a row when its violation is removed in a later reviewed slice. Adding a
row needs source review: a changed count alone cannot authorize an exception.
"""

import json
from pathlib import Path

from butlers.testing.source_guard import Violation
from tests.architecture.source_rules import Offender

OFFENDERS = {
    rule: tuple(
        Offender(
            Violation(Path(row["path"]), row["line"], row["rule"], row["detail"]), row["reason"]
        )
        for row in rows
    )
    for rule, rows in json.loads(Path(__file__).with_suffix(".json").read_text()).items()
}
