"""Durable goal waiting: owner threads, plan versions, epochs, wait envelopes, wake intents.

Revision ID: sw_063
Revises: sw_062

RFC 0038 S2 / S2.1 / S2.2 (bu-dhloi1.7, PR A). Dark like the rest of the ledger: nothing here is
wired to ingress or to the runner. This revision is additive: it adds records and constraints and
leaves every existing writer working, so the runner and admission keep running unchanged until
the follow-up PRs consume the new records.

* ``owner_threads``: the one owner thread registry. ``goals.thread_id`` becomes a real FK.
  Legacy ``thread_id`` values with no registry row are backfilled as ``legacy`` threads.
* ``goal_plans``: append-only plan versions with premise and evidence links;
  ``goals.plan_version`` and the envelope bind ``(goal_id, version)`` by FK.
* ``goal_epochs``: ``(goal_id, epoch)`` with the reason, label snapshot, grant digest and a
  forward-only state. Backfilled from the existing lead invocations (``lead_epoch``).
* ``wait_envelopes`` and ``wait_generations``: the immutable envelope bounds, the cumulative
  ``consumed_wakes`` counter and one registration row per generation. ``kind`` keeps the
  waits apart: ``conditions`` and ``owner_answer`` and ``approval`` launch a successor, while
  ``settle`` only resolves the goal with no successor epoch.
* ``wake_intents``: the outbox. ``fire_key`` (the evaluator's fire key) is unique, a successor
  is unique per ``(envelope, generation)``, and a fire the ledger refused ends in a terminal
  ``not_admitted`` row with its reason, so a sweeper never retries it forever.
* ``goal_inbox``: discriminated by ``input_kind`` (``owner_reply``, ``owner_non_reply``,
  ``condition_fire``); consumption is the one-time ``(consumed_at, consumed_epoch)`` tuple, and
  already-consumed rows are marked ``legacy_consumed`` rather than given an invented epoch.
* ``admission_decisions``: the same ``input_kind`` and the ``input_joined`` /
  ``condition_joined`` reasons. A decision for a reply join still written by the existing
  admission writer without a kind is filled in as ``owner_reply``.
* ``goals.status`` additionally admits ``waiting``. The legacy states stay until the runner
  stops using them (the follow-up PR), so no row or writer changes meaning here.

The downgrade refuses while any new record exists.
"""

from alembic import op

revision = "sw_063"
down_revision = "sw_062"
branch_labels = None
depends_on = None

_RUNTIME = "butler_switchboard_rw"
_LABEL_COLUMNS = """
    label_version SMALLINT NOT NULL,
    label_instr TEXT[] NOT NULL,
    label_data TEXT[] NOT NULL,
    label_sens TEXT[] NOT NULL,
"""
_LABEL_CHECK = "switchboard.goal_label_valid(label_version, label_instr, label_data, label_sens)"
_NEW_TABLES = (
    "owner_threads",
    "goal_epochs",
    "goal_plans",
    "wait_envelopes",
    "wait_generations",
    "wake_intents",
)
# Mutable tables keep UPDATE for the runtime role; the rest are append-only.
_MUTABLE = frozenset({"goal_epochs", "wait_envelopes", "wake_intents"})
_REASONS = (
    "source_evidence_unavailable",
    "limits_unavailable",
    "cause_invalid",
    "creator_unavailable",
    "causal_chain_limit",
    "reply_joined",
    "quota_exhausted",
    "new_goal",
    "recorded_without_goal",
)
_NEW_REASONS = ("input_joined", "condition_joined")


# sw_057's definitions of the three functions this revision replaces, restored on downgrade.
_SW057_FUNCTIONS = """
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

    CREATE OR REPLACE FUNCTION switchboard.goal_label_inbox()
    RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
    DECLARE
        answered RECORD;
    BEGIN
        SELECT label_instr AS i, label_data AS d, label_sens AS c INTO answered
        FROM switchboard.effects WHERE goal_id=NEW.goal_id AND id=NEW.reply_effect_id;
        NEW.label_version := 1;
        NEW.label_instr := switchboard.goal_label_union(
            switchboard.goal_label_union(NEW.label_instr, '{owner}'), answered.i);
        NEW.label_data := switchboard.goal_label_union(
            switchboard.goal_label_union(NEW.label_data, '{owner}'), answered.d);
        NEW.label_sens := switchboard.goal_label_union(NEW.label_sens, answered.c);
        RETURN NEW;
    END $$;

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
"""


def _quoted(values: tuple[str, ...]) -> str:
    return ",".join(f"'{value}'" for value in values)


