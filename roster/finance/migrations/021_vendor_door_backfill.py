"""Backfill the vendor-door registry from subscription cancellation links (bu-jv1moa.40 D5).

Revision ID: finance_021
Revises: finance_020

Each subscription link becomes one row in ``public.vendor_doors``, by stored provenance only:

* ``cancellation_url_source = 'owner'`` and a rule-clean URL: a ``confirmed`` ``cancel`` door with
  origin ``legacy_owner_set``. finance_017 recorded ``owner`` only for an owner-attested session,
  never for a routed email, so this is the one place a door is confirmed without the dashboard.
* ``owner`` but the URL breaks a door rule: an inert ``proposed`` row, origin ``legacy_owner_set``,
  with the failing rule in ``lineage.rule_problem``.
* anything else (``extracted``, unknown): an inert ``proposed`` row, origin ``extracted``. Never
  confirmed here. A link that is not https, or whose host cannot be computed, has no row to
  record and is skipped.

Owner provenance is never reconstructed from old text or metadata. The migration is idempotent
(a subscription's link is skipped once any row names its ``subscription_id`` in lineage), writes
no subscription column and deletes nothing. When ``public.vendor_doors`` or the owner entity is
absent it does nothing. It inserts directly as the migration owner: no runtime role can write
the table, and the cap rules are for live proposers, not for a one-off backfill.
"""

from alembic import op

revision = "finance_021"
down_revision = "finance_020"
branch_labels = None
depends_on = None

_BACKFILL = r"""
DO $$
DECLARE
    owner_id uuid;
BEGIN
    IF to_regclass('public.vendor_doors') IS NULL OR to_regclass('subscriptions') IS NULL THEN
        RETURN;
    END IF;
    SELECT e.id INTO owner_id FROM public.entities AS e
     WHERE 'owner' = ANY (e.roles) ORDER BY e.created_at, e.id LIMIT 1;
    IF owner_id IS NULL THEN
        RETURN;
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended('vendor_doors:' || owner_id::text, 0));

    INSERT INTO public.vendor_doors (
        owner_entity_id, kind, host, template, slot_types, status, origin, tier,
        lineage, expires_at, confirmed_at)
    SELECT owner_id, 'cancel', c.host, c.template, '{}'::jsonb,
           CASE WHEN c.is_owner AND c.problem IS NULL THEN 'confirmed' ELSE 'proposed' END,
           CASE WHEN c.is_owner THEN 'legacy_owner_set' ELSE 'extracted' END,
           CASE WHEN c.is_owner THEN 'exempt' ELSE 'extracted' END,
           jsonb_build_object('subscription_id', c.id::text, 'column', 'cancellation_url',
                              'backfill', 'finance_021')
               || CASE WHEN c.problem IS NULL THEN '{}'::jsonb
                       ELSE jsonb_build_object('rule_problem', c.problem) END,
           CASE WHEN c.is_owner AND c.problem IS NULL THEN NULL
                ELSE clock_timestamp() + interval '30 days' END,
           CASE WHEN c.is_owner AND c.problem IS NULL THEN clock_timestamp() END
      FROM (
          SELECT s.id, t.template, t.host,
                 s.cancellation_url_source = 'owner' AS is_owner,
                 public.vendor_door_template_problem(t.template, '{}'::jsonb) AS problem
            FROM subscriptions AS s
            CROSS JOIN LATERAL (
                SELECT public.vendor_door_normalize(
                           regexp_replace(btrim(s.cancellation_url), '[?#].*$', '')) AS template
            ) AS n
            CROSS JOIN LATERAL (
                SELECT n.template AS template,
                       public.registrable_domain(split_part(substr(n.template, 9), '/', 1)) AS host
            ) AS t
           WHERE s.cancellation_url IS NOT NULL AND btrim(s.cancellation_url) <> ''
             AND n.template LIKE 'https://%'
      ) AS c
     WHERE c.host ~ '^[a-z0-9.-]{3,253}$' AND char_length(c.template) <= 300
       AND NOT EXISTS (
           SELECT 1 FROM public.vendor_doors AS d
            WHERE d.lineage ->> 'subscription_id' = c.id::text
              AND d.lineage ->> 'column' = 'cancellation_url')
    ON CONFLICT (owner_entity_id, kind, template) WHERE status IN ('proposed', 'confirmed')
    DO NOTHING;
END $$
"""


def upgrade() -> None:
    op.execute(_BACKFILL)


def downgrade() -> None:
    # Rows are registry records the owner may have confirmed or rejected since; nothing is
    # removed. A downgrade leaves them in place, and the subscription columns were never touched.
    pass
