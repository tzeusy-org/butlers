"""Bind legacy transaction enrichment to ledger rows without changing facts.

Revision ID: finance_023
Revises: finance_022

The old read joined on time/merchant/currency/absolute amount. Account provenance
narrows that match; a fact that still identifies several ledger rows refuses the
cutover rather than copying one account's enrichment to another. Malformed and
unmatched facts remain memory history. No historical fact is updated or deleted.
"""

from alembic import op

revision = "finance_023"
down_revision = "finance_022"
branch_labels = None
depends_on = None


def _projection(*, ledger: bool) -> None:
    """Keep the public scalar projection and its strict installer on the same source."""
    old = """COALESCE(NEW.metadata->>'normalized_merchant', NEW.merchant);
    NEW.display_category := COALESCE(NEW.metadata->>'inferred_category', NEW.category)"""
    new = """COALESCE(NEW.normalized_merchant, NEW.merchant);
    NEW.display_category := COALESCE(NEW.inferred_category, NEW.category)"""
    before, after = (old, new) if ledger else (new, old)
    op.execute(f"""
        DO $projection$
        DECLARE
            source_schema text := current_schema();
            signature text;
            definition text;
        BEGIN
            -- Update both the trigger and the installer's expected body. Changing
            -- just the trigger would make the memory bootstrap callback reject it.
            FOREACH signature IN ARRAY ARRAY[
                format('%I.mcp_finance_overlay()', source_schema),
                format('%I.mcp_install_finance_projection(boolean)', source_schema)
            ] LOOP
                SELECT pg_get_functiondef(to_regprocedure(signature)) INTO definition;
                IF definition IS NULL THEN
                    RAISE EXCEPTION 'finance overlay projection prerequisite missing';
                END IF;
                IF position($before${before}$before$ IN definition) > 0 THEN
                    EXECUTE replace(definition, $before${before}$before$, $after${after}$after$);
                ELSIF position($after${after}$after$ IN definition) = 0 THEN
                    RAISE EXCEPTION 'finance overlay projection definition drift';
                END IF;
            END LOOP;
        END
        $projection$;
    """)