_TABLES_SQL = f"""
    CREATE TABLE switchboard.owner_threads (
        id UUID PRIMARY KEY,
        owner_entity_id UUID,
        channel TEXT NOT NULL CHECK (length(channel) BETWEEN 1 AND 64),
        conversation_key TEXT NOT NULL CHECK (length(conversation_key) BETWEEN 1 AND 512),
        legacy BOOLEAN NOT NULL DEFAULT false,
        created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT ck_thread_owner CHECK (legacy OR owner_entity_id IS NOT NULL),
        CONSTRAINT uq_owner_thread UNIQUE (owner_entity_id, channel, conversation_key)
    );

    CREATE TABLE switchboard.goal_epochs (
        goal_id UUID NOT NULL REFERENCES switchboard.goals(id),
        epoch BIGINT NOT NULL CHECK (epoch >= 0),
        predecessor_invocation_id UUID,
        reason TEXT NOT NULL CHECK (reason IN ('initial','wake','recovery','escalation')),
        input_refs JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (
            jsonb_typeof(input_refs) = 'array' AND jsonb_array_length(input_refs) <= 64),
        {_LABEL_COLUMNS}
        grant_digest TEXT,
        state TEXT NOT NULL CHECK (state IN ('pending','launching','running','finished','failed')),
        created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        PRIMARY KEY (goal_id, epoch),
        CONSTRAINT ck_epoch_label CHECK ({_LABEL_CHECK}),
        CONSTRAINT ck_epoch_initial CHECK ((reason = 'initial') = (predecessor_invocation_id IS NULL)),
        CONSTRAINT fk_epoch_predecessor FOREIGN KEY (goal_id, predecessor_invocation_id)
            REFERENCES switchboard.invocations(goal_id, id)
    );

    CREATE TABLE switchboard.goal_plans (
        goal_id UUID NOT NULL REFERENCES switchboard.goals(id),
        version BIGINT NOT NULL CHECK (version >= 1),
        predecessor_version BIGINT,
        premise_refs JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (
            jsonb_typeof(premise_refs) = 'array' AND jsonb_array_length(premise_refs) <= 64),
        evidence_refs JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (
            jsonb_typeof(evidence_refs) = 'array' AND jsonb_array_length(evidence_refs) <= 64),
        step_refs JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (
            jsonb_typeof(step_refs) = 'array' AND jsonb_array_length(step_refs) <= 64),
        created_epoch BIGINT NOT NULL,
        digest TEXT NOT NULL CHECK (digest ~ '^[0-9a-f]{{64}}$'),
        created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        PRIMARY KEY (goal_id, version),
        CONSTRAINT ck_plan_chain CHECK (
            (version = 1 AND predecessor_version IS NULL) OR predecessor_version = version - 1),
        CONSTRAINT fk_plan_predecessor FOREIGN KEY (goal_id, predecessor_version)
            REFERENCES switchboard.goal_plans(goal_id, version),
        CONSTRAINT fk_plan_epoch FOREIGN KEY (goal_id, created_epoch)
            REFERENCES switchboard.goal_epochs(goal_id, epoch)
    );

    CREATE TABLE switchboard.wait_envelopes (
        id UUID PRIMARY KEY,
        goal_id UUID NOT NULL REFERENCES switchboard.goals(id),
        kind TEXT NOT NULL CHECK (kind IN ('conditions','owner_answer','approval','settle')),
        original_epoch BIGINT NOT NULL,
        plan_version BIGINT NOT NULL,
        plan_digest TEXT NOT NULL CHECK (plan_digest ~ '^[0-9a-f]{{64}}$'),
        predicate JSONB NOT NULL CHECK (
            jsonb_typeof(predicate) = 'object' AND octet_length(predicate::text) <= 8192),
        predicate_digest TEXT NOT NULL CHECK (predicate_digest ~ '^[0-9a-f]{{64}}$'),
        source_set TEXT[] NOT NULL CHECK (cardinality(source_set) BETWEEN 1 AND 16),
        permitted_sources TEXT[] NOT NULL CHECK (cardinality(permitted_sources) BETWEEN 1 AND 16),
        grant_snapshot JSONB NOT NULL CHECK (jsonb_typeof(grant_snapshot) = 'array'),
        grant_digest TEXT NOT NULL CHECK (grant_digest ~ '^[0-9a-f]{{64}}$'),
        {_LABEL_COLUMNS}
        deadline TIMESTAMPTZ NOT NULL CHECK (isfinite(deadline)),
        max_wakes INTEGER NOT NULL CHECK (max_wakes > 0),
        consumed_wakes INTEGER NOT NULL DEFAULT 0 CHECK (consumed_wakes >= 0),
        generation BIGINT NOT NULL DEFAULT 0 CHECK (generation >= 0),
        approval_effect_id UUID,
        approval_digest TEXT CHECK (approval_digest ~ '^[0-9a-f]{{64}}$'),
        state TEXT NOT NULL DEFAULT 'open'
            CHECK (state IN ('open','fired','expired','cancelled')),
        created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        UNIQUE (goal_id, id),
        CONSTRAINT ck_envelope_label CHECK ({_LABEL_CHECK}),
        CONSTRAINT ck_envelope_wakes CHECK (consumed_wakes <= max_wakes),
        CONSTRAINT ck_envelope_approval_pair CHECK (
            (approval_effect_id IS NULL) = (approval_digest IS NULL)),
        CONSTRAINT ck_envelope_approval_kind CHECK (kind <> 'approval' OR approval_effect_id IS NOT NULL),
        CONSTRAINT fk_envelope_epoch FOREIGN KEY (goal_id, original_epoch)
            REFERENCES switchboard.goal_epochs(goal_id, epoch),
        CONSTRAINT fk_envelope_plan FOREIGN KEY (goal_id, plan_version)
            REFERENCES switchboard.goal_plans(goal_id, version),
        CONSTRAINT fk_envelope_approval_effect FOREIGN KEY (goal_id, approval_effect_id)
            REFERENCES switchboard.effects(goal_id, id)
    );
    CREATE UNIQUE INDEX uq_wait_envelope_live ON switchboard.wait_envelopes(goal_id)
        WHERE state IN ('open','fired');

    CREATE TABLE switchboard.wait_generations (
        goal_id UUID NOT NULL,
        envelope_id UUID NOT NULL,
        generation BIGINT NOT NULL CHECK (generation >= 0),
        condition_instance_ids TEXT[] NOT NULL CHECK (cardinality(condition_instance_ids) >= 1),
        predecessor_epoch BIGINT,
        predecessor_fire_event_id UUID,
        registration_cursor JSONB NOT NULL DEFAULT '{{}}'::jsonb
            CHECK (jsonb_typeof(registration_cursor) = 'object'),
        created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        PRIMARY KEY (goal_id, envelope_id, generation),
        CONSTRAINT ck_generation_chain CHECK (
            (generation = 0 AND predecessor_epoch IS NULL AND predecessor_fire_event_id IS NULL)
            OR (generation > 0 AND predecessor_epoch IS NOT NULL
                AND predecessor_fire_event_id IS NOT NULL)),
        CONSTRAINT fk_generation_envelope FOREIGN KEY (goal_id, envelope_id)
            REFERENCES switchboard.wait_envelopes(goal_id, id),
        CONSTRAINT fk_generation_epoch FOREIGN KEY (goal_id, predecessor_epoch)
            REFERENCES switchboard.goal_epochs(goal_id, epoch)
    );

    CREATE TABLE switchboard.wake_intents (
        id UUID PRIMARY KEY,
        goal_id UUID NOT NULL,
        envelope_id UUID NOT NULL,
        generation BIGINT NOT NULL,
        fire_key UUID NOT NULL UNIQUE,
        fire_event_id UUID NOT NULL,
        fire_cursors JSONB NOT NULL DEFAULT '{{}}'::jsonb
            CHECK (jsonb_typeof(fire_cursors) = 'object'),
        epoch BIGINT,
        state TEXT NOT NULL CHECK (state IN
            ('pending','claimed','launched','failed','not_admitted','cancelled')),
        not_admitted_reason TEXT CHECK (not_admitted_reason IN (
            'stopped','expired','cap_reached','goal_not_waiting','envelope_not_open',
            'stale_generation')),
        cancelled_reason TEXT CHECK (cancelled_reason IN ('stopped','expired','superseded')),
        claimed_by TEXT,
        claim_expires_at TIMESTAMPTZ,
        attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
        created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT uq_wake_event UNIQUE (envelope_id, generation, fire_event_id),
        CONSTRAINT uq_wake_epoch UNIQUE (goal_id, epoch),
        CONSTRAINT ck_wake_outcome CHECK (
            (state = 'not_admitted' AND epoch IS NULL AND not_admitted_reason IS NOT NULL)
            OR (state <> 'not_admitted' AND epoch IS NOT NULL AND not_admitted_reason IS NULL)),
        CONSTRAINT ck_wake_cancelled CHECK ((state = 'cancelled') = (cancelled_reason IS NOT NULL)),
        CONSTRAINT ck_wake_claim CHECK (
            (state = 'claimed') = (claimed_by IS NOT NULL AND claim_expires_at IS NOT NULL)),
        CONSTRAINT fk_wake_generation FOREIGN KEY (goal_id, envelope_id, generation)
            REFERENCES switchboard.wait_generations(goal_id, envelope_id, generation),
        CONSTRAINT fk_wake_epoch FOREIGN KEY (goal_id, epoch)
            REFERENCES switchboard.goal_epochs(goal_id, epoch)
    );
    CREATE UNIQUE INDEX uq_wake_successor ON switchboard.wake_intents(envelope_id, generation)
        WHERE state <> 'not_admitted';
    CREATE INDEX ix_wake_intents_open ON switchboard.wake_intents(state, claim_expires_at)
        WHERE state IN ('pending','claimed','failed');
"""

