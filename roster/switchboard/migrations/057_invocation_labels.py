"""Persist RFC 0038 S1 invocation labels on the goal ledger (bu-dhloi1.6).

Revision ID: sw_057
Revises: sw_056

Every goal, invocation, effect and inbox row carries a version 1 label:
``label_instr`` (I), ``label_data`` (D) and ``label_sens`` (C). The column
values are validated sets, never normalised; ``butlers.core.goal_labels``
holds the same grammar.

* A goal keeps its immutable ``admitted_label_*`` and a monotonic reporting
  union in ``label_*``. The union is audit and outcome evidence only; call
  policy reads the invocation. The admitted label is derived here from the
  server-held admission facts: D5's ``admitted_exposed`` (exposed input is of
  unknown provenance and sensitivity; unexposed input is owner data) joined
  with the causing effect's label for a caused admission.
* An invocation starts from its goal's admitted label, joined with its caller
  (a child) or with every earlier lead and recorded effect of the goal (a
  replacement or escalated lead). A clean sibling that is already running is
  not contaminated by the goal's union. This replaces sw_047's rule that an
  invocation starts with the goal's aggregate exposure (S1, first switch).
  Its label only grows, and ``exposure_cause`` (closed S1 enum) records why it
  first became exposed.
* An effect snapshots its invoking label when it is recorded, so park,
  approval, edit and retry carry the requesting label without narrowing.
* An inbox row carries the label of the input it delivers: the verified owner
  reply joined with the label of the effect it answers, plus whatever the
  admission writer adds for outside content in the reply itself. Its consumer
  joins it (cause ``inbox``).

``exposed`` stays and is derived: it is true exactly when I or D holds a
principal other than ``owner``/``owner_device``. A writer that sets only the
Boolean is joined with the unknown label (``{world}``, all six categories), so
no unlabelled path is authoritative.

Legacy rows keep at least their old restriction: an exposed row becomes the
unknown label with cause ``legacy_unknown``, an unexposed row keeps owner
provenance, and every legacy row assumes all six categories. Inbox rows take
their goal's exposure. The downgrade refuses while any goal exists.
"""

from alembic import op

revision = "sw_057"
down_revision = "sw_056"
branch_labels = None
depends_on = None

_ALL_SIX = "'{credentials,finance,health,location,relationship_notes,routine}'::text[]"
_PRINCIPAL = "^(owner|owner_device|world|(domain|external):[a-z0-9][a-z0-9_.-]{0,127})$"
_LABELLED = ("goals", "invocations", "effects", "goal_inbox")

_HELPERS = f"""
    CREATE OR REPLACE FUNCTION switchboard.goal_label_valid(
        v SMALLINT, i TEXT[], d TEXT[], c TEXT[])
    RETURNS boolean LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
        SELECT COALESCE(
            v = 1
            AND cardinality(i) > 0 AND cardinality(d) > 0
            AND array_position(i, NULL) IS NULL AND array_position(d, NULL) IS NULL
            AND array_position(c, NULL) IS NULL
            AND (SELECT count(DISTINCT x) = count(*) FROM unnest(i) x)
            AND (SELECT count(DISTINCT x) = count(*) FROM unnest(d) x)
            AND (SELECT count(DISTINCT x) = count(*) FROM unnest(c) x)
            AND (SELECT bool_and(x ~ '{_PRINCIPAL}') FROM unnest(i || d) x)
            AND c <@ {_ALL_SIX},
            false)
    $$;
    CREATE OR REPLACE FUNCTION switchboard.goal_label_exposed(i TEXT[], d TEXT[])
    RETURNS boolean LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
        SELECT EXISTS (
            SELECT 1 FROM unnest(COALESCE(i, '{{}}') || COALESCE(d, '{{}}')) x
            WHERE x NOT IN ('owner', 'owner_device'))
    $$;
    CREATE OR REPLACE FUNCTION switchboard.goal_label_union(a TEXT[], b TEXT[])
    RETURNS text[] LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
        SELECT COALESCE(array_agg(DISTINCT x COLLATE "C" ORDER BY x COLLATE "C"), '{{}}')
        FROM unnest(COALESCE(a, '{{}}') || COALESCE(b, '{{}}')) x
    $$;
"""

