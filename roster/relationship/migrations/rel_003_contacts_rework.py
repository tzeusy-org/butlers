"""rel_003_contacts_rework

Revision ID: rel_003
Revises: rel_002
Create Date: 2026-02-12 00:00:00.000000

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "rel_003"
down_revision = "rel_002f"
branch_labels = ("relationship",)
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE contacts ADD COLUMN IF NOT EXISTS first_name VARCHAR")
    op.execute("ALTER TABLE contacts ADD COLUMN IF NOT EXISTS last_name VARCHAR")
    op.execute("ALTER TABLE contacts ADD COLUMN IF NOT EXISTS nickname VARCHAR")
    op.execute("ALTER TABLE contacts ADD COLUMN IF NOT EXISTS company VARCHAR")
    op.execute("ALTER TABLE contacts ADD COLUMN IF NOT EXISTS job_title VARCHAR")
    op.execute("ALTER TABLE contacts ADD COLUMN IF NOT EXISTS gender VARCHAR")
    op.execute("ALTER TABLE contacts ADD COLUMN IF NOT EXISTS pronouns VARCHAR")
    op.execute("ALTER TABLE contacts ADD COLUMN IF NOT EXISTS avatar_url VARCHAR")
    op.execute("ALTER TABLE contacts ADD COLUMN IF NOT EXISTS listed BOOLEAN NOT NULL DEFAULT true")
    op.execute("ALTER TABLE contacts ADD COLUMN IF NOT EXISTS metadata JSONB")

    op.execute("""
        UPDATE contacts
        SET first_name = COALESCE(first_name, details->>'first_name', SPLIT_PART(name, ' ', 1)),
            last_name = COALESCE(last_name, details->>'last_name', SPLIT_PART(name, ' ', 2)),
            nickname = COALESCE(nickname, details->>'nickname'),
            company = COALESCE(company, details->>'company'),
            job_title = COALESCE(job_title, details->>'job_title'),
            metadata = COALESCE(metadata, details, '{}'::jsonb),
            listed = COALESCE(listed, true)
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE contacts DROP COLUMN IF EXISTS metadata")
    op.execute("ALTER TABLE contacts DROP COLUMN IF EXISTS listed")
    op.execute("ALTER TABLE contacts DROP COLUMN IF EXISTS avatar_url")
    op.execute("ALTER TABLE contacts DROP COLUMN IF EXISTS pronouns")
    op.execute("ALTER TABLE contacts DROP COLUMN IF EXISTS gender")
    op.execute("ALTER TABLE contacts DROP COLUMN IF EXISTS job_title")
    op.execute("ALTER TABLE contacts DROP COLUMN IF EXISTS company")
    op.execute("ALTER TABLE contacts DROP COLUMN IF EXISTS nickname")
    op.execute("ALTER TABLE contacts DROP COLUMN IF EXISTS last_name")
    op.execute("ALTER TABLE contacts DROP COLUMN IF EXISTS first_name")