_BACKFILL = """
    INSERT INTO switchboard.owner_threads(id, channel, conversation_key, legacy)
        SELECT DISTINCT thread_id, 'legacy', thread_id::text, true
        FROM switchboard.goals WHERE thread_id IS NOT NULL;
    ALTER TABLE switchboard.goals ADD CONSTRAINT fk_goal_thread
        FOREIGN KEY (thread_id) REFERENCES switchboard.owner_threads(id);

    INSERT INTO switchboard.goal_epochs(goal_id, epoch, predecessor_invocation_id, reason,
            label_version, label_instr, label_data, label_sens, grant_digest, state,
            created_at, updated_at)
        SELECT l.goal_id, l.lead_epoch, lag(l.id) OVER w,
               CASE WHEN lag(l.id) OVER w IS NULL THEN 'initial'
                    WHEN lag(l.status) OVER w = 'escalated' THEN 'escalation'
                    ELSE 'recovery' END,
               l.label_version, l.label_instr, l.label_data, l.label_sens, l.grant_digest,
               CASE l.status WHEN 'launching' THEN 'launching' WHEN 'running' THEN 'running'
                    WHEN 'returned' THEN 'finished' WHEN 'escalated' THEN 'finished'
                    ELSE 'failed' END,
               l.created_at, l.updated_at
        FROM (SELECT DISTINCT ON (goal_id, lead_epoch) *
              FROM switchboard.invocations WHERE depth = 0
              ORDER BY goal_id, lead_epoch, created_at, id) l
        WINDOW w AS (PARTITION BY l.goal_id ORDER BY l.lead_epoch);
"""

_GOALS_SQL = """
    ALTER TABLE switchboard.goals
        ADD COLUMN legacy_status TEXT,
        ADD COLUMN plan_version BIGINT CHECK (plan_version IS NULL OR plan_version >= 1),
        ADD COLUMN wait_generation BIGINT NOT NULL DEFAULT 0 CHECK (wait_generation >= 0);
    ALTER TABLE switchboard.goals ADD CONSTRAINT fk_goal_plan
        FOREIGN KEY (id, plan_version) REFERENCES switchboard.goal_plans(goal_id, version);
    ALTER TABLE switchboard.goals DROP CONSTRAINT goals_status_check;
    ALTER TABLE switchboard.goals ADD CONSTRAINT goals_status_check CHECK (status IN (
        'admitted','running','settling','completed','partial','failed','stopped',
        'budget_exhausted','awaiting_approval','recovering','waiting'));
"""

