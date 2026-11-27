"""Retain the trusted target of owner-published QA proposals (bu-ejow5p.7.4)."""

from alembic import op

revision = "core_382"
down_revision = "core_375"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_382', 0))")
    op.execute(
        "ALTER TABLE public.qa_publication_exports ADD COLUMN IF NOT EXISTS target_repository TEXT"
    )
    op.execute(
        "ALTER TABLE public.qa_publication_exports ADD COLUMN IF NOT EXISTS target_base_ref TEXT"
    )
    op.execute(
        "ALTER TABLE public.qa_publication_exports ADD COLUMN IF NOT EXISTS target_branch TEXT"
    )
    op.execute(
        "ALTER TABLE public.healing_attempts ADD COLUMN IF NOT EXISTS publication_provenance TEXT"
    )
    op.execute("""
        CREATE OR REPLACE FUNCTION public.qa_export_target_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.target_repository IS DISTINCT FROM OLD.target_repository
               OR NEW.target_base_ref IS DISTINCT FROM OLD.target_base_ref
               OR NEW.target_branch IS DISTINCT FROM OLD.target_branch THEN
                RAISE EXCEPTION 'retained QA export target is immutable'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute("DROP TRIGGER IF EXISTS qa_export_target_immutable ON public.qa_publication_exports")
    op.execute("""
        CREATE TRIGGER qa_export_target_immutable BEFORE UPDATE ON public.qa_publication_exports
        FOR EACH ROW EXECUTE FUNCTION public.qa_export_target_immutable()
    """)


def downgrade() -> None:
    # Shared durable evidence must not disappear on a schema-local downgrade.
    raise RuntimeError("core_382 retains durable QA publication bindings")
