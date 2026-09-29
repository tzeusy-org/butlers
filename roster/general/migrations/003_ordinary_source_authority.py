"""ordinary_source_authority: passive ordinary classification, vocabulary, source versions

Revision ID: gen_003
Revises: gen_002
Create Date: 2026-09-30 00:00:00.000000

bu-2jtfw.9.3 (RFC 0037 "Passive classification is not custody enrollment";
REQ-general-capture-003/-005).  One cohesive General-chain expand migration:

- ``collections.custody_private BOOLEAN NOT NULL DEFAULT false`` -- the passive
  classification every generic reader/writer filters on.  An existing
  compatible column and every existing ``true`` value are preserved; nothing
  here ever sets it.  ``collections.eligibility_generation`` advances whenever
  the classification changes, and a classified collection can never be reset
  to ordinary by an UPDATE.
- ``collections_ordinary_name_key``: ordinary-only partial name uniqueness,
  added *beside* any legacy global ``UNIQUE (name)`` so deployed
  ``ON CONFLICT (name)`` writers keep working.  General never drops the global
  constraint; a separately released custody rollout owns that cutover.  An
  already-cut-over schema (compatible partial index, no global constraint) is
  preserved as-is -- global uniqueness is not reintroduced.
- ``collection_vocabulary`` / ``collection_vocabulary_keys``: explicit ordinary
  declarations with a nonblank shape, canonical and alias spellings, and a
  DB-enforced normalized-key uniqueness that only ordinary parents take part
  in (the key rows mirror their parent's classification by trigger, so a
  private parent's spellings leave the ordinary namespace atomically).
- ``source_versions``: immutable General-local item source history, bound to
  item/parent/version/digest/eligibility generation.  UPDATE, DELETE and
  TRUNCATE are refused.

A reserved ``possession_profile`` on an item whose parent is not
authoritatively classified private aborts the upgrade transactionally: no
flag is guessed, reset or backfilled, and nothing is auto-enrolled.

Downgrade refuses while any classified-private collection or globally
duplicated collection name exists; it never resets flags, renames, merges or
deletes rows to fit the old schema.  Otherwise it restores global name
uniqueness and drops the passive representation.  Vocabulary and source
versions are durable records: their tables are dropped only when empty and are
otherwise retained, inert, for a later re-upgrade to adopt.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "gen_003"
down_revision = "gen_002"
branch_labels = None
depends_on = None

_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended('butlers:gen_003:ordinary_source', 0))"

# Ordinary-only partial name uniqueness.  The predicate text is what
# pg_get_expr() renders for the index, so a compatible pre-existing index is
# recognised by the same spelling.
_ORDINARY_INDEX = "collections_ordinary_name_key"
_ORDINARY_PREDICATE = "(custody_private = false)"


def _schema() -> str:
    bind = op.get_bind()
    return bind.exec_driver_sql("SELECT current_schema()").scalar_one()


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def upgrade() -> None:
    op.execute(_LOCK)
    schema = _quote(_schema())

    # Refuse before any change: a reserved custody profile needs an
    # authoritative private parent, and there is none unless the column
    # already exists with that parent classified true.
    op.execute(
        """
        DO $$
        DECLARE
            v_has_flag BOOLEAN;
            v_ambiguous BOOLEAN;
        BEGIN
            SELECT EXISTS (
                SELECT 1 FROM pg_catalog.pg_attribute
                WHERE attrelid = 'collections'::regclass
                  AND attname = 'custody_private'
                  AND NOT attisdropped
            ) INTO v_has_flag;
            IF v_has_flag THEN
                EXECUTE $q$
                    SELECT EXISTS (
                        SELECT 1
                        FROM collection_items AS item
                        JOIN collections AS parent ON parent.id = item.collection_id
                        WHERE item.data ? 'possession_profile'
                          AND parent.custody_private IS NOT TRUE
                    )
                $q$ INTO v_ambiguous;
            ELSE
                SELECT EXISTS (
                    SELECT 1 FROM collection_items WHERE data ? 'possession_profile'
                ) INTO v_ambiguous;
            END IF;
            IF v_ambiguous THEN
                RAISE EXCEPTION
                    'gen_003 refused: a reserved possession_profile exists without an '
                    'authoritative private classification; no flag is guessed or backfilled'
                    USING ERRCODE = '55000';
            END IF;
        END
        $$;
        """
    )

    # Passive classification: add only if absent; an existing column must be
    # exactly compatible (boolean, NOT NULL, default false).
    op.execute(
        """
        ALTER TABLE collections
        ADD COLUMN IF NOT EXISTS custody_private BOOLEAN NOT NULL DEFAULT false
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_catalog.pg_attribute AS attribute
                LEFT JOIN pg_catalog.pg_attrdef AS def
                  ON def.adrelid = attribute.attrelid AND def.adnum = attribute.attnum
                WHERE attribute.attrelid = 'collections'::regclass
                  AND attribute.attname = 'custody_private'
                  AND attribute.atttypid = 'boolean'::regtype
                  AND attribute.attnotnull
                  AND pg_catalog.pg_get_expr(def.adbin, def.adrelid) = 'false'
            ) THEN
                RAISE EXCEPTION
                    'gen_003 refused: collections.custody_private exists but is not '
                    'BOOLEAN NOT NULL DEFAULT false'
                    USING ERRCODE = '55000';
            END IF;
        END
        $$;
        """
    )
    op.execute(
        """
        ALTER TABLE collections
        ADD COLUMN IF NOT EXISTS eligibility_generation BIGINT NOT NULL DEFAULT 0
        """
    )

    # Ordinary-only uniqueness beside (never instead of) the legacy global
    # constraint.  Reuse any compatible partial unique index already present.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_catalog.pg_index AS ix
                JOIN pg_catalog.pg_attribute AS attribute
                  ON attribute.attrelid = ix.indrelid AND attribute.attnum = ix.indkey[0]
                WHERE ix.indrelid = 'collections'::regclass
                  AND ix.indisunique
                  AND ix.indnkeyatts = 1
                  AND ix.indexprs IS NULL
                  AND attribute.attname = 'name'
                  AND pg_catalog.pg_get_expr(ix.indpred, ix.indrelid) = '{_ORDINARY_PREDICATE}'
            ) THEN
                CREATE UNIQUE INDEX {_ORDINARY_INDEX}
                ON collections (name) WHERE custody_private = false;
            END IF;
        END
        $$;
        """
    )

    # Makes the reserved-profile parent check in every generic predicate cheap.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_collection_items_reserved_profile
        ON collection_items (collection_id) WHERE data ? 'possession_profile'
        """
    )

    # Classification may only move ordinary -> private, and each change
    # advances the parent's eligibility generation.
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {schema}.collections_classification_guard()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, pg_temp
        AS $fn$
        BEGIN
            IF NEW.custody_private IS DISTINCT FROM OLD.custody_private THEN
                IF OLD.custody_private THEN
                    RAISE EXCEPTION 'a private collection classification cannot be cleared'
                        USING ERRCODE = '55000';
                END IF;
                NEW.eligibility_generation := OLD.eligibility_generation + 1;
            ELSIF NEW.eligibility_generation IS DISTINCT FROM OLD.eligibility_generation THEN
                RAISE EXCEPTION 'eligibility_generation only advances with classification'
                    USING ERRCODE = '55000';
            END IF;
            RETURN NEW;
        END;
        $fn$
        """
    )
    op.execute("DROP TRIGGER IF EXISTS collections_classification_guard ON collections")
    op.execute(
        f"""
        CREATE TRIGGER collections_classification_guard
        BEFORE UPDATE ON collections
        FOR EACH ROW EXECUTE FUNCTION {schema}.collections_classification_guard()
        """
    )

    # Explicit ordinary vocabulary.
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {schema}.collection_vocabulary_key(spelling TEXT)
        RETURNS TEXT
        LANGUAGE sql
        IMMUTABLE
        STRICT
        PARALLEL SAFE
        SET search_path = pg_catalog, pg_temp
        AS $fn$
            SELECT pg_catalog.btrim(
                pg_catalog.regexp_replace(
                    pg_catalog.regexp_replace(
                        pg_catalog.lower(spelling), '[[:space:]_-]+', '-', 'g'
                    ),
                    '[^[:alnum:]-]', '', 'g'
                ),
                '-'
            )
        $fn$
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS collection_vocabulary (
            collection_id UUID PRIMARY KEY REFERENCES collections(id) ON DELETE CASCADE,
            shape_description TEXT NOT NULL
                CHECK (btrim(shape_description) <> '' AND length(shape_description) <= 2000),
            declared_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS collection_vocabulary_keys (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            collection_id UUID NOT NULL
                REFERENCES collection_vocabulary(collection_id) ON DELETE CASCADE,
            spelling TEXT NOT NULL
                CHECK (btrim(spelling) = spelling AND spelling <> '' AND length(spelling) <= 200),
            normalized_key TEXT GENERATED ALWAYS AS
                ({schema}.collection_vocabulary_key(spelling)) STORED,
            kind TEXT NOT NULL CHECK (kind IN ('canonical', 'alias')),
            custody_private BOOLEAN NOT NULL DEFAULT false,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT collection_vocabulary_keys_key_nonempty CHECK (normalized_key <> '')
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS collection_vocabulary_keys_ordinary_key
        ON collection_vocabulary_keys (normalized_key) WHERE NOT custody_private
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS collection_vocabulary_keys_one_canonical
        ON collection_vocabulary_keys (collection_id) WHERE kind = 'canonical'
        """
    )
    # Key rows carry their parent's classification: stamped on insert (under
    # the parent's row lock) and rewritten in the classifying transaction.
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {schema}.collection_vocabulary_keys_stamp()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, pg_temp
        AS $fn$
        BEGIN
            SELECT parent.custody_private INTO NEW.custody_private
            FROM {schema}.collections AS parent
            WHERE parent.id = NEW.collection_id
            FOR SHARE;
            IF NEW.custody_private IS NULL THEN
                RAISE EXCEPTION 'vocabulary key parent is unavailable' USING ERRCODE = '23503';
            END IF;
            RETURN NEW;
        END;
        $fn$
        """
    )
    op.execute(
        "DROP TRIGGER IF EXISTS collection_vocabulary_keys_stamp ON collection_vocabulary_keys"
    )
    op.execute(
        f"""
        CREATE TRIGGER collection_vocabulary_keys_stamp
        BEFORE INSERT OR UPDATE ON collection_vocabulary_keys
        FOR EACH ROW EXECUTE FUNCTION {schema}.collection_vocabulary_keys_stamp()
        """
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {schema}.collections_classification_mirror()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, pg_temp
        AS $fn$
        BEGIN
            UPDATE {schema}.collection_vocabulary_keys
            SET custody_private = NEW.custody_private
            WHERE collection_id = NEW.id;
            RETURN NULL;
        END;
        $fn$
        """
    )
    op.execute("DROP TRIGGER IF EXISTS collections_classification_mirror ON collections")
    op.execute(
        f"""
        CREATE TRIGGER collections_classification_mirror
        AFTER UPDATE OF custody_private ON collections
        FOR EACH ROW
        WHEN (NEW.custody_private IS DISTINCT FROM OLD.custody_private)
        EXECUTE FUNCTION {schema}.collections_classification_mirror()
        """
    )
    # A retained key table re-adopted after a downgrade may have missed
    # classification changes; resynchronise it before any reader trusts it.
    op.execute(
        """
        UPDATE collection_vocabulary_keys AS key_row
        SET custody_private = parent.custody_private
        FROM collections AS parent
        WHERE parent.id = key_row.collection_id
          AND key_row.custody_private IS DISTINCT FROM parent.custody_private
        """
    )

    # Immutable General-local source versions.  No FK to the item: history
    # outlives the row it describes (delete writes a tombstone version).
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS source_versions (
            item_id UUID NOT NULL,
            version BIGINT NOT NULL CHECK (version > 0),
            collection_id UUID NOT NULL,
            eligibility_generation BIGINT NOT NULL CHECK (eligibility_generation >= 0),
            operation TEXT NOT NULL CHECK (operation IN ('create', 'update', 'delete')),
            digest TEXT NOT NULL CHECK (digest ~ '^[0-9a-f]{64}$'),
            content JSONB,
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (item_id, version),
            CONSTRAINT source_versions_tombstone_has_no_content
                CHECK ((operation = 'delete') = (content IS NULL))
        )
        """
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {schema}.source_versions_immutable()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, pg_temp
        AS $fn$
        BEGIN
            RAISE EXCEPTION 'General source versions are immutable' USING ERRCODE = '55000';
        END;
        $fn$
        """
    )
    op.execute("DROP TRIGGER IF EXISTS source_versions_immutable ON source_versions")
    op.execute(
        f"""
        CREATE TRIGGER source_versions_immutable
        BEFORE UPDATE OR DELETE ON source_versions
        FOR EACH ROW EXECUTE FUNCTION {schema}.source_versions_immutable()
        """
    )
    op.execute("DROP TRIGGER IF EXISTS source_versions_no_truncate ON source_versions")
    op.execute(
        f"""
        CREATE TRIGGER source_versions_no_truncate
        BEFORE TRUNCATE ON source_versions
        FOR EACH STATEMENT EXECUTE FUNCTION {schema}.source_versions_immutable()
        """
    )
    # Re-adopted history keeps its generations reachable after a downgrade
    # dropped (and this upgrade re-added) the counter at 0.
    op.execute(
        """
        UPDATE collections AS parent
        SET eligibility_generation = history.generation
        FROM (
            SELECT collection_id, max(eligibility_generation) AS generation
            FROM source_versions
            GROUP BY collection_id
        ) AS history
        WHERE history.collection_id = parent.id
          AND parent.custody_private = false
          AND parent.eligibility_generation < history.generation
        """
    )


def downgrade() -> None:
    op.execute(_LOCK)
    schema = _quote(_schema())

    # Refuse rather than reopen private data or flatten duplicated names.
    op.execute(
        """
        DO $$
        DECLARE
            v_private BOOLEAN := false;
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_catalog.pg_attribute
                WHERE attrelid = 'collections'::regclass
                  AND attname = 'custody_private'
                  AND NOT attisdropped
            ) THEN
                EXECUTE 'SELECT EXISTS (SELECT 1 FROM collections WHERE custody_private)'
                    INTO v_private;
            END IF;
            IF v_private THEN
                RAISE EXCEPTION
                    'gen_003 downgrade refused: a private collection classification exists; '
                    'flags are never reset to fit the old schema'
                    USING ERRCODE = '55000';
            END IF;
            IF EXISTS (
                SELECT 1 FROM collections GROUP BY name HAVING count(*) > 1
            ) THEN
                RAISE EXCEPTION
                    'gen_003 downgrade refused: collection names are not globally unique; '
                    'rows are never renamed, merged or deleted to fit the old schema'
                    USING ERRCODE = '55000';
            END IF;
        END
        $$;
        """
    )

    # Restore global name uniqueness if a cut-over schema had dropped it.
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_catalog.pg_index AS ix
                JOIN pg_catalog.pg_attribute AS attribute
                  ON attribute.attrelid = ix.indrelid AND attribute.attnum = ix.indkey[0]
                WHERE ix.indrelid = 'collections'::regclass
                  AND ix.indisunique
                  AND ix.indnkeyatts = 1
                  AND ix.indexprs IS NULL
                  AND ix.indpred IS NULL
                  AND attribute.attname = 'name'
            ) THEN
                ALTER TABLE collections ADD CONSTRAINT collections_name_key UNIQUE (name);
            END IF;
        END
        $$;
        """
    )

    op.execute("DROP TRIGGER IF EXISTS collections_classification_mirror ON collections")
    op.execute(f"DROP FUNCTION IF EXISTS {schema}.collections_classification_mirror()")
    op.execute("DROP TRIGGER IF EXISTS collections_classification_guard ON collections")
    op.execute(f"DROP FUNCTION IF EXISTS {schema}.collections_classification_guard()")

    # Durable records: dropped only when empty, otherwise retained inert.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM collection_vocabulary)
               AND NOT EXISTS (SELECT 1 FROM collection_vocabulary_keys) THEN
                DROP TABLE collection_vocabulary_keys;
                DROP TABLE collection_vocabulary;
                DROP FUNCTION IF EXISTS {schema}.collection_vocabulary_keys_stamp();
                DROP FUNCTION IF EXISTS {schema}.collection_vocabulary_key(TEXT);
            ELSE
                -- The stamp trigger reads collections.custody_private, which
                -- is about to be dropped; the key rows keep their last value.
                DROP TRIGGER IF EXISTS collection_vocabulary_keys_stamp
                    ON collection_vocabulary_keys;
                DROP FUNCTION IF EXISTS {schema}.collection_vocabulary_keys_stamp();
            END IF;
            IF NOT EXISTS (SELECT 1 FROM source_versions) THEN
                DROP TABLE source_versions;
                DROP FUNCTION IF EXISTS {schema}.source_versions_immutable();
            END IF;
        END
        $$;
        """
    )

    op.execute("DROP INDEX IF EXISTS idx_collection_items_reserved_profile")
    op.execute(f"DROP INDEX IF EXISTS {_ORDINARY_INDEX}")
    op.execute("ALTER TABLE collections DROP COLUMN IF EXISTS eligibility_generation")
    op.execute("ALTER TABLE collections DROP COLUMN IF EXISTS custody_private")