_DECISIONS_SQL = f"""
    ALTER TABLE switchboard.admission_decisions ADD COLUMN input_kind TEXT
        CHECK (input_kind IN ('owner_reply','owner_non_reply','condition_fire'));
    ALTER TABLE switchboard.admission_decisions DISABLE TRIGGER goal_ledger_immutable;
    UPDATE switchboard.admission_decisions SET input_kind = 'owner_reply'
        WHERE reason = 'reply_joined';
    ALTER TABLE switchboard.admission_decisions ENABLE TRIGGER goal_ledger_immutable;
    ALTER TABLE switchboard.admission_decisions
        DROP CONSTRAINT admission_decisions_reason_check;
    ALTER TABLE switchboard.admission_decisions
        ADD CONSTRAINT admission_decisions_reason_check
        CHECK (reason IN ({_quoted(_REASONS + _NEW_REASONS)}));
    ALTER TABLE switchboard.admission_decisions ADD CONSTRAINT ck_decision_input_kind CHECK (
        (reason = 'reply_joined' AND result = 'joined' AND input_kind = 'owner_reply')
        OR (reason = 'input_joined' AND result = 'joined' AND input_kind = 'owner_non_reply')
        OR (reason = 'condition_joined' AND result = 'joined' AND input_kind = 'condition_fire')
        OR (reason NOT IN ('reply_joined','input_joined','condition_joined')
            AND input_kind IS NULL));
    CREATE UNIQUE INDEX uq_admission_decision_goal_source
        ON switchboard.admission_decisions(id, goal_id, source_key);

    -- The existing admission writer does not name an input kind for a reply join.
    CREATE FUNCTION switchboard.goal_decision_default_kind()
    RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
    BEGIN
        IF NEW.input_kind IS NULL AND NEW.reason = 'reply_joined' THEN
            NEW.input_kind := 'owner_reply';
        END IF;
        RETURN NEW;
    END $$;
    CREATE TRIGGER goal_decision_default_kind BEFORE INSERT
        ON switchboard.admission_decisions FOR EACH ROW
        EXECUTE FUNCTION switchboard.goal_decision_default_kind();
"""

_INBOX_SQL = """
    ALTER TABLE switchboard.goal_inbox
        ADD COLUMN input_kind TEXT NOT NULL DEFAULT 'owner_reply'
            CHECK (input_kind IN ('owner_reply','owner_non_reply','condition_fire')),
        ADD COLUMN event_id UUID,
        ADD COLUMN envelope_id UUID,
        ADD COLUMN envelope_generation BIGINT,
        ADD COLUMN consumed_epoch BIGINT,
        ADD COLUMN legacy_consumed BOOLEAN NOT NULL DEFAULT false;
    ALTER TABLE switchboard.goal_inbox DISABLE TRIGGER goal_ledger_immutable;
    ALTER TABLE switchboard.goal_inbox DISABLE TRIGGER goal_label_immutable_settled;
    UPDATE switchboard.goal_inbox SET legacy_consumed = true WHERE consumed_at IS NOT NULL;
    ALTER TABLE switchboard.goal_inbox ENABLE TRIGGER goal_ledger_immutable;
    ALTER TABLE switchboard.goal_inbox ENABLE TRIGGER goal_label_immutable_settled;
    ALTER TABLE switchboard.goal_inbox ALTER COLUMN delivery_ref DROP NOT NULL;
    ALTER TABLE switchboard.goal_inbox ALTER COLUMN reply_effect_id DROP NOT NULL;
    ALTER TABLE switchboard.goal_inbox ADD CONSTRAINT ck_inbox_kind CHECK (
        (input_kind = 'owner_reply' AND delivery_ref IS NOT NULL AND reply_effect_id IS NOT NULL
            AND envelope_id IS NULL AND envelope_generation IS NULL)
        OR (input_kind = 'owner_non_reply' AND delivery_ref IS NULL AND reply_effect_id IS NULL
            AND envelope_id IS NULL AND envelope_generation IS NULL)
        OR (input_kind = 'condition_fire' AND delivery_ref IS NULL AND reply_effect_id IS NULL
            AND event_id IS NOT NULL AND envelope_id IS NOT NULL
            AND envelope_generation IS NOT NULL));
    ALTER TABLE switchboard.goal_inbox ADD CONSTRAINT ck_inbox_consumed CHECK (
        (consumed_at IS NULL AND consumed_epoch IS NULL AND NOT legacy_consumed)
        OR (consumed_at IS NOT NULL AND consumed_epoch IS NOT NULL AND NOT legacy_consumed)
        OR (consumed_at IS NOT NULL AND consumed_epoch IS NULL AND legacy_consumed
            AND input_kind = 'owner_reply'));
    ALTER TABLE switchboard.goal_inbox ADD CONSTRAINT fk_inbox_decision_key
        FOREIGN KEY (decision_id, goal_id, source_key)
        REFERENCES switchboard.admission_decisions(id, goal_id, source_key);
    ALTER TABLE switchboard.goal_inbox ADD CONSTRAINT fk_inbox_generation
        FOREIGN KEY (goal_id, envelope_id, envelope_generation)
        REFERENCES switchboard.wait_generations(goal_id, envelope_id, generation);
    ALTER TABLE switchboard.goal_inbox ADD CONSTRAINT fk_inbox_fire
        FOREIGN KEY (envelope_id, envelope_generation, event_id)
        REFERENCES switchboard.wake_intents(envelope_id, generation, fire_event_id);
    ALTER TABLE switchboard.goal_inbox ADD CONSTRAINT fk_inbox_consumed_epoch
        FOREIGN KEY (goal_id, consumed_epoch)
        REFERENCES switchboard.goal_epochs(goal_id, epoch);

    -- An inbox row is inserted only for a joined decision of the same kind, from the matching
    -- source; the legacy marker is the migration's alone. AFTER INSERT, so a role without
    -- insert authority is refused by RLS before this check can answer differently.
    CREATE FUNCTION switchboard.goal_inbox_discriminate()
    RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
    DECLARE
        d RECORD;
        expected TEXT;
    BEGIN
        IF NEW.legacy_consumed THEN
            RAISE EXCEPTION 'The legacy consumption marker is set by migration only';
        END IF;
        SELECT result, reason, input_kind, source_kind, source_ref, occurrence_key
            INTO d FROM switchboard.admission_decisions WHERE id = NEW.decision_id;
        expected := CASE NEW.input_kind
            WHEN 'owner_reply' THEN 'reply_joined'
            WHEN 'owner_non_reply' THEN 'input_joined'
            ELSE 'condition_joined' END;
        IF d.result IS DISTINCT FROM 'joined' OR d.input_kind IS DISTINCT FROM NEW.input_kind
           OR d.reason IS DISTINCT FROM expected THEN
            RAISE EXCEPTION 'Inbox input kind must match its joined admission decision';
        END IF;
        IF NEW.input_kind = 'condition_fire' AND (
               d.source_kind <> 'occurrence'
               OR d.source_ref <> 'condition:' || NEW.envelope_id::text
               OR d.occurrence_key IS DISTINCT FROM
                  'gen:' || NEW.envelope_generation::text || ':event:' || NEW.event_id::text) THEN
            RAISE EXCEPTION 'A condition fire is bound to its envelope generation and fire event';
        END IF;
        IF NEW.input_kind <> 'condition_fire' AND d.source_kind <> 'message' THEN
            RAISE EXCEPTION 'An owner input comes from a verified message';
        END IF;
        RETURN NULL;
    END $$;
    CREATE TRIGGER goal_inbox_discriminate AFTER INSERT ON switchboard.goal_inbox
        FOR EACH ROW EXECUTE FUNCTION switchboard.goal_inbox_discriminate();
"""