# sw_047's immutability, extended: labels only grow, an admitted label and an
# effect or inbox label are fixed once written, and exposure_cause is set once.
_IMMUTABLE = """
    CREATE OR REPLACE FUNCTION switchboard.goal_label_narrowed(
        nv SMALLINT, ni TEXT[], nd TEXT[], nc TEXT[],
        ov SMALLINT, oi TEXT[], od TEXT[], oc TEXT[])
    RETURNS boolean LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
        SELECT ov IS NOT NULL AND (nv IS DISTINCT FROM ov
            OR NOT COALESCE(ni @> oi AND nd @> od AND nc @> oc, false))
    $$;
    CREATE OR REPLACE FUNCTION switchboard.goal_ledger_immutable()
    RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
    BEGIN
        IF TG_TABLE_NAME='admission_decisions' THEN
            RAISE EXCEPTION 'Admission history is immutable';
        ELSIF TG_TABLE_NAME='goals' THEN
            -- thread_id may be bound once, after admission; never rebound.
            IF (to_jsonb(NEW) - ARRAY['status','exposed','thread_id','updated_at',
                    'label_version','label_instr','label_data','label_sens',
                    'admitted_label_version','admitted_label_instr','admitted_label_data',
                    'admitted_label_sens'])
               IS DISTINCT FROM
               (to_jsonb(OLD) - ARRAY['status','exposed','thread_id','updated_at',
                    'label_version','label_instr','label_data','label_sens',
                    'admitted_label_version','admitted_label_instr','admitted_label_data',
                    'admitted_label_sens'])
               OR (OLD.exposed AND NOT NEW.exposed)
               OR (OLD.thread_id IS NOT NULL
                   AND NEW.thread_id IS DISTINCT FROM OLD.thread_id)
               OR (OLD.admitted_label_version IS NOT NULL AND
                   (NEW.admitted_label_version, NEW.admitted_label_instr,
                    NEW.admitted_label_data, NEW.admitted_label_sens) IS DISTINCT FROM
                   (OLD.admitted_label_version, OLD.admitted_label_instr,
                    OLD.admitted_label_data, OLD.admitted_label_sens))
               OR switchboard.goal_label_narrowed(
                   NEW.label_version, NEW.label_instr, NEW.label_data, NEW.label_sens,
                   OLD.label_version, OLD.label_instr, OLD.label_data, OLD.label_sens) THEN
                RAISE EXCEPTION 'Goal authority and source association are immutable';
            END IF;
        ELSIF TG_TABLE_NAME='invocations' THEN
            IF (NEW.goal_id,NEW.parent_invocation_id,NEW.depth,NEW.role,NEW.lead_epoch,
                NEW.grant_snapshot,NEW.grant_digest,NEW.max_turns,NEW.max_tokens) IS DISTINCT FROM
               (OLD.goal_id,OLD.parent_invocation_id,OLD.depth,OLD.role,OLD.lead_epoch,
                OLD.grant_snapshot,OLD.grant_digest,OLD.max_turns,OLD.max_tokens)
               OR (OLD.exposed AND NOT NEW.exposed)
               OR (OLD.exposure_cause IS NOT NULL
                   AND NEW.exposure_cause IS DISTINCT FROM OLD.exposure_cause)
               OR switchboard.goal_label_narrowed(
                   NEW.label_version, NEW.label_instr, NEW.label_data, NEW.label_sens,
                   OLD.label_version, OLD.label_instr, OLD.label_data, OLD.label_sens) THEN
                RAISE EXCEPTION 'Invocation lineage, grant and exposure are immutable';
            END IF;
        ELSIF TG_TABLE_NAME='goal_inbox' THEN
            IF (to_jsonb(NEW) - ARRAY['consumed_at','label_version','label_instr',
                    'label_data','label_sens'])
               IS DISTINCT FROM
               (to_jsonb(OLD) - ARRAY['consumed_at','label_version','label_instr',
                    'label_data','label_sens'])
               OR (OLD.consumed_at IS NOT NULL
                   AND NEW.consumed_at IS DISTINCT FROM OLD.consumed_at)
               OR (OLD.label_version IS NOT NULL AND
                   (NEW.label_version, NEW.label_instr, NEW.label_data, NEW.label_sens)
                   IS DISTINCT FROM
                   (OLD.label_version, OLD.label_instr, OLD.label_data, OLD.label_sens)) THEN
                RAISE EXCEPTION 'Goal inbox delivery is immutable';
            END IF;
        ELSIF TG_TABLE_NAME='effects' THEN
            IF (NEW.goal_id,NEW.invocation_id,NEW.capability,NEW.args_digest,
                NEW.delivery_ref,NEW.recipient_entity,NEW.recipient_channel) IS DISTINCT FROM
               (OLD.goal_id,OLD.invocation_id,OLD.capability,OLD.args_digest,
                OLD.delivery_ref,OLD.recipient_entity,OLD.recipient_channel)
               OR (OLD.label_version IS NOT NULL AND
                   (NEW.label_version, NEW.label_instr, NEW.label_data, NEW.label_sens)
                   IS DISTINCT FROM
                   (OLD.label_version, OLD.label_instr, OLD.label_data, OLD.label_sens)) THEN
                RAISE EXCEPTION 'Effect lineage and recipient association are immutable';
            END IF;
        END IF;
        RETURN NEW;
    END $$;
"""

