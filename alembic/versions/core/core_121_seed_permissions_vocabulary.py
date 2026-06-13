"""Seed the permissions matrix vocabulary so it renders non-empty.

Revision ID: core_121
Revises: core_120
Create Date: 2026-06-14 00:00:00.000000

The Settings → Permissions matrix (``public.permissions``) is built entirely
from the rows that exist in the table — the GET endpoint derives both axes
(butlers × permissions) from ``SELECT DISTINCT`` over existing rows. With no
rows seeded, the matrix renders EMPTY live: there is nothing to grant or revoke,
so the page is decorative.

This migration seeds the matrix vocabulary: one row per
``(butler, permission)`` pair across the known butler roster and a small, real
capability vocabulary, all ``granted=true`` by default (opt-in deny — the owner
revokes a capability by flipping a cell to ``granted=false``).

The ``spawn`` permission is the one currently ENFORCED at runtime
(``butlers.core.permissions.check_permission`` consulted by the Spawner). The
remaining permissions are seeded as real, owner-visible options whose enforcement
is tracked as follow-up work; they are not silently inert config — they are the
governance surface the matrix is meant to expose.

Idempotent: ``INSERT ... ON CONFLICT (butler, permission) DO NOTHING`` so it
never clobbers an owner's later grant/revoke decisions on re-run.
"""

from __future__ import annotations

from alembic import op

revision = "core_121"
down_revision = "core_120"
branch_labels = None
depends_on = None

# Butler roster (mirrors roster/ and the runtime-role list in core_095).
_BUTLERS = (
    "chronicler",
    "education",
    "finance",
    "general",
    "health",
    "home",
    "lifestyle",
    "messenger",
    "qa",
    "relationship",
    "switchboard",
    "travel",
)

# Permission vocabulary surfaced in the matrix.
#
#   spawn         — ENFORCED: butler may spawn an ephemeral runtime session at
#                   all (the universal "may this butler act?" gate). Consulted by
#                   butlers.core.permissions.check_permission in the Spawner.
#   cross_butler  — butler may invoke other butlers via the Switchboard.
#   notify        — butler may send owner-facing notifications.
#   email.send    — butler may send email on the owner's behalf.
#   calendar.write— butler may create/modify calendar events.
#
# Only ``spawn`` is enforced today; the rest are governance options whose
# enforcement is flagged for follow-up. Seeding them makes the matrix a real,
# non-empty control surface instead of an empty grid.
_PERMISSIONS = (
    "spawn",
    "cross_butler",
    "notify",
    "email.send",
    "calendar.write",
)

_SEED_REASON = "seeded default (core_121)"


def upgrade() -> None:
    rows = ", ".join(
        f"('{butler}', '{perm}', TRUE, '{_SEED_REASON}')"
        for butler in _BUTLERS
        for perm in _PERMISSIONS
    )
    op.execute(
        f"""
        INSERT INTO public.permissions (butler, permission, granted, reason)
        VALUES {rows}
        ON CONFLICT (butler, permission) DO NOTHING
        """
    )


def downgrade() -> None:
    butlers_list = ", ".join(f"'{b}'" for b in _BUTLERS)
    perms_list = ", ".join(f"'{p}'" for p in _PERMISSIONS)
    # Only remove the seeded rows that still carry the seed reason — never delete
    # rows the owner has since edited (their reason will differ).
    op.execute(
        f"""
        DELETE FROM public.permissions
        WHERE butler IN ({butlers_list})
          AND permission IN ({perms_list})
          AND reason = '{_SEED_REASON}'
        """
    )