def upgrade() -> None:
    op.execute("""
        ALTER TABLE transactions
            ADD COLUMN IF NOT EXISTS inferred_category TEXT,
            ADD COLUMN IF NOT EXISTS legacy_overlay_fact_id UUID,
            ADD COLUMN IF NOT EXISTS taint_goal_id UUID,
            ADD COLUMN IF NOT EXISTS taint_invocation_id UUID;
    """)
    _projection(ledger=True)
    op.execute("""
        DO $cutover$
        BEGIN
            LOCK TABLE transactions IN SHARE ROW EXCLUSIVE MODE;
            IF to_regclass('facts') IS NOT NULL THEN
                LOCK TABLE facts IN SHARE ROW EXCLUSIVE MODE;
                CREATE TEMP TABLE finance_overlay_candidates ON COMMIT DROP AS
                SELECT t.id AS transaction_id, f.id AS fact_id,
                       f.metadata AS overlay_metadata, f.created_at,
                       (to_jsonb(f)->>'taint_goal_id')::uuid AS taint_goal_id,
                       (to_jsonb(f)->>'taint_invocation_id')::uuid AS taint_invocation_id,
                       (NULLIF(f.metadata->>'account_id', '') IS NULL
                        OR lower(f.metadata->>'account_id') = t.account_id::text) IS TRUE
                           AS account_matches
                FROM transactions t
                JOIN facts f ON f.valid_at = t.posted_at
                    AND f.metadata->>'merchant' = t.merchant
                    AND upper(f.metadata->>'currency') = upper(t.currency)
                    AND CASE
                        WHEN pg_input_is_valid(f.metadata->>'amount', 'numeric')
                        THEN (f.metadata->>'amount')::numeric = abs(t.amount)
                        ELSE false
                    END
                WHERE f.scope = 'finance' AND f.validity = 'active'
                    AND f.predicate IN ('transaction_debit', 'transaction_credit')
                    AND jsonb_typeof(f.metadata) = 'object';

                IF EXISTS (
                    SELECT fact_id FROM finance_overlay_candidates
                    GROUP BY fact_id HAVING NOT bool_or(account_matches)
                ) THEN
                    RAISE EXCEPTION 'finance_overlay_account_conflict: legacy account provenance '
                        'disagrees with matching ledger rows; resolve provenance before retry';
                END IF;
                DELETE FROM finance_overlay_candidates WHERE NOT account_matches;

                IF EXISTS (
                    SELECT fact_id FROM finance_overlay_candidates
                    GROUP BY fact_id HAVING count(*) > 1
                ) THEN
                    RAISE EXCEPTION 'finance_overlay_ambiguous: legacy fact matches multiple '
                        'transactions; resolve account or transaction provenance before retry';
                END IF;
                IF EXISTS (
                    SELECT 1 FROM transactions t JOIN finance_overlay_candidates c
                        ON c.transaction_id = t.id
                    WHERE jsonb_typeof(t.metadata) <> 'object'
                ) THEN
                    RAISE EXCEPTION 'finance_overlay_metadata_invalid: ledger metadata is not '
                        'an object; preserve and resolve the stored shape before retry';
                END IF;

                -- Latest means the old created_at ordering; UUID breaks timestamp ties
                -- deterministically. The marker makes an explicit installer replay safe.
                WITH latest AS (
                    SELECT DISTINCT ON (transaction_id) *
                    FROM finance_overlay_candidates
                    ORDER BY transaction_id, created_at DESC, fact_id DESC
                )
                UPDATE transactions t SET
                    normalized_merchant = COALESCE(
                        NULLIF(l.overlay_metadata->>'normalized_merchant', ''),
                        NULLIF(t.metadata->>'normalized_merchant', ''), t.normalized_merchant),
                    inferred_category = COALESCE(
                        NULLIF(l.overlay_metadata->>'inferred_category', ''),
                        NULLIF(t.metadata->>'inferred_category', ''), t.inferred_category),
                    metadata = t.metadata || l.overlay_metadata,
                    legacy_overlay_fact_id = l.fact_id,
                    taint_goal_id = COALESCE(l.taint_goal_id, t.taint_goal_id),
                    taint_invocation_id = COALESCE(l.taint_invocation_id, t.taint_invocation_id)
                FROM latest l
                WHERE t.id = l.transaction_id AND t.legacy_overlay_fact_id IS NULL;
                DROP TABLE finance_overlay_candidates;
            END IF;

            -- Some imports already stored enrichment inline, without any fact.
            UPDATE transactions SET
                normalized_merchant = COALESCE(normalized_merchant,
                    NULLIF(metadata->>'normalized_merchant', '')),
                inferred_category = COALESCE(inferred_category,
                    NULLIF(metadata->>'inferred_category', ''))
            WHERE normalized_merchant IS NULL OR inferred_category IS NULL;
            -- Refresh rows without a historical/inline overlay as well.
            UPDATE transactions SET display_merchant = COALESCE(normalized_merchant, merchant),
                display_category = COALESCE(inferred_category, category);
        END
        $cutover$;
    """)


def downgrade() -> None:
    op.execute("""
        DO $rollback$
        BEGIN
            LOCK TABLE transactions IN SHARE ROW EXCLUSIVE MODE;
            IF EXISTS (SELECT 1 FROM transactions
                       WHERE inferred_category IS NOT NULL OR legacy_overlay_fact_id IS NOT NULL
                           OR taint_goal_id IS NOT NULL OR taint_invocation_id IS NOT NULL)
            THEN
                RAISE EXCEPTION 'finance_overlay_downgrade_refused: retain transaction enrichment '
                    'and its legacy provenance; roll back application code without dropping data';
            END IF;
        END
        $rollback$;
    """)
    _projection(ledger=False)
    op.execute("""
        ALTER TABLE transactions DROP COLUMN IF EXISTS inferred_category,
                                 DROP COLUMN IF EXISTS legacy_overlay_fact_id,
                                 DROP COLUMN IF EXISTS taint_goal_id,
                                 DROP COLUMN IF EXISTS taint_invocation_id;
    """)