_SW047_IMMUTABLE = """
    CREATE OR REPLACE FUNCTION switchboard.goal_ledger_immutable()
    RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
    BEGIN
        IF TG_TABLE_NAME='admission_decisions' THEN
            RAISE EXCEPTION 'Admission history is immutable';
        ELSIF TG_TABLE_NAME='goals' THEN
            -- thread_id may be bound once, after admission; never rebound.
            IF (to_jsonb(NEW) - ARRAY['status','exposed','thread_id','updated_at'])
               IS DISTINCT FROM
               (to_jsonb(OLD) - ARRAY['status','exposed','thread_id','updated_at'])
               OR (OLD.exposed AND NOT NEW.exposed)
               OR (OLD.thread_id IS NOT NULL
                   AND NEW.thread_id IS DISTINCT FROM OLD.thread_id) THEN
                RAISE EXCEPTION 'Goal authority and source association are immutable';
            END IF;
        ELSIF TG_TABLE_NAME='invocations' THEN
            IF (NEW.goal_id,NEW.parent_invocation_id,NEW.depth,NEW.role,NEW.lead_epoch,
                NEW.grant_snapshot,NEW.grant_digest,NEW.max_turns,NEW.max_tokens) IS DISTINCT FROM
               (OLD.goal_id,OLD.parent_invocation_id,OLD.depth,OLD.role,OLD.lead_epoch,
                OLD.grant_snapshot,OLD.grant_digest,OLD.max_turns,OLD.max_tokens)
               OR (OLD.exposed AND NOT NEW.exposed) THEN
                RAISE EXCEPTION 'Invocation lineage, grant and exposure are immutable';
            END IF;
        ELSIF TG_TABLE_NAME='goal_inbox' THEN
            IF (to_jsonb(NEW) - 'consumed_at') IS DISTINCT FROM (to_jsonb(OLD) - 'consumed_at')
               OR (OLD.consumed_at IS NOT NULL
                   AND NEW.consumed_at IS DISTINCT FROM OLD.consumed_at) THEN
                RAISE EXCEPTION 'Goal inbox delivery is immutable';
            END IF;
        ELSIF TG_TABLE_NAME='effects' THEN
            IF (NEW.goal_id,NEW.invocation_id,NEW.capability,NEW.args_digest,
                NEW.delivery_ref,NEW.recipient_entity,NEW.recipient_channel) IS DISTINCT FROM
               (OLD.goal_id,OLD.invocation_id,OLD.capability,OLD.args_digest,
                OLD.delivery_ref,OLD.recipient_entity,OLD.recipient_channel) THEN
                RAISE EXCEPTION 'Effect lineage and recipient association are immutable';
            END IF;
        END IF;
        RETURN NEW;
    END $$;
"""

_SW047_EXPOSURE = """
    CREATE OR REPLACE FUNCTION switchboard.goal_invocation_exposure()
    RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
    BEGIN
        IF TG_OP='INSERT' AND NOT NEW.exposed AND EXISTS (
            SELECT 1 FROM switchboard.goals WHERE id=NEW.goal_id AND exposed) THEN
            RAISE EXCEPTION 'An invocation starts with its goal''s exposure';
        END IF;
        IF NEW.exposed THEN
            UPDATE switchboard.goals SET exposed=true, updated_at=now()
            WHERE id=NEW.goal_id AND NOT exposed;
        END IF;
        RETURN NULL;
    END $$;
"""

_BACKFILL = f"""
    UPDATE switchboard.goals SET
        admitted_label_version = 1,
        admitted_label_instr = CASE WHEN admitted_exposed THEN '{{world}}'::text[] ELSE '{{owner}}'::text[] END,
        admitted_label_data = CASE WHEN admitted_exposed THEN '{{world}}'::text[] ELSE '{{owner}}'::text[] END,
        admitted_label_sens = {_ALL_SIX},
        label_version = 1,
        label_instr = CASE WHEN exposed THEN '{{world}}'::text[] ELSE '{{owner}}'::text[] END,
        label_data = CASE WHEN exposed THEN '{{world}}'::text[] ELSE '{{owner}}'::text[] END,
        label_sens = {_ALL_SIX}
    WHERE label_version IS NULL;
    UPDATE switchboard.invocations SET
        label_version = 1,
        label_instr = CASE WHEN exposed THEN '{{world}}'::text[] ELSE '{{owner}}'::text[] END,
        label_data = CASE WHEN exposed THEN '{{world}}'::text[] ELSE '{{owner}}'::text[] END,
        label_sens = {_ALL_SIX},
        exposure_cause = CASE WHEN exposed THEN 'legacy_unknown' END
    WHERE label_version IS NULL;
    UPDATE switchboard.effects e SET
        label_version = i.label_version, label_instr = i.label_instr,
        label_data = i.label_data, label_sens = i.label_sens
    FROM switchboard.invocations i
    WHERE (i.goal_id, i.id) = (e.goal_id, e.invocation_id) AND e.label_version IS NULL;
    UPDATE switchboard.goal_inbox b SET
        label_version = 1,
        label_instr = CASE WHEN g.exposed THEN '{{world}}'::text[] ELSE '{{owner}}'::text[] END,
        label_data = CASE WHEN g.exposed THEN '{{world}}'::text[] ELSE '{{owner}}'::text[] END,
        label_sens = {_ALL_SIX}
    FROM switchboard.goals g WHERE g.id = b.goal_id AND b.label_version IS NULL;
"""