# sw_057's immutability function, with the waiting columns and the inbox tuple. CREATE OR REPLACE
# swaps the old consumed_at-only guard for the one-time tuple guard in a single step.
_IMMUTABLE = """
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
                    'admitted_label_sens','legacy_status','plan_version','wait_generation'])
               IS DISTINCT FROM
               (to_jsonb(OLD) - ARRAY['status','exposed','thread_id','updated_at',
                    'label_version','label_instr','label_data','label_sens',
                    'admitted_label_version','admitted_label_instr','admitted_label_data',
                    'admitted_label_sens','legacy_status','plan_version','wait_generation'])
               OR (OLD.exposed AND NOT NEW.exposed)
               OR (OLD.thread_id IS NOT NULL
                   AND NEW.thread_id IS DISTINCT FROM OLD.thread_id)
               OR (OLD.legacy_status IS NOT NULL
                   AND NEW.legacy_status IS DISTINCT FROM OLD.legacy_status)
               OR (OLD.plan_version IS NOT NULL
                   AND (NEW.plan_version IS NULL OR NEW.plan_version < OLD.plan_version))
               OR NEW.wait_generation < OLD.wait_generation
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
            -- Only consumption (once, as the (consumed_at, consumed_epoch) tuple) and label growth
            -- may change; a duplicate read of a consumed row rewrites nothing.
            IF (to_jsonb(NEW) - ARRAY['consumed_at','consumed_epoch','label_version',
                    'label_instr','label_data','label_sens'])
               IS DISTINCT FROM
               (to_jsonb(OLD) - ARRAY['consumed_at','consumed_epoch','label_version',
                    'label_instr','label_data','label_sens'])
               OR (OLD.consumed_at IS NOT NULL
                   AND (NEW.consumed_at IS DISTINCT FROM OLD.consumed_at
                        OR NEW.consumed_epoch IS DISTINCT FROM OLD.consumed_epoch))
               OR (OLD.consumed_at IS NULL AND NOT (
                       (NEW.consumed_at IS NULL AND NEW.consumed_epoch IS NULL)
                       OR (NEW.consumed_at IS NOT NULL AND NEW.consumed_epoch IS NOT NULL)))
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

# The inbox row's label: a reply carries the label of the effect it answers (sw_057); a verified
# non-reply owner message carries the owner label; a condition fire carries its envelope's label.
_INBOX_LABELS = """
    CREATE OR REPLACE FUNCTION switchboard.goal_label_inbox()
    RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
    DECLARE
        answered RECORD;
    BEGIN
        IF NEW.input_kind = 'condition_fire' THEN
            SELECT label_instr AS i, label_data AS d, label_sens AS c INTO answered
            FROM switchboard.wait_envelopes WHERE goal_id=NEW.goal_id AND id=NEW.envelope_id;
            NEW.label_version := 1;
            NEW.label_instr := switchboard.goal_label_union(NEW.label_instr, answered.i);
            NEW.label_data := switchboard.goal_label_union(NEW.label_data, answered.d);
            NEW.label_sens := switchboard.goal_label_union(NEW.label_sens, answered.c);
            RETURN NEW;
        END IF;
        SELECT label_instr AS i, label_data AS d, label_sens AS c INTO answered
        FROM switchboard.effects WHERE goal_id=NEW.goal_id AND id=NEW.reply_effect_id;
        NEW.label_version := 1;
        NEW.label_instr := switchboard.goal_label_union(
            switchboard.goal_label_union(NEW.label_instr, '{owner}'), answered.i);
        NEW.label_data := switchboard.goal_label_union(
            switchboard.goal_label_union(NEW.label_data, '{owner}'), answered.d);
        NEW.label_sens := switchboard.goal_label_union(NEW.label_sens, answered.c);
        RETURN NEW;
    END $$;
