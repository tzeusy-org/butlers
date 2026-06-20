"""rel_031 — re-home contact-only columns onto public.entities (Phase 7.4a, bu-v4c39).

Revision ID: rel_031
Revises: rel_030
Create Date: 2026-06-20 00:00:00.000000

Context (Phase 7.4a — contacts retirement foundation, bu-oluyt)
--------------------------------------------------------------
``public.contacts`` stores several columns that logically belong on
``public.entities``:

  - ``stay_in_touch_days`` — used in SQL WHERE/ORDER by dunbar.py:445,
    briefing.py:703, resolve.py:481, relationship_jobs.py:464; written by
    stay_in_touch.py:24.  Must be a **real column** (NOT embedded in metadata)
    because it appears in ORDER BY and indexed WHERE clauses.

  - CRM profile fields: ``first_name``, ``last_name``, ``company``, ``job_title``,
    ``gender``, ``pronouns``, ``avatar_url`` — stored under
    ``entities.metadata['profile'].*`` to co-locate with the values already
    written by the Google Contacts backfill
    (``src/butlers/modules/contacts/backfill.py``).

Already homed (NOT re-done here): ``name`` → ``canonical_name``,
``nickname`` → ``aliases``, ``listed`` → ``entities.listed``,
``details``/``metadata`` → ``entities.metadata``.

What this migration does
------------------------
1. **Add column** ``public.entities.stay_in_touch_days INT`` (nullable; idempotent
   ``ADD COLUMN IF NOT EXISTS``).
2. **Snapshot** linked entity rows before any mutation for reversibility
   (``to_regclass``-guarded; only on first run).
3. **Backfill** ``stay_in_touch_days`` from ``public.contacts`` into the linked
   entity (additive: only fills where ``entities.stay_in_touch_days IS NULL``).
   When multiple contacts share one entity, the most-recently-updated contact
   wins (``updated_at DESC, id ASC`` tie-break).
4. **Backfill** CRM profile fields from ``public.contacts`` into
   ``entities.metadata['profile'].*`` (additive merge: existing keys in
   ``entities.metadata['profile']`` win — preserves any data already written by
   Google Contacts sync; contacts fills gaps, especially ``gender`` and
   ``pronouns`` which Google Contacts does not supply).
5. **Parity assertions** abort (RAISE EXCEPTION) if any contact with a linked
   entity is found to have data that did not reach its entity.
6. **Guard**: all data-touching statements are wrapped in a
   ``to_regclass('public.contacts') IS NOT NULL`` guard so this migration is a
   clean no-op on a schema where ``public.contacts`` has already been dropped.
7. **Reversible downgrade**: restores ``entities.metadata['profile']`` from the
   snapshot and drops ``stay_in_touch_days``.

``public.contacts`` is NOT dropped here (that is bu-y6o7q).

Schema qualification
--------------------
``public.entities`` and ``public.contacts`` are always fully qualified.
Snapshot tables are created in ``public`` with explicit schema prefix.
Relationship butler child tables are unqualified (search_path resolves to
``relationship`` in production, ``public`` in schema-less test runs — per
``_entity_resolve`` doctrine).
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "rel_031"
down_revision = "rel_030"
branch_labels = None
depends_on = None

# Name of the snapshot table created before mutation (for reversibility + parity).
_SNAPSHOT_TABLE = "public.entities_contact_profile_bak_rel_031"

# ---------------------------------------------------------------------------
# DDL — add stay_in_touch_days to public.entities (idempotent).
# ---------------------------------------------------------------------------
_ADD_COLUMN_SQL = """
ALTER TABLE public.entities
    ADD COLUMN IF NOT EXISTS stay_in_touch_days INT;
"""

# ---------------------------------------------------------------------------
# Snapshot + backfill + parity — single DO block (atomic within migration tx).
# ---------------------------------------------------------------------------
_BACKFILL_AND_PARITY_SQL = """
DO $$
DECLARE
    _src_sit_count    bigint;
    _dest_sit_count   bigint;
    _orphaned_avatars bigint;