_TRIGGERS = f"""
    -- The admitted label comes from server-held admission facts; a Boolean-only
    -- exposure is unknown provenance.
    CREATE OR REPLACE FUNCTION switchboard.goal_label_goal()
    RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
    DECLARE
        cause RECORD;
    BEGIN
        IF TG_OP='UPDATE' AND OLD.exposed AND NOT NEW.exposed THEN
            RAISE EXCEPTION 'Goal authority and source association are immutable';
        END IF;
        IF TG_OP='INSERT' THEN
            IF NEW.admitted_label_version IS NULL THEN
                NEW.admitted_label_version := 1;
                NEW.admitted_label_instr := CASE WHEN NEW.admitted_exposed
                    THEN '{{world}}'::text[] ELSE '{{owner}}'::text[] END;
                NEW.admitted_label_data := NEW.admitted_label_instr;
                NEW.admitted_label_sens := CASE WHEN NEW.admitted_exposed
                    THEN {_ALL_SIX} ELSE '{{}}'::text[] END;
            END IF;
            IF NEW.caused_by_effect IS NOT NULL THEN
                -- A caused admission inherits the causing effect's label.
                SELECT label_instr AS i, label_data AS d, label_sens AS c INTO cause
                FROM switchboard.effects WHERE goal_id=NEW.caused_by_goal AND id=NEW.caused_by_effect;
                NEW.admitted_label_instr := switchboard.goal_label_union(NEW.admitted_label_instr, cause.i);
                NEW.admitted_label_data := switchboard.goal_label_union(NEW.admitted_label_data, cause.d);
                NEW.admitted_label_sens := switchboard.goal_label_union(NEW.admitted_label_sens, cause.c);
            END IF;
            IF NEW.admitted_exposed AND NOT switchboard.goal_label_exposed(
                    NEW.admitted_label_instr, NEW.admitted_label_data) THEN
                NEW.admitted_label_instr := switchboard.goal_label_union(NEW.admitted_label_instr, '{{world}}');
                NEW.admitted_label_data := switchboard.goal_label_union(NEW.admitted_label_data, '{{world}}');
                NEW.admitted_label_sens := {_ALL_SIX};
            END IF;
            NEW.admitted_exposed := switchboard.goal_label_exposed(
                NEW.admitted_label_instr, NEW.admitted_label_data);
            NEW.exposed := NEW.exposed OR NEW.admitted_exposed;
            NEW.label_version := 1;
            NEW.label_instr := switchboard.goal_label_union(NEW.label_instr, NEW.admitted_label_instr);
            NEW.label_data := switchboard.goal_label_union(NEW.label_data, NEW.admitted_label_data);
            NEW.label_sens := switchboard.goal_label_union(NEW.label_sens, NEW.admitted_label_sens);
        END IF;
        IF NEW.exposed AND NOT switchboard.goal_label_exposed(NEW.label_instr, NEW.label_data) THEN
            NEW.label_instr := switchboard.goal_label_union(NEW.label_instr, '{{world}}');
            NEW.label_data := switchboard.goal_label_union(NEW.label_data, '{{world}}');
            NEW.label_sens := {_ALL_SIX};
        END IF;
        NEW.exposed := NEW.exposed OR switchboard.goal_label_exposed(NEW.label_instr, NEW.label_data);
        RETURN NEW;
    END $$;

    -- S1 first switch: an invocation's required start label is its goal's
    -- admitted label joined with its actual caller (a child) or with every
    -- earlier lead and the goal's recorded effects (a replacement or escalated
    -- lead's recovered context), never the goal union. Shared by the BEFORE
    -- trigger that sets it and the AFTER check that verifies the final row.
    CREATE OR REPLACE FUNCTION switchboard.goal_label_start(g UUID, parent UUID, epoch BIGINT)
    RETURNS TABLE (i TEXT[], d TEXT[], c TEXT[], cause TEXT)
    LANGUAGE plpgsql STABLE SET search_path=pg_catalog AS $$
    DECLARE
        goal RECORD;
        inherited RECORD;
    BEGIN
        SELECT admitted_label_instr AS i, admitted_label_data AS d,
               admitted_label_sens AS c INTO goal
        FROM switchboard.goals WHERE id=g;
        IF switchboard.goal_label_exposed(goal.i, goal.d) THEN cause := 'ingress'; END IF;
        IF parent IS NOT NULL THEN
            SELECT p.label_instr AS i, p.label_data AS d, p.label_sens AS c INTO inherited
            FROM switchboard.invocations p WHERE p.goal_id=g AND p.id=parent;
            IF cause IS NULL AND switchboard.goal_label_exposed(inherited.i, inherited.d) THEN
                cause := 'child';
            END IF;
        ELSE
            SELECT switchboard.goal_label_union(
                       (SELECT array_agg(x) FROM switchboard.invocations p,
                               unnest(p.label_instr) x
                        WHERE p.goal_id=g AND p.depth=0 AND p.lead_epoch < epoch),
                       (SELECT array_agg(x) FROM switchboard.effects e,
                               unnest(e.label_instr) x WHERE e.goal_id=g)) AS i,
                   switchboard.goal_label_union(
                       (SELECT array_agg(x) FROM switchboard.invocations p,
                               unnest(p.label_data) x
                        WHERE p.goal_id=g AND p.depth=0 AND p.lead_epoch < epoch),
                       (SELECT array_agg(x) FROM switchboard.effects e,
                               unnest(e.label_data) x WHERE e.goal_id=g)) AS d,
                   switchboard.goal_label_union(
                       (SELECT array_agg(x) FROM switchboard.invocations p,
                               unnest(p.label_sens) x
                        WHERE p.goal_id=g AND p.depth=0 AND p.lead_epoch < epoch),
                       (SELECT array_agg(x) FROM switchboard.effects e,
                               unnest(e.label_sens) x WHERE e.goal_id=g)) AS c
            INTO inherited;
            IF cause IS NULL AND switchboard.goal_label_exposed(inherited.i, inherited.d) THEN
                cause := 'recovery';
            END IF;
        END IF;
        i := switchboard.goal_label_union(goal.i, inherited.i);
        d := switchboard.goal_label_union(goal.d, inherited.d);
        c := switchboard.goal_label_union(goal.c, inherited.c);
        RETURN NEXT;
    END $$;

    CREATE OR REPLACE FUNCTION switchboard.goal_label_invocation()
    RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
    DECLARE
        start RECORD;
        cause TEXT;
    BEGIN
        IF TG_OP='UPDATE' AND OLD.exposed AND NOT NEW.exposed THEN
            RAISE EXCEPTION 'Invocation lineage, grant and exposure are immutable';
        END IF;
        IF TG_OP='INSERT' THEN
            SELECT * INTO start FROM switchboard.goal_label_start(
                NEW.goal_id, NEW.parent_invocation_id, NEW.lead_epoch);
            cause := start.cause;
            NEW.label_version := 1;
            NEW.label_instr := switchboard.goal_label_union(NEW.label_instr, start.i);
            NEW.label_data := switchboard.goal_label_union(NEW.label_data, start.d);
            NEW.label_sens := switchboard.goal_label_union(NEW.label_sens, start.c);
        END IF;
        IF NEW.exposed AND NOT switchboard.goal_label_exposed(NEW.label_instr, NEW.label_data) THEN
            NEW.label_instr := switchboard.goal_label_union(NEW.label_instr, '{{world}}');
            NEW.label_data := switchboard.goal_label_union(NEW.label_data, '{{world}}');
            NEW.label_sens := {_ALL_SIX};
            cause := COALESCE(cause, 'legacy_unknown');
        END IF;
        NEW.exposed := switchboard.goal_label_exposed(NEW.label_instr, NEW.label_data);
        IF NEW.exposed AND NEW.exposure_cause IS NULL THEN
            NEW.exposure_cause := COALESCE(cause, 'legacy_unknown');
        END IF;
        RETURN NEW;
    END $$;

    -- An effect records the label of the invocation that requested it.
    CREATE OR REPLACE FUNCTION switchboard.goal_label_effect()
    RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
    DECLARE
        invoking RECORD;
    BEGIN
        SELECT label_instr AS i, label_data AS d, label_sens AS c INTO invoking
        FROM switchboard.invocations WHERE goal_id=NEW.goal_id AND id=NEW.invocation_id;
        NEW.label_version := 1;
        NEW.label_instr := switchboard.goal_label_union(NEW.label_instr, invoking.i);
        NEW.label_data := switchboard.goal_label_union(NEW.label_data, invoking.d);
        NEW.label_sens := switchboard.goal_label_union(NEW.label_sens, invoking.c);
        RETURN NEW;
    END $$;

    -- A verified owner reply carries the label of the effect it answers.
    CREATE OR REPLACE FUNCTION switchboard.goal_label_inbox()
    RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
    DECLARE
        answered RECORD;
    BEGIN
        SELECT label_instr AS i, label_data AS d, label_sens AS c INTO answered
        FROM switchboard.effects WHERE goal_id=NEW.goal_id AND id=NEW.reply_effect_id;
        NEW.label_version := 1;
        NEW.label_instr := switchboard.goal_label_union(
            switchboard.goal_label_union(NEW.label_instr, '{{owner}}'), answered.i);
        NEW.label_data := switchboard.goal_label_union(
            switchboard.goal_label_union(NEW.label_data, '{{owner}}'), answered.d);
        NEW.label_sens := switchboard.goal_label_union(NEW.label_sens, answered.c);
        RETURN NEW;
    END $$;

    -- The goal's reporting union takes every invocation and inbox label.
    CREATE OR REPLACE FUNCTION switchboard.goal_invocation_exposure()
    RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
    BEGIN
        UPDATE switchboard.goals SET
            label_instr = switchboard.goal_label_union(label_instr, NEW.label_instr),
            label_data = switchboard.goal_label_union(label_data, NEW.label_data),
            label_sens = switchboard.goal_label_union(label_sens, NEW.label_sens),
            updated_at = now()
        WHERE id=NEW.goal_id AND NOT (label_instr @> NEW.label_instr
            AND label_data @> NEW.label_data AND label_sens @> NEW.label_sens);
        RETURN NULL;
    END $$;

    DROP TRIGGER IF EXISTS goal_label_goal ON switchboard.goals;
    CREATE TRIGGER goal_label_goal BEFORE INSERT OR UPDATE ON switchboard.goals
        FOR EACH ROW EXECUTE FUNCTION switchboard.goal_label_goal();
    DROP TRIGGER IF EXISTS goal_label_invocation ON switchboard.invocations;
    CREATE TRIGGER goal_label_invocation BEFORE INSERT OR UPDATE ON switchboard.invocations
        FOR EACH ROW EXECUTE FUNCTION switchboard.goal_label_invocation();
    DROP TRIGGER IF EXISTS goal_label_effect ON switchboard.effects;
    CREATE TRIGGER goal_label_effect BEFORE INSERT ON switchboard.effects
        FOR EACH ROW EXECUTE FUNCTION switchboard.goal_label_effect();
    DROP TRIGGER IF EXISTS goal_label_inbox ON switchboard.goal_inbox;
    CREATE TRIGGER goal_label_inbox BEFORE INSERT ON switchboard.goal_inbox
        FOR EACH ROW EXECUTE FUNCTION switchboard.goal_label_inbox();
    DROP TRIGGER IF EXISTS goal_invocation_exposure ON switchboard.invocations;
    CREATE TRIGGER goal_invocation_exposure
        AFTER INSERT OR UPDATE OF exposed, label_instr, label_data, label_sens
        ON switchboard.invocations
        FOR EACH ROW EXECUTE FUNCTION switchboard.goal_invocation_exposure();
    DROP TRIGGER IF EXISTS goal_inbox_label ON switchboard.goal_inbox;
    CREATE TRIGGER goal_inbox_label AFTER INSERT ON switchboard.goal_inbox
        FOR EACH ROW EXECUTE FUNCTION switchboard.goal_invocation_exposure();
"""