"""

_SETTLED_OLD = "SELECT * FROM switchboard.goal_label_start("
_SETTLED_INBOX_OLD = """        ELSIF TG_TABLE_NAME='goal_inbox' THEN
            SELECT switchboard.goal_label_union(label_instr, '{owner}') AS i,
                   switchboard.goal_label_union(label_data, '{owner}') AS d,
                   label_sens AS c INTO req
            FROM switchboard.effects WHERE goal_id=NEW.goal_id AND id=NEW.reply_effect_id;
"""
_SETTLED_INBOX_NEW = """        ELSIF TG_TABLE_NAME='goal_inbox' THEN
            IF NEW.input_kind = 'condition_fire' THEN
                SELECT label_instr AS i, label_data AS d, label_sens AS c INTO req
                FROM switchboard.wait_envelopes
                WHERE goal_id=NEW.goal_id AND id=NEW.envelope_id;
            ELSIF NEW.input_kind = 'owner_non_reply' THEN
                SELECT '{owner}'::text[] AS i, '{owner}'::text[] AS d, '{}'::text[] AS c INTO req;
            ELSE
                SELECT switchboard.goal_label_union(label_instr, '{owner}') AS i,
                       switchboard.goal_label_union(label_data, '{owner}') AS d,
                       label_sens AS c INTO req
                FROM switchboard.effects WHERE goal_id=NEW.goal_id AND id=NEW.reply_effect_id;
            END IF;
