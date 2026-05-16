"""complexity_tier_rename: rename complexity tiers to canonical six + add last_verified columns.

Revision ID: core_093
Revises: core_092
Create Date: 2026-05-16 00:00:00.000000

Renames the six legacy complexity_tier values to the canonical six:
  extra_high   → reasoning
  high         → reasoning
  medium       → workhorse
  trivial      → cheap
  discretion   → specialty
  self_healing → specialty

Also adds three verification columns to public.model_catalog:
  last_verified_at        TIMESTAMPTZ
  last_verified_latency_ms INT
  last_verified_ok        BOOL

Affects:
  - public.model_catalog.complexity_tier (CHECK constraint + data rows)
  - public.butler_model_overrides.complexity_tier (CHECK constraint + data rows)
  - public.model_round_robin_counters.complexity_tier (CHECK constraint + data rows)
"""

from __future__ import annotations

from alembic import op

revision = "core_093"
down_revision = "core_092"
branch_labels = None
depends_on = None

# New canonical six values.
_NEW_TIERS = ("reasoning", "workhorse", "cheap", "specialty", "local", "legacy")
_NEW_CHECK = "('reasoning', 'workhorse', 'cheap', 'specialty', 'local', 'legacy')"

# Old six values (pre-rename).
_OLD_TIERS = ("trivial", "medium", "high", "extra_high", "discretion", "self_healing")
_OLD_CHECK = "('trivial', 'medium', 'high', 'extra_high', 'discretion', 'self_healing')"


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Add last_verified_* columns to public.model_catalog
    # ------------------------------------------------------------------
    op.execute("""
        ALTER TABLE public.model_catalog
        ADD COLUMN IF NOT EXISTS last_verified_at        TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS last_verified_latency_ms INT,
        ADD COLUMN IF NOT EXISTS last_verified_ok        BOOL
    """)

    # ------------------------------------------------------------------
    # 2. Remap complexity_tier values in model_catalog
    # ------------------------------------------------------------------
    op.execute("""
        UPDATE public.model_catalog
        SET complexity_tier = CASE complexity_tier
            WHEN 'extra_high'   THEN 'reasoning'
            WHEN 'high'         THEN 'reasoning'
            WHEN 'medium'       THEN 'workhorse'
            WHEN 'trivial'      THEN 'cheap'
            WHEN 'discretion'   THEN 'specialty'
            WHEN 'self_healing' THEN 'specialty'
            ELSE complexity_tier
        END
        WHERE complexity_tier IN ('extra_high', 'high', 'medium', 'trivial', 'discretion', 'self_healing')
    """)

    # 2a. Drop old CHECK constraint and add new one on model_catalog
    op.execute("""
        ALTER TABLE public.model_catalog
        DROP CONSTRAINT IF EXISTS chk_model_catalog_complexity_tier
    """)
    op.execute(f"""
        ALTER TABLE public.model_catalog
        ADD CONSTRAINT chk_model_catalog_complexity_tier
            CHECK (complexity_tier IN {_NEW_CHECK})
    """)

    # ------------------------------------------------------------------
    # 3. Remap complexity_tier values in butler_model_overrides
    # ------------------------------------------------------------------
    op.execute("""
        UPDATE public.butler_model_overrides
        SET complexity_tier = CASE complexity_tier
            WHEN 'extra_high'   THEN 'reasoning'
            WHEN 'high'         THEN 'reasoning'
            WHEN 'medium'       THEN 'workhorse'
            WHEN 'trivial'      THEN 'cheap'
            WHEN 'discretion'   THEN 'specialty'
            WHEN 'self_healing' THEN 'specialty'
            ELSE complexity_tier
        END
        WHERE complexity_tier IN ('extra_high', 'high', 'medium', 'trivial', 'discretion', 'self_healing')
    """)

    # 3a. Drop old CHECK constraint and add new one on butler_model_overrides
    op.execute("""
        ALTER TABLE public.butler_model_overrides
        DROP CONSTRAINT IF EXISTS chk_butler_model_overrides_complexity_tier
    """)
    op.execute(f"""
        ALTER TABLE public.butler_model_overrides
        ADD CONSTRAINT chk_butler_model_overrides_complexity_tier
            CHECK (complexity_tier IS NULL OR complexity_tier IN {_NEW_CHECK})
    """)

    # ------------------------------------------------------------------
    # 4. Remap complexity_tier values in model_round_robin_counters
    #    (old counters become best-effort; merge by summing counts for
    #    high+extra_high→reasoning, discretion+self_healing→specialty)
    # ------------------------------------------------------------------
    # Merge rows that map to the same new tier before renaming to avoid PK conflicts.
    op.execute("""
        INSERT INTO public.model_round_robin_counters
            (butler_name, complexity_tier, counter, updated_at)
        SELECT
            butler_name,
            CASE complexity_tier
                WHEN 'extra_high'   THEN 'reasoning'
                WHEN 'high'         THEN 'reasoning'
                WHEN 'medium'       THEN 'workhorse'
                WHEN 'trivial'      THEN 'cheap'
                WHEN 'discretion'   THEN 'specialty'
                WHEN 'self_healing' THEN 'specialty'
            END AS new_tier,
            SUM(counter),
            MAX(updated_at)
        FROM public.model_round_robin_counters
        WHERE complexity_tier IN ('extra_high', 'high', 'medium', 'trivial', 'discretion', 'self_healing')
        GROUP BY butler_name, new_tier
        ON CONFLICT (butler_name, complexity_tier) DO UPDATE
            SET counter    = public.model_round_robin_counters.counter + EXCLUDED.counter,
                updated_at = GREATEST(public.model_round_robin_counters.updated_at, EXCLUDED.updated_at)
    """)

    # Remove old-vocabulary rows (now superseded by the merged new rows)
    op.execute("""
        DELETE FROM public.model_round_robin_counters
        WHERE complexity_tier IN ('extra_high', 'high', 'medium', 'trivial', 'discretion', 'self_healing')
    """)

    # 4a. Drop old CHECK constraint and add new one on model_round_robin_counters
    op.execute("""
        ALTER TABLE public.model_round_robin_counters
        DROP CONSTRAINT IF EXISTS chk_rr_complexity_tier
    """)
    op.execute(f"""
        ALTER TABLE public.model_round_robin_counters
        ADD CONSTRAINT chk_rr_complexity_tier
            CHECK (complexity_tier IN {_NEW_CHECK})
    """)


def downgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Reverse remap model_catalog rows (reasoning→high, workhorse→medium,
    #    cheap→trivial; specialty has two sources — pick 'discretion' as canonical)
    # ------------------------------------------------------------------
    op.execute("""
        ALTER TABLE public.model_catalog
        DROP CONSTRAINT IF EXISTS chk_model_catalog_complexity_tier
    """)
    op.execute("""
        UPDATE public.model_catalog
        SET complexity_tier = CASE complexity_tier
            WHEN 'reasoning'  THEN 'high'
            WHEN 'workhorse'  THEN 'medium'
            WHEN 'cheap'      THEN 'trivial'
            WHEN 'specialty'  THEN 'discretion'
            WHEN 'local'      THEN 'trivial'
            WHEN 'legacy'     THEN 'trivial'
            ELSE complexity_tier
        END
        WHERE complexity_tier IN ('reasoning', 'workhorse', 'cheap', 'specialty', 'local', 'legacy')
    """)
    op.execute(f"""
        ALTER TABLE public.model_catalog
        ADD CONSTRAINT chk_model_catalog_complexity_tier
            CHECK (complexity_tier IN {_OLD_CHECK})
    """)

    # ------------------------------------------------------------------
    # 2. Reverse remap butler_model_overrides
    # ------------------------------------------------------------------
    op.execute("""
        ALTER TABLE public.butler_model_overrides
        DROP CONSTRAINT IF EXISTS chk_butler_model_overrides_complexity_tier
    """)
    op.execute("""
        UPDATE public.butler_model_overrides
        SET complexity_tier = CASE complexity_tier
            WHEN 'reasoning'  THEN 'high'
            WHEN 'workhorse'  THEN 'medium'
            WHEN 'cheap'      THEN 'trivial'
            WHEN 'specialty'  THEN 'discretion'
            WHEN 'local'      THEN 'trivial'
            WHEN 'legacy'     THEN 'trivial'
            ELSE complexity_tier
        END
        WHERE complexity_tier IN ('reasoning', 'workhorse', 'cheap', 'specialty', 'local', 'legacy')
    """)
    op.execute(f"""
        ALTER TABLE public.butler_model_overrides
        ADD CONSTRAINT chk_butler_model_overrides_complexity_tier
            CHECK (complexity_tier IS NULL OR complexity_tier IN {_OLD_CHECK})
    """)

    # ------------------------------------------------------------------
    # 3. Reverse remap model_round_robin_counters
    # ------------------------------------------------------------------
    op.execute("""
        ALTER TABLE public.model_round_robin_counters
        DROP CONSTRAINT IF EXISTS chk_rr_complexity_tier
    """)
    # Insert reversed rows; use ON CONFLICT to handle any pre-existing old rows.
    op.execute("""
        INSERT INTO public.model_round_robin_counters
            (butler_name, complexity_tier, counter, updated_at)
        SELECT
            butler_name,
            CASE complexity_tier
                WHEN 'reasoning'  THEN 'high'
                WHEN 'workhorse'  THEN 'medium'
                WHEN 'cheap'      THEN 'trivial'
                WHEN 'specialty'  THEN 'discretion'
                WHEN 'local'      THEN 'trivial'
                WHEN 'legacy'     THEN 'trivial'
            END AS old_tier,
            SUM(counter),
            MAX(updated_at)
        FROM public.model_round_robin_counters
        WHERE complexity_tier IN ('reasoning', 'workhorse', 'cheap', 'specialty', 'local', 'legacy')
        GROUP BY butler_name, old_tier
        ON CONFLICT (butler_name, complexity_tier) DO UPDATE
            SET counter    = public.model_round_robin_counters.counter + EXCLUDED.counter,
                updated_at = GREATEST(public.model_round_robin_counters.updated_at, EXCLUDED.updated_at)
    """)
    op.execute("""
        DELETE FROM public.model_round_robin_counters
        WHERE complexity_tier IN ('reasoning', 'workhorse', 'cheap', 'specialty', 'local', 'legacy')
    """)
    op.execute(f"""
        ALTER TABLE public.model_round_robin_counters
        ADD CONSTRAINT chk_rr_complexity_tier
            CHECK (complexity_tier IN {_OLD_CHECK})
    """)

    # ------------------------------------------------------------------
    # 4. Remove last_verified_* columns from model_catalog
    # ------------------------------------------------------------------
    op.execute("""
        ALTER TABLE public.model_catalog
        DROP COLUMN IF EXISTS last_verified_at,
        DROP COLUMN IF EXISTS last_verified_latency_ms,
        DROP COLUMN IF EXISTS last_verified_ok
    """)