# The runtime role holds TRIGGER (core_001 re-grants it per schema), so it could
# add a BEFORE trigger that sorts after the label triggers and rewrites NEW. The
# same checks therefore run again as AFTER ROW constraint triggers, on the final
# row no BEFORE trigger can still change: an insert must cover its required
# label, and an update must not narrow or rewrite what sw_057 fixes.
_SETTLED = """
    CREATE OR REPLACE FUNCTION switchboard.goal_label_settled()
    RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
    DECLARE
        req RECORD;
    BEGIN
        IF TG_TABLE_NAME='invocations' THEN
            SELECT * INTO req FROM switchboard.goal_label_start(
                NEW.goal_id, NEW.parent_invocation_id, NEW.lead_epoch);
        ELSIF TG_TABLE_NAME='effects' THEN
            SELECT label_instr AS i, label_data AS d, label_sens AS c INTO req
            FROM switchboard.invocations WHERE goal_id=NEW.goal_id AND id=NEW.invocation_id;
        ELSIF TG_TABLE_NAME='goal_inbox' THEN
            SELECT switchboard.goal_label_union(label_instr, '{owner}') AS i,
                   switchboard.goal_label_union(label_data, '{owner}') AS d,
                   label_sens AS c INTO req
            FROM switchboard.effects WHERE goal_id=NEW.goal_id AND id=NEW.reply_effect_id;
        ELSIF TG_TABLE_NAME='goals' THEN
            IF NOT (NEW.label_instr @> NEW.admitted_label_instr
                    AND NEW.label_data @> NEW.admitted_label_data
                    AND NEW.label_sens @> NEW.admitted_label_sens) THEN
                RAISE EXCEPTION 'A goal label must cover its admitted label'
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
            -- A caused admission's admitted label covers the causing effect's.
            IF NEW.caused_by_effect IS NULL THEN
                RETURN NULL;
            END IF;
            SELECT label_instr AS i, label_data AS d, label_sens AS c INTO req
            FROM switchboard.effects WHERE goal_id=NEW.caused_by_goal AND id=NEW.caused_by_effect;
            IF NOT COALESCE(NEW.admitted_label_instr @> req.i AND NEW.admitted_label_data @> req.d
                            AND NEW.admitted_label_sens @> req.c, false) THEN
                RAISE EXCEPTION 'A ledger label must cover the label it inherits'
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
            RETURN NULL;
        END IF;
        IF NOT COALESCE(NEW.label_instr @> req.i AND NEW.label_data @> req.d
                        AND NEW.label_sens @> req.c, false) THEN
            RAISE EXCEPTION 'A ledger label must cover the label it inherits'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        RETURN NULL;
    END $$;
    DO $$
    DECLARE
        t TEXT;
    BEGIN
        FOREACH t IN ARRAY ARRAY['goals','invocations','effects','goal_inbox'] LOOP
            EXECUTE format('DROP TRIGGER IF EXISTS goal_label_settled ON switchboard.%I', t);
            EXECUTE format('CREATE CONSTRAINT TRIGGER goal_label_settled AFTER INSERT '
                'ON switchboard.%I FOR EACH ROW EXECUTE FUNCTION switchboard.goal_label_settled()', t);
            EXECUTE format('DROP TRIGGER IF EXISTS goal_label_immutable_settled ON switchboard.%I', t);
            EXECUTE format('CREATE CONSTRAINT TRIGGER goal_label_immutable_settled AFTER UPDATE '
                'ON switchboard.%I FOR EACH ROW EXECUTE FUNCTION switchboard.goal_ledger_immutable()', t);
        END LOOP;
    END $$;

    -- A replacement lead's start label is derived from earlier leads and the
    -- goal's recorded effects, so neither may disappear. Statement level, so it
    -- fires when RLS leaves no row to touch and on TRUNCATE (including CASCADE),
    -- for the runtime role and the owner alike; grants cannot be the guard
    -- because core_001 re-grants own-schema DML.
    CREATE OR REPLACE FUNCTION switchboard.guard_goal_lineage()
    RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
    BEGIN
        RAISE EXCEPTION 'Invocation and effect history is durable'
            USING ERRCODE = 'insufficient_privilege';
    END $$;
    DROP TRIGGER IF EXISTS durable_goal_lineage ON switchboard.invocations;
    CREATE TRIGGER durable_goal_lineage BEFORE DELETE OR TRUNCATE ON switchboard.invocations
        FOR EACH STATEMENT EXECUTE FUNCTION switchboard.guard_goal_lineage();
    DROP TRIGGER IF EXISTS durable_goal_lineage ON switchboard.effects;
    CREATE TRIGGER durable_goal_lineage BEFORE DELETE OR TRUNCATE ON switchboard.effects
        FOR EACH STATEMENT EXECUTE FUNCTION switchboard.guard_goal_lineage();
    DROP POLICY IF EXISTS goal_ledger_delete ON switchboard.invocations;
    CREATE POLICY goal_ledger_delete ON switchboard.invocations FOR DELETE
        TO butler_switchboard_rw USING (false);
    DROP POLICY IF EXISTS goal_ledger_delete ON switchboard.effects;
    CREATE POLICY goal_ledger_delete ON switchboard.effects FOR DELETE
        TO butler_switchboard_rw USING (false);
"""