BEGIN
    -- Guard: clean no-op when public.contacts is already gone (post-DROP or fresh
    -- DB provisioned without a contacts table).
    IF to_regclass('public.contacts') IS NULL THEN
        RAISE NOTICE 'rel_031: public.contacts absent — skipping backfill';
        RETURN;
    END IF;

    -- 1. Snapshot linked entity profile state for reversibility.
    --    Guard: only create on first run so a re-run preserves the original
    --    pre-backfill state.
    IF to_regclass('public.entities_contact_profile_bak_rel_031') IS NULL THEN
        CREATE TABLE public.entities_contact_profile_bak_rel_031 AS
        SELECT
            e.id                     AS entity_id,
            e.stay_in_touch_days,
            e.metadata -> 'profile'  AS profile_json
        FROM public.entities e
        WHERE e.id IN (
            SELECT entity_id FROM public.contacts WHERE entity_id IS NOT NULL
        );
    END IF;

    -- 2. Backfill stay_in_touch_days (additive: only fills where entity is NULL).
    --    When multiple contacts share one entity (edge case; rel_030 deduped most),
    --    the most-recently-updated contact wins.
    UPDATE public.entities e
    SET    stay_in_touch_days = src.stay_in_touch_days,
           updated_at         = now()
    FROM (
        SELECT DISTINCT ON (entity_id)
               entity_id,
               stay_in_touch_days
        FROM   public.contacts
        WHERE  entity_id        IS NOT NULL
          AND  stay_in_touch_days IS NOT NULL
        ORDER  BY entity_id, updated_at DESC, id ASC
    ) src
    WHERE  e.id                = src.entity_id
      AND  e.stay_in_touch_days IS NULL;

    -- 3. Backfill CRM profile fields into entities.metadata['profile'].
    --
    --    Shape mirrors the Google Contacts backfill
    --    (src/butlers/modules/contacts/backfill.py::_deep_set) so existing reads
    --    (e.g. metadata->'profile'->>'avatar_url') keep working unchanged.
    --
    --    Merge strategy:
    --       contacts_profile || existing_profile
    --    The RIGHT operand wins on key collision, so any key already present in
    --    entities.metadata['profile'] (e.g. set by a Google Contacts sync) is
    --    preserved.  contacts fills gaps — especially 'gender' and 'pronouns'
    --    which Google Contacts does not supply.
    --
    --    jsonb_strip_nulls removes NULL-valued keys from the contacts side so we
    --    never write {"first_name": null} into metadata.
    UPDATE public.entities e
    SET    metadata   = COALESCE(e.metadata, '{}'::jsonb)
                        || jsonb_build_object(
                               'profile',
                               jsonb_strip_nulls(jsonb_build_object(
                                   'first_name', src.first_name,
                                   'last_name',  src.last_name,
                                   'company',    src.company,
                                   'job_title',  src.job_title,
                                   'gender',     src.gender,
                                   'pronouns',   src.pronouns,
                                   'avatar_url', src.avatar_url
                               ))
                               || COALESCE(e.metadata -> 'profile', '{}'::jsonb)
                           ),
           updated_at = now()
    FROM (
        SELECT DISTINCT ON (entity_id)
               entity_id,
               first_name, last_name, company, job_title,
               gender, pronouns, avatar_url
        FROM   public.contacts
        WHERE  entity_id IS NOT NULL
          AND  (   first_name  IS NOT NULL
                OR last_name   IS NOT NULL
                OR company     IS NOT NULL
                OR job_title   IS NOT NULL
                OR gender      IS NOT NULL
                OR pronouns    IS NOT NULL
                OR avatar_url  IS NOT NULL)
        ORDER  BY entity_id, updated_at DESC, id ASC
    ) src
    WHERE  e.id = src.entity_id;

    -- 4. Parity assertion A: stay_in_touch_days coverage.
    --    Every distinct entity_id linked from a contact with a non-NULL
    --    stay_in_touch_days must now carry that value in public.entities.
    SELECT COUNT(DISTINCT entity_id)
      INTO _src_sit_count
      FROM public.contacts
     WHERE entity_id         IS NOT NULL
       AND stay_in_touch_days IS NOT NULL;

    SELECT COUNT(DISTINCT e.id)
      INTO _dest_sit_count
      FROM public.entities e
     WHERE e.stay_in_touch_days IS NOT NULL
       AND e.id IN (
           SELECT entity_id
             FROM public.contacts
            WHERE entity_id         IS NOT NULL
              AND stay_in_touch_days IS NOT NULL
       );

    IF _dest_sit_count < _src_sit_count THEN
        RAISE EXCEPTION
            'rel_031 parity failure (stay_in_touch_days): '
            '% contact-linked entity IDs carried stay_in_touch_days '
            'but only % now have it in public.entities',
            _src_sit_count, _dest_sit_count;
    END IF;

    -- 4. Parity assertion B: avatar_url coverage.
    --    Every contact that has entity_id IS NOT NULL AND avatar_url IS NOT NULL
    --    must result in a non-NULL entities.metadata->'profile'->>'avatar_url'.
    --    (Either the migration wrote it or Google Contacts had it already.)
    SELECT COUNT(*)
      INTO _orphaned_avatars
      FROM public.contacts c
      JOIN public.entities e ON e.id = c.entity_id
     WHERE c.entity_id  IS NOT NULL
       AND c.avatar_url IS NOT NULL
       AND (e.metadata -> 'profile' ->> 'avatar_url') IS NULL;

    IF _orphaned_avatars > 0 THEN
        RAISE EXCEPTION
            'rel_031 parity failure (avatar_url): '
            '% contacts with avatar_url have no '
            'entities.metadata[''profile''][''avatar_url''] after backfill',
            _orphaned_avatars;
    END IF;

END;
$$;
"""

# ---------------------------------------------------------------------------
# Downgrade — restore metadata['profile'] from snapshot; drop the column.
# ---------------------------------------------------------------------------
_DOWNGRADE_SQL = """
DO $$
BEGIN
    IF to_regclass('public.entities_contact_profile_bak_rel_031') IS NULL THEN
        RAISE NOTICE 'rel_031 downgrade: snapshot absent — skipping profile restore';
        RETURN;
    END IF;

    -- Restore entities.metadata['profile'] to the pre-upgrade state.
    UPDATE public.entities e
    SET    metadata   = CASE
               WHEN bak.profile_json IS NULL
                    -- Entity had no profile key before upgrade — remove it entirely.
                    THEN COALESCE(e.metadata, '{}'::jsonb) - 'profile'
               ELSE
                    COALESCE(e.metadata, '{}'::jsonb)
                    || jsonb_build_object('profile', bak.profile_json)
           END,
           updated_at = now()
    FROM public.entities_contact_profile_bak_rel_031 bak
    WHERE e.id = bak.entity_id;

    DROP TABLE public.entities_contact_profile_bak_rel_031;
END;
$$;
"""


def upgrade() -> None:
    op.execute(_ADD_COLUMN_SQL)
    op.execute(_BACKFILL_AND_PARITY_SQL)


def downgrade() -> None:
    op.execute(_DOWNGRADE_SQL)
    # Drop the new column (loses stay_in_touch_days data, but contacts remains
    # the source of truth until contacts is retired; re-upgrade restores it).
    op.execute("ALTER TABLE public.entities DROP COLUMN IF EXISTS stay_in_touch_days")
