"""Owner mode read policies: the readers activation names see every row, writers none.

Revision ID: sw_078
Revises: sw_077

bu-dhloi1.21 review A1. sw_076's ``owner_mode_overlay`` and sw_077's
``owner_mode_entries`` enable row-level security with no policy, so a role
later granted SELECT would read zero rows without any error. That fails open: the
``owner.mode`` projector would publish an empty mode set as fresh, and an
overlay reader would see every mode's default profile, looser than the owner's
edits. These policies are what activation needs, landed now, so activation is
grants only:

* ``perception_context_writer`` (the ``owner.mode`` projector) reads
  ``owner_mode_entries``.
* ``butler_switchboard_rw`` (the switchboard daemon role, where a mode consumer
  runs) reads ``owner_mode_overlay`` and ``owner_mode_entries``.

No role gets an INSERT, UPDATE or DELETE policy: owner edits, entries and exits
run on the dashboard's owner connection (the table owner, outside RLS, as for
sw_059's owner policy), so the daemon role and every model session's tools stay
unable to write a mode even if core_001 re-grants own-schema DML.

Nothing is granted: a policy without a privilege is inert, so both tables stay
dark. The downgrade drops the policies.
"""

from alembic import op

revision = "sw_078"
down_revision = "sw_077"
branch_labels = None
depends_on = None

_POLICIES = (
    ("owner_mode_entries", "owner_mode_entries_projection_read", "perception_context_writer"),
    ("owner_mode_entries", "owner_mode_entries_runtime_read", "butler_switchboard_rw"),
    ("owner_mode_overlay", "owner_mode_overlay_runtime_read", "butler_switchboard_rw"),
)


def upgrade() -> None:
    op.execute(
        ";\n".join(
            f"CREATE POLICY {name} ON switchboard.{table} FOR SELECT TO {role} "
            f"USING (current_user = '{role}')"
            for table, name, role in _POLICIES
        )
        + ";"
    )


def downgrade() -> None:
    op.execute(
        ";\n".join(
            f"DROP POLICY IF EXISTS {name} ON switchboard.{table}" for table, name, _ in _POLICIES
        )
        + ";"
    )