_UNSETTLED = """
    DROP TRIGGER IF EXISTS durable_goal_lineage ON switchboard.invocations;
    DROP TRIGGER IF EXISTS durable_goal_lineage ON switchboard.effects;
    DROP FUNCTION IF EXISTS switchboard.guard_goal_lineage();
    DROP POLICY IF EXISTS goal_ledger_delete ON switchboard.invocations;
    CREATE POLICY goal_ledger_delete ON switchboard.invocations FOR DELETE
        TO butler_switchboard_rw USING (current_user='butler_switchboard_rw');
    DROP POLICY IF EXISTS goal_ledger_delete ON switchboard.effects;
    CREATE POLICY goal_ledger_delete ON switchboard.effects FOR DELETE
        TO butler_switchboard_rw USING (current_user='butler_switchboard_rw');
    DROP TRIGGER IF EXISTS goal_label_settled ON switchboard.goals;
    DROP TRIGGER IF EXISTS goal_label_settled ON switchboard.invocations;
    DROP TRIGGER IF EXISTS goal_label_settled ON switchboard.effects;
    DROP TRIGGER IF EXISTS goal_label_settled ON switchboard.goal_inbox;
    DROP TRIGGER IF EXISTS goal_label_immutable_settled ON switchboard.goals;
    DROP TRIGGER IF EXISTS goal_label_immutable_settled ON switchboard.invocations;
    DROP TRIGGER IF EXISTS goal_label_immutable_settled ON switchboard.effects;
    DROP TRIGGER IF EXISTS goal_label_immutable_settled ON switchboard.goal_inbox;
    DROP FUNCTION IF EXISTS switchboard.goal_label_settled();
"""


