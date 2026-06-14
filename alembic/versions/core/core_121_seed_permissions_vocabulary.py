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

All five seeded permissions are now ENFORCED at runtime at their own call
sites (``butlers.core.permissions.check_permission`` / ``require_permission``):
``spawn`` (Spawner), ``cross_butler`` (Switchboard route_to_butler),
``notify`` (notify() core tool / telegram), ``email.send`` (email module), and
``calendar.write`` (calendar module). They are the live governance surface the
matrix exposes, not inert config.

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
#   cross_butler  — ENFORCED: butler may invoke other butlers via the Switchboard
#                   (core_tools/_switchboard.route_to_butler).
#   notify        — ENFORCED: butler may send owner-facing notifications
#                   (notify() core tool / telegram module).
#   email.send    — ENFORCED: butler may send email on the owner's behalf
#                   (email module _send_email).
#   calendar.write— ENFORCED: butler may create/modify calendar events
#                   (calendar module _require_calendar_write_permission).
#
# All five permissions are enforced at their own call sites today. Seeding them
# makes the matrix a real, non-empty control surface instead of an empty grid.
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