"""

_WAITING_GUARD = """
    CREATE OR REPLACE FUNCTION switchboard.goal_waiting_immutable()
    RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
    BEGIN
        IF TG_TABLE_NAME IN ('owner_threads','goal_plans','wait_generations') THEN
            RAISE EXCEPTION '% rows are append-only', TG_TABLE_NAME;
        ELSIF TG_TABLE_NAME = 'goal_epochs' THEN
            IF (to_jsonb(NEW) - ARRAY['state','updated_at'])
               IS DISTINCT FROM (to_jsonb(OLD) - ARRAY['state','updated_at']) THEN
                RAISE EXCEPTION 'An epoch record is immutable except for its state';
            END IF;
            IF NEW.state IS DISTINCT FROM OLD.state AND NOT (
                (OLD.state = 'pending' AND NEW.state IN ('launching','failed'))
                OR (OLD.state = 'launching' AND NEW.state IN ('pending','running','finished','failed'))
                OR (OLD.state = 'running' AND NEW.state IN ('finished','failed'))) THEN
                RAISE EXCEPTION 'Illegal epoch transition % -> %', OLD.state, NEW.state;
            END IF;
        ELSIF TG_TABLE_NAME = 'wait_envelopes' THEN
            IF (to_jsonb(NEW) - ARRAY['state','consumed_wakes','generation','updated_at'])
               IS DISTINCT FROM
               (to_jsonb(OLD) - ARRAY['state','consumed_wakes','generation','updated_at']) THEN
                RAISE EXCEPTION 'A wait envelope is immutable except for its state, '
                    'generation and wake count';
            END IF;
            IF NEW.state IS DISTINCT FROM OLD.state AND NOT (
                (OLD.state = 'open' AND NEW.state IN ('fired','expired','cancelled'))
                OR (OLD.state = 'fired' AND NEW.state IN ('open','expired','cancelled'))) THEN
                RAISE EXCEPTION 'Illegal envelope transition % -> %', OLD.state, NEW.state;
            END IF;
            -- One wake per fire; one generation per rearm; neither otherwise moves.
            IF NEW.consumed_wakes <> OLD.consumed_wakes + (
                   CASE WHEN OLD.state = 'open' AND NEW.state = 'fired' THEN 1 ELSE 0 END)
               OR NEW.generation <> OLD.generation + (
                   CASE WHEN OLD.state = 'fired' AND NEW.state = 'open' THEN 1 ELSE 0 END) THEN
                RAISE EXCEPTION 'An envelope counts one wake per fire and one generation per rearm';
            END IF;
        ELSIF TG_TABLE_NAME = 'wake_intents' THEN
            IF (to_jsonb(NEW) - ARRAY['state','cancelled_reason','claimed_by',
                    'claim_expires_at','attempts','updated_at'])
               IS DISTINCT FROM (to_jsonb(OLD) - ARRAY['state','cancelled_reason','claimed_by',
                    'claim_expires_at','attempts','updated_at']) THEN
                RAISE EXCEPTION 'A wake intent is immutable except for its launch state';
            END IF;
            IF NEW.state IS DISTINCT FROM OLD.state AND NOT (
                (OLD.state IN ('pending','failed') AND NEW.state = 'claimed')
                OR (OLD.state = 'claimed' AND NEW.state IN ('launched','failed','pending'))
                -- Stop, expiry or a superseding rearm cancels an admitted wake that has not
                -- launched; cancelled is terminal and stays counted in uq_wake_successor.
                OR (OLD.state IN ('pending','failed','claimed') AND NEW.state = 'cancelled')) THEN
                RAISE EXCEPTION 'Illegal wake intent transition % -> %', OLD.state, NEW.state;
            END IF;
            IF NEW.attempts < OLD.attempts THEN
                RAISE EXCEPTION 'A wake intent never forgets an attempt';
            END IF;
        END IF;
        RETURN NEW;
    END $$;
    -- Until the runner records epochs itself, a lead invocation derives its epoch record (and
    -- keeps its state in step), so goal_epochs is complete for every lead that ever ran. An
    -- epoch already recorded (a wake's) is kept as it is.
    CREATE OR REPLACE FUNCTION switchboard.goal_epoch_track()
    RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
    DECLARE
        prior RECORD;
        target TEXT;
    BEGIN
        IF NEW.depth <> 0 THEN
            RETURN NULL;
        END IF;
        target := CASE NEW.status WHEN 'launching' THEN 'launching' WHEN 'running' THEN 'running'
            WHEN 'returned' THEN 'finished' WHEN 'escalated' THEN 'finished' ELSE 'failed' END;
        IF TG_OP = 'INSERT' THEN
            SELECT id, status INTO prior FROM switchboard.invocations
            WHERE goal_id = NEW.goal_id AND depth = 0 AND lead_epoch < NEW.lead_epoch
            ORDER BY lead_epoch DESC, created_at DESC LIMIT 1;
            INSERT INTO switchboard.goal_epochs(goal_id, epoch, predecessor_invocation_id, reason,
                    label_version, label_instr, label_data, label_sens, grant_digest, state)
                VALUES (NEW.goal_id, NEW.lead_epoch, prior.id,
                    CASE WHEN prior.id IS NULL THEN 'initial'
                         WHEN prior.status = 'escalated' THEN 'escalation'
                         ELSE 'recovery' END,
                    NEW.label_version, NEW.label_instr, NEW.label_data, NEW.label_sens,
                    NEW.grant_digest, 'pending')
                ON CONFLICT (goal_id, epoch) DO NOTHING;
        END IF;
        UPDATE switchboard.goal_epochs SET state = 'launching', updated_at = clock_timestamp()
        WHERE goal_id = NEW.goal_id AND epoch = NEW.lead_epoch AND state = 'pending'
            AND target IN ('running','finished');
        UPDATE switchboard.goal_epochs SET state = target, updated_at = clock_timestamp()
        WHERE goal_id = NEW.goal_id AND epoch = NEW.lead_epoch AND state <> target AND (
            (state = 'pending' AND target IN ('launching','failed'))
            OR (state = 'launching' AND target IN ('running','finished','failed'))
            OR (state = 'running' AND target IN ('finished','failed')));
        RETURN NULL;
    END $$;
    CREATE OR REPLACE FUNCTION switchboard.goal_waiting_durable()
    RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
    BEGIN
        RAISE EXCEPTION 'Waiting history is durable' USING ERRCODE = 'insufficient_privilege';
    END $$;
"""


def _runtime_access() -> str:
    statements = []
    for table in _NEW_TABLES:
        qualified = f"switchboard.{table}"
        statements.append(f"ALTER TABLE {qualified} ENABLE ROW LEVEL SECURITY")
        statements.append(f"REVOKE ALL ON {qualified} FROM PUBLIC")
        privileges = "SELECT, INSERT, UPDATE" if table in _MUTABLE else "SELECT, INSERT"
        statements.append(f"GRANT {privileges} ON {qualified} TO {_RUNTIME}")
        guard = "current_user='butler_switchboard_rw'"
        statements.append(
            f"CREATE POLICY waiting_read ON {qualified} FOR SELECT TO {_RUNTIME} USING ({guard})"
        )
        statements.append(
            f"CREATE POLICY waiting_insert ON {qualified} FOR INSERT TO {_RUNTIME} "
            f"WITH CHECK ({guard})"
        )
        update = f"USING ({guard}) WITH CHECK ({guard})" if table in _MUTABLE else "USING (false)"
        if table not in _MUTABLE:
            update += " WITH CHECK (false)"
        statements.append(
            f"CREATE POLICY waiting_update ON {qualified} FOR UPDATE TO {_RUNTIME} {update}"
        )
        statements.append(
            f"CREATE POLICY waiting_delete ON {qualified} FOR DELETE TO {_RUNTIME} USING (false)"
        )
        statements.append(
            f"CREATE TRIGGER goal_waiting_immutable BEFORE UPDATE ON {qualified} "
            "FOR EACH ROW EXECUTE FUNCTION switchboard.goal_waiting_immutable()"
        )
        statements.append(
            f"CREATE TRIGGER goal_waiting_durable BEFORE DELETE OR TRUNCATE ON {qualified} "
            "FOR EACH STATEMENT EXECUTE FUNCTION switchboard.goal_waiting_durable()"
        )
    role_guard = f"""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname IN (
                'butler_chronicler_rw','butler_concierge_rw','butler_education_rw',
                'butler_finance_rw','butler_general_rw','butler_health_rw','butler_home_rw',
                'butler_lifestyle_rw','butler_messenger_rw','butler_qa_rw',
                'butler_relationship_rw','{_RUNTIME}','butler_calendar_rw','connector_writer')
                AND (rolsuper OR rolbypassrls OR pg_has_role(oid,
                    (SELECT relowner FROM pg_class WHERE
                     oid='switchboard.wait_envelopes'::regclass),'MEMBER'))) THEN
                RAISE EXCEPTION 'Runtime may not bypass or own the waiting ledger';
            END IF;
        END $$"""
    return ";\n".join([*statements, role_guard])


def upgrade() -> None:
    for table in ("goals", "invocations", "effects", "admission_decisions", "goal_inbox"):
        op.execute(f"LOCK TABLE switchboard.{table} IN ACCESS EXCLUSIVE MODE")
    op.execute(_TABLES_SQL)
    op.execute(_BACKFILL)
    op.execute(_GOALS_SQL)
    op.execute(_DECISIONS_SQL)
    op.execute(_INBOX_SQL)
    op.execute(_WAITING_GUARD)
    op.execute(_IMMUTABLE)
    op.execute(_INBOX_LABELS)
    # The sw_057 settled-label check, restated with the discriminated inbox branch.
    op.execute(f"""
        DO $$
        DECLARE
            body TEXT := pg_get_functiondef('switchboard.goal_label_settled()'::regprocedure);
            old_branch TEXT := $old${_SETTLED_INBOX_OLD}$old$;
            new_branch TEXT := $new${_SETTLED_INBOX_NEW}$new$;
        BEGIN
            IF position(old_branch IN body) = 0 THEN
                RAISE EXCEPTION 'goal_label_settled() is not the sw_057 definition';
            END IF;
            EXECUTE replace(body, old_branch, new_branch);
        END $$
    """)
    op.execute(_runtime_access())
    op.execute("""
        CREATE TRIGGER goal_epoch_track AFTER INSERT OR UPDATE OF status
            ON switchboard.invocations FOR EACH ROW
            EXECUTE FUNCTION switchboard.goal_epoch_track()
    """)


def downgrade() -> None:
    for table in ("goals", "goal_inbox", "admission_decisions", *_NEW_TABLES):
        op.execute(f"LOCK TABLE switchboard.{table} IN ACCESS EXCLUSIVE MODE")
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM switchboard.wait_envelopes)
               OR EXISTS (SELECT 1 FROM switchboard.wake_intents)
               OR EXISTS (SELECT 1 FROM switchboard.goal_plans)
               OR EXISTS (SELECT 1 FROM switchboard.goal_inbox WHERE input_kind <> 'owner_reply')
               OR EXISTS (SELECT 1 FROM switchboard.admission_decisions
                          WHERE reason IN ('input_joined','condition_joined'))
               OR EXISTS (SELECT 1 FROM switchboard.goals
                          WHERE plan_version IS NOT NULL OR legacy_status IS NOT NULL
                             OR wait_generation <> 0 OR status = 'waiting') THEN
                RAISE EXCEPTION 'Cannot discard durable waiting records';
            END IF;
            IF EXISTS (SELECT 1 FROM switchboard.goal_inbox WHERE legacy_consumed) THEN
                RAISE EXCEPTION 'Cannot discard the legacy consumption markers';
            END IF;
        END $$;
    """)
    op.execute("""
        DROP TRIGGER IF EXISTS goal_epoch_track ON switchboard.invocations;
        DROP FUNCTION IF EXISTS switchboard.goal_epoch_track();
        DROP TRIGGER IF EXISTS goal_inbox_discriminate ON switchboard.goal_inbox;
        DROP FUNCTION IF EXISTS switchboard.goal_inbox_discriminate();
        DROP TRIGGER IF EXISTS goal_decision_default_kind ON switchboard.admission_decisions;
        DROP FUNCTION IF EXISTS switchboard.goal_decision_default_kind();
        ALTER TABLE switchboard.goal_inbox
            DROP CONSTRAINT fk_inbox_consumed_epoch, DROP CONSTRAINT fk_inbox_fire,
            DROP CONSTRAINT fk_inbox_generation, DROP CONSTRAINT fk_inbox_decision_key,
            DROP CONSTRAINT ck_inbox_consumed, DROP CONSTRAINT ck_inbox_kind;
    """)
    op.execute(_SW057_FUNCTIONS)
    op.execute(
        """
        ALTER TABLE switchboard.goal_inbox
            DROP COLUMN legacy_consumed, DROP COLUMN consumed_epoch,
            DROP COLUMN envelope_generation, DROP COLUMN envelope_id,
            DROP COLUMN event_id, DROP COLUMN input_kind;
        ALTER TABLE switchboard.goal_inbox ALTER COLUMN delivery_ref SET NOT NULL;
        ALTER TABLE switchboard.goal_inbox ALTER COLUMN reply_effect_id SET NOT NULL;
        DROP INDEX switchboard.uq_admission_decision_goal_source;
        ALTER TABLE switchboard.admission_decisions
            DROP CONSTRAINT ck_decision_input_kind,
            DROP CONSTRAINT admission_decisions_reason_check;
        ALTER TABLE switchboard.admission_decisions DROP COLUMN input_kind;
        ALTER TABLE switchboard.admission_decisions ADD CONSTRAINT admission_decisions_reason_check
            CHECK (reason IN ({reasons}));
        ALTER TABLE switchboard.goals
            DROP CONSTRAINT fk_goal_plan, DROP CONSTRAINT fk_goal_thread,
            DROP CONSTRAINT goals_status_check;
        ALTER TABLE switchboard.goals ADD CONSTRAINT goals_status_check CHECK (status IN (
            'admitted','running','settling','completed','partial','failed','stopped',
            'budget_exhausted','awaiting_approval','recovering'));
        ALTER TABLE switchboard.goals
            DROP COLUMN wait_generation, DROP COLUMN plan_version, DROP COLUMN legacy_status;
        DROP TABLE switchboard.wake_intents, switchboard.wait_generations,
            switchboard.wait_envelopes, switchboard.goal_plans, switchboard.goal_epochs,
            switchboard.owner_threads;
        DROP FUNCTION switchboard.goal_waiting_immutable();
        DROP FUNCTION switchboard.goal_waiting_durable();
    """.replace("{reasons}", _quoted(_REASONS))
    )