# Content-blind counts that compare the switched gate with the one it replaced.
# ``goal_union_exposed`` is the sw_047 gate (an invocation was treated as exposed
# whenever its goal was) and stays the retained pre-switch baseline;
# ``invocation_exposed`` is the S1 gate. Rows where the first is true and the
# second false are the invocations the switch stopped restricting.
# ``approval_routed_effects`` counts effects that reached the owning approvals
# module (they carry an approval reference). Owner-only: no role is granted it.
_MEASUREMENTS = """
    CREATE VIEW switchboard.invocation_exposure_measurements AS
    SELECT i.role, i.exposure_cause, g.exposed AS goal_union_exposed,
           i.exposed AS invocation_exposed, count(*) AS invocations,
           coalesce(sum(e.total), 0)::bigint AS effects,
           coalesce(sum(e.routed), 0)::bigint AS approval_routed_effects
    FROM switchboard.invocations i
    JOIN switchboard.goals g ON g.id = i.goal_id
    LEFT JOIN LATERAL (
        SELECT count(*) AS total, count(*) FILTER (WHERE approval_ref IS NOT NULL) AS routed
        FROM switchboard.effects
        WHERE goal_id = i.goal_id AND invocation_id = i.id
    ) e ON true
    GROUP BY i.role, i.exposure_cause, g.exposed, i.exposed
"""


def _label_columns(prefix: str = "") -> str:
    return ", ".join(
        f"ADD COLUMN IF NOT EXISTS {prefix}{name} {kind}"
        for name, kind in (
            ("label_version", "SMALLINT"),
            ("label_instr", "TEXT[]"),
            ("label_data", "TEXT[]"),
            ("label_sens", "TEXT[]"),
        )
    )


def _labelled(table: str, prefix: str = "") -> str:
    columns = ", ".join(
        f"ALTER COLUMN {prefix}{name} SET NOT NULL"
        for name in ("label_version", "label_instr", "label_data", "label_sens")
    )
    check = f"ck_{table}_{prefix}label"
    return f"""
        ALTER TABLE switchboard.{table} {columns};
        ALTER TABLE switchboard.{table} DROP CONSTRAINT IF EXISTS {check};
        ALTER TABLE switchboard.{table} ADD CONSTRAINT {check} CHECK (
            switchboard.goal_label_valid({prefix}label_version, {prefix}label_instr,
                {prefix}label_data, {prefix}label_sens));
    """


def upgrade() -> None:
    for table in ("goals", "invocations", "effects", "budgets", "admission_decisions", *_LABELLED):
        op.execute(f"LOCK TABLE switchboard.{table} IN ACCESS EXCLUSIVE MODE")
    op.execute(_HELPERS)
    op.execute(f"ALTER TABLE switchboard.goals {_label_columns()}, {_label_columns('admitted_')}")
    op.execute(
        f"ALTER TABLE switchboard.invocations {_label_columns()}, "
        "ADD COLUMN IF NOT EXISTS exposure_cause TEXT"
    )
    op.execute(f"ALTER TABLE switchboard.effects {_label_columns()}")
    op.execute(f"ALTER TABLE switchboard.goal_inbox {_label_columns()}")
    op.execute(_IMMUTABLE)
    op.execute(_BACKFILL)
    for table in _LABELLED:
        op.execute(_labelled(table))
    op.execute(_labelled("goals", "admitted_"))
    op.execute("""
        ALTER TABLE switchboard.goals DROP CONSTRAINT IF EXISTS ck_goal_label_exposure;
        ALTER TABLE switchboard.goals ADD CONSTRAINT ck_goal_label_exposure CHECK (
            exposed = switchboard.goal_label_exposed(label_instr, label_data)
            AND admitted_exposed = switchboard.goal_label_exposed(
                admitted_label_instr, admitted_label_data));
        ALTER TABLE switchboard.invocations DROP CONSTRAINT IF EXISTS ck_invocation_label_exposure;
        ALTER TABLE switchboard.invocations ADD CONSTRAINT ck_invocation_label_exposure CHECK (
            exposed = switchboard.goal_label_exposed(label_instr, label_data)
            AND (exposure_cause IS NOT NULL) = exposed
            AND (exposure_cause IS NULL OR exposure_cause IN (
                'ingress','read','child','inbox','recovery','legacy_unknown')));
    """)
    op.execute(_TRIGGERS)
    op.execute(_SETTLED)
    op.execute(_MEASUREMENTS)


def downgrade() -> None:
    for table in ("goals", "invocations", "effects", "budgets", "admission_decisions", *_LABELLED):
        op.execute(f"LOCK TABLE switchboard.{table} IN ACCESS EXCLUSIVE MODE")
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM switchboard.goals) THEN
                RAISE EXCEPTION 'Cannot discard durable invocation labels';
            END IF;
        END $$;
        DROP VIEW IF EXISTS switchboard.invocation_exposure_measurements;
    """)
    op.execute(_UNSETTLED)
    op.execute("""
        DROP TRIGGER IF EXISTS goal_inbox_label ON switchboard.goal_inbox;
        DROP TRIGGER IF EXISTS goal_label_inbox ON switchboard.goal_inbox;
        DROP TRIGGER IF EXISTS goal_label_effect ON switchboard.effects;
        DROP TRIGGER IF EXISTS goal_label_invocation ON switchboard.invocations;
        DROP TRIGGER IF EXISTS goal_label_goal ON switchboard.goals;
        DROP TRIGGER IF EXISTS goal_invocation_exposure ON switchboard.invocations;
    """)
    op.execute(_SW047_EXPOSURE)
    op.execute("""
        CREATE TRIGGER goal_invocation_exposure
            AFTER INSERT OR UPDATE OF exposed ON switchboard.invocations
            FOR EACH ROW EXECUTE FUNCTION switchboard.goal_invocation_exposure();
    """)
    op.execute(_SW047_IMMUTABLE)
    op.execute("""
        ALTER TABLE switchboard.goals DROP CONSTRAINT IF EXISTS ck_goal_label_exposure;
        ALTER TABLE switchboard.invocations DROP CONSTRAINT IF EXISTS ck_invocation_label_exposure;
        ALTER TABLE switchboard.goals
            DROP COLUMN label_version, DROP COLUMN label_instr, DROP COLUMN label_data,
            DROP COLUMN label_sens, DROP COLUMN admitted_label_version,
            DROP COLUMN admitted_label_instr, DROP COLUMN admitted_label_data,
            DROP COLUMN admitted_label_sens;
        ALTER TABLE switchboard.invocations
            DROP COLUMN label_version, DROP COLUMN label_instr, DROP COLUMN label_data,
            DROP COLUMN label_sens, DROP COLUMN exposure_cause;
        ALTER TABLE switchboard.effects
            DROP COLUMN label_version, DROP COLUMN label_instr, DROP COLUMN label_data,
            DROP COLUMN label_sens;
        ALTER TABLE switchboard.goal_inbox
            DROP COLUMN label_version, DROP COLUMN label_instr, DROP COLUMN label_data,
            DROP COLUMN label_sens;
        DROP FUNCTION IF EXISTS switchboard.goal_label_goal();
        DROP FUNCTION IF EXISTS switchboard.goal_label_invocation();
        DROP FUNCTION IF EXISTS switchboard.goal_label_start(UUID, UUID, BIGINT);
        DROP FUNCTION IF EXISTS switchboard.goal_label_effect();
        DROP FUNCTION IF EXISTS switchboard.goal_label_inbox();
        DROP FUNCTION IF EXISTS switchboard.goal_label_narrowed(
            SMALLINT, TEXT[], TEXT[], TEXT[], SMALLINT, TEXT[], TEXT[], TEXT[]);
        DROP FUNCTION IF EXISTS switchboard.goal_label_union(TEXT[], TEXT[]);
        DROP FUNCTION IF EXISTS switchboard.goal_label_exposed(TEXT[], TEXT[]);
        DROP FUNCTION IF EXISTS switchboard.goal_label_valid(SMALLINT, TEXT[], TEXT[], TEXT[]);
    """)
