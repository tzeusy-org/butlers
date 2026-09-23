"""Separate registry observation, operator policy, and boot authority.

Revision ID: sw_035
Revises: sw_034
Create Date: 2026-09-23 00:00:00.000000

The legacy registry remains the routing projection until the L3 cutover.  The
new table is deliberately retained on downgrade: discarding an owner hold or a
committed boot epoch would make rollback an authority escalation.
"""

from __future__ import annotations

from alembic import op

revision = "sw_035"
down_revision = "sw_034"
branch_labels = None
depends_on = None

_RUNTIME_ROLES = (
    "butler_chronicler_rw",
    "butler_concierge_rw",
    "butler_education_rw",
    "butler_finance_rw",
    "butler_general_rw",
    "butler_health_rw",
    "butler_home_rw",
    "butler_lifestyle_rw",
    "butler_messenger_rw",
    "butler_qa_rw",
    "butler_relationship_rw",
    "butler_switchboard_rw",
    "butler_travel_rw",
)


def _execute(statement: str) -> None:
    """Bind the Switchboard-owned objects to Alembic's selected schema.

    Production uses `switchboard`; several migration fixtures deliberately
    install this chain in `public`.  The operation names in `public` stay
    fixed while their table and trigger targets follow the chain schema.
    """
    bind = op.get_bind()
    schema = (
        str(bind.exec_driver_sql("SELECT current_schema()").scalar_one())
        if hasattr(bind, "exec_driver_sql")
        else "switchboard"
    )
    quoted_schema = '"' + schema.replace('"', '""') + '"'
    statement = statement.replace("switchboard.", f"{quoted_schema}.")
    statement = statement.replace(
        "SET search_path = pg_catalog, switchboard, pg_temp",
        f"SET search_path = pg_catalog, {quoted_schema}, pg_temp",
    )
    statement = statement.replace(
        "SET search_path = pg_catalog, switchboard\n",
        f"SET search_path = pg_catalog, {quoted_schema}\n",
    )
    op.execute(statement)


def upgrade() -> None:
    _execute("""
        CREATE TABLE IF NOT EXISTS switchboard.butler_registry_control_plane (
            name TEXT PRIMARY KEY,
            policy_state TEXT NOT NULL DEFAULT 'active'
                CHECK (policy_state IN ('active', 'paused', 'quarantined', 'review_required')),
            policy_actor TEXT,
            policy_reason TEXT,
            policy_changed_at TIMESTAMPTZ,
            policy_provenance TEXT NOT NULL DEFAULT 'none'
                CHECK (policy_provenance IN (
                    'none', 'legacy_ttl', 'legacy_operator', 'legacy_ambiguous', 'operator'
                )),
            legacy_evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
            observed_state TEXT NOT NULL DEFAULT 'observer_unknown'
                CHECK (observed_state IN (
                    'healthy', 'stale', 'unavailable', 'observer_unknown'
                )),
            last_probe_at TIMESTAMPTZ,
            healthy_observed_at TIMESTAMPTZ,
            observed_boot_epoch BIGINT,
            probe_failure_class TEXT,
            route_compatible BOOLEAN,
            accepting_routes BOOLEAN,
            boot_instance_id UUID,
            boot_epoch BIGINT NOT NULL DEFAULT 0 CHECK (boot_epoch >= 0),
            probe_sequence BIGINT NOT NULL DEFAULT 0 CHECK (probe_sequence >= 0),
            recorded_probe_sequence BIGINT NOT NULL DEFAULT 0
                CHECK (recorded_probe_sequence >= 0),
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            CHECK (recorded_probe_sequence <= probe_sequence)
        )
    """)

    # Only a matching quarantine-transition receipt proves provenance.  Text in
    # quarantine_reason alone is not an owner assertion, and a missing or
    # mismatched receipt stays review_required.
    _execute("""
        INSERT INTO switchboard.butler_registry_control_plane (
            name, policy_state, policy_actor, policy_reason, policy_changed_at,
            policy_provenance, legacy_evidence, observed_state
        )
        SELECT r.name,
               CASE
                   WHEN r.eligibility_state = 'quarantined'
                        OR r.quarantined_at IS NOT NULL
                   THEN CASE WHEN l.new_state = 'quarantined'
                                      AND l.reason = 'operator_action'
                                      AND l.observed_at = r.quarantined_at
                             THEN 'quarantined'
                             WHEN l.new_state = 'quarantined'
                                      AND l.reason = 'liveness_ttl_2x_expired'
                                      AND l.observed_at = r.quarantined_at
                             THEN 'active'
                             ELSE 'review_required' END
                   WHEN r.eligibility_state = 'stale'
                   THEN CASE WHEN l.new_state = 'stale'
                                      AND l.reason = 'operator_action'
                                      AND l.observed_at = r.eligibility_updated_at
                             THEN 'paused'
                             WHEN l.new_state = 'stale'
                                      AND l.reason = 'liveness_ttl_expired'
                                      AND l.observed_at = r.eligibility_updated_at
                             THEN 'active'
                             ELSE 'review_required' END
                   ELSE 'active'
               END,
               CASE WHEN l.reason = 'operator_action'
                              AND l.new_state = r.eligibility_state
                              AND l.observed_at = CASE
                                  WHEN r.eligibility_state = 'stale'
                                  THEN r.eligibility_updated_at
                                  ELSE r.quarantined_at END
                    THEN 'owner' ELSE NULL END,
               CASE WHEN r.eligibility_state IN ('quarantined', 'stale')
                              OR r.quarantined_at IS NOT NULL
                    THEN 'legacy_ineligible' ELSE NULL END,
               CASE WHEN r.eligibility_state IN ('quarantined', 'stale')
                              OR r.quarantined_at IS NOT NULL
                    THEN r.eligibility_updated_at ELSE NULL END,
               CASE
                   WHEN r.eligibility_state = 'quarantined'
                        OR r.quarantined_at IS NOT NULL
                   THEN CASE WHEN l.new_state = 'quarantined'
                                      AND l.reason = 'operator_action'
                                      AND l.observed_at = r.quarantined_at
                             THEN 'legacy_operator'
                             WHEN l.new_state = 'quarantined'
                                      AND l.reason = 'liveness_ttl_2x_expired'
                                      AND l.observed_at = r.quarantined_at
                             THEN 'legacy_ttl'
                             ELSE 'legacy_ambiguous' END
                   WHEN r.eligibility_state = 'stale'
                   THEN CASE WHEN l.new_state = 'stale'
                                      AND l.reason = 'operator_action'
                                      AND l.observed_at = r.eligibility_updated_at
                             THEN 'legacy_operator'
                             WHEN l.new_state = 'stale'
                                      AND l.reason = 'liveness_ttl_expired'
                                      AND l.observed_at = r.eligibility_updated_at
                             THEN 'legacy_ttl'
                             ELSE 'legacy_ambiguous' END
                   ELSE 'none'
               END,
               jsonb_build_object(
                   'eligibility_state', r.eligibility_state,
                   'quarantined_at', r.quarantined_at,
                   'quarantine_reason', r.quarantine_reason,
                   'eligibility_updated_at', r.eligibility_updated_at,
                   'transition_id', l.id,
                   'transition_state', l.new_state,
                   'transition_reason', l.reason,
                   'transition_at', l.observed_at
               ),
               CASE WHEN (r.eligibility_state = 'stale'
                             AND l.new_state = 'stale'
                             AND l.reason = 'liveness_ttl_expired'
                             AND l.observed_at = r.eligibility_updated_at)
                         OR (l.new_state = 'quarantined'
                             AND l.reason = 'liveness_ttl_2x_expired'
                             AND l.observed_at = r.quarantined_at)
                    THEN 'stale' ELSE 'observer_unknown' END
        FROM switchboard.butler_registry AS r
        LEFT JOIN LATERAL (
            SELECT id, new_state, reason, observed_at
            FROM switchboard.butler_registry_eligibility_log
            WHERE butler_name = r.name
              AND new_state IN ('quarantined', 'stale')
            ORDER BY observed_at DESC, id DESC
            LIMIT 1
        ) AS l ON TRUE
        ON CONFLICT (name) DO NOTHING
    """)

    # Bootstrap can re-grant table DML.  RLS remains the durable runtime fence:
    # only the migration/dashboard table owner and the fixed definer operations
    # below can mutate these facts.  Non-FORCE preserves pg_dump under the
    # migration login (the same pattern as core_201).
    _execute("REVOKE ALL ON switchboard.butler_registry_control_plane FROM PUBLIC")
    _execute("""
        GRANT SELECT ON switchboard.butler_registry_control_plane
        TO butler_switchboard_rw
    """)
    _execute("ALTER TABLE switchboard.butler_registry_control_plane ENABLE ROW LEVEL SECURITY")
    _execute("""
        DROP POLICY IF EXISTS registry_control_read ON
            switchboard.butler_registry_control_plane
    """)
    _execute("""
        CREATE POLICY registry_control_read
        ON switchboard.butler_registry_control_plane
        FOR SELECT TO butler_switchboard_rw
        USING (true)
    """)

    _execute("""
        CREATE OR REPLACE FUNCTION switchboard.preserve_registry_policy()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, switchboard
        AS $fn$
        DECLARE
            v_policy text;
            v_previous_quarantined_at timestamptz;
            v_previous_reason text;
        BEGIN
            SELECT policy_state INTO v_policy
            FROM switchboard.butler_registry_control_plane
            WHERE name = NEW.name;
            IF v_policy IN ('paused', 'quarantined', 'review_required') THEN
                IF TG_OP = 'UPDATE' THEN
                    v_previous_quarantined_at := OLD.quarantined_at;
                    v_previous_reason := OLD.quarantine_reason;
                END IF;
                NEW.eligibility_state := 'quarantined';
                NEW.quarantined_at := COALESCE(
                    v_previous_quarantined_at, NEW.quarantined_at, clock_timestamp()
                );
                NEW.quarantine_reason := COALESCE(
                    v_previous_reason, NEW.quarantine_reason, 'protected_policy:' || v_policy
                );
            END IF;
            RETURN NEW;
        END;
        $fn$
    """)
    _execute("""
        DROP TRIGGER IF EXISTS trg_preserve_registry_policy
        ON switchboard.butler_registry
    """)
    _execute("""
        CREATE TRIGGER trg_preserve_registry_policy
        BEFORE INSERT OR UPDATE ON switchboard.butler_registry
        FOR EACH ROW EXECUTE FUNCTION switchboard.preserve_registry_policy()
    """)
    _execute("""
        CREATE OR REPLACE FUNCTION switchboard.ensure_registry_control()
        RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, switchboard
        AS $fn$
        BEGIN
            INSERT INTO switchboard.butler_registry_control_plane (name)
            VALUES (NEW.name)
            ON CONFLICT (name) DO NOTHING;
            RETURN NEW;
        END;
        $fn$
    """)
    _execute("""
        DROP TRIGGER IF EXISTS trg_ensure_registry_control
        ON switchboard.butler_registry
    """)
    _execute("""
        CREATE TRIGGER trg_ensure_registry_control
        AFTER INSERT ON switchboard.butler_registry
        FOR EACH ROW EXECUTE FUNCTION switchboard.ensure_registry_control()
    """)
    # A legacy manually paused or ambiguous stale row must be as restrictive
    # as its new policy, including callers that use allow_stale before L3.
    _execute("""
        UPDATE switchboard.butler_registry AS r
           SET eligibility_state = 'quarantined',
               quarantined_at = COALESCE(r.quarantined_at, clock_timestamp()),
               quarantine_reason = COALESCE(
                   r.quarantine_reason, 'protected_policy:' || c.policy_state
               )
          FROM switchboard.butler_registry_control_plane AS c
         WHERE c.name = r.name
           AND c.policy_state IN ('paused', 'quarantined', 'review_required')
           AND r.eligibility_state <> 'quarantined'
    """)

    _execute("""
        CREATE OR REPLACE FUNCTION public.register_butler_boot(
            p_name text, p_instance uuid
        ) RETURNS bigint
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, switchboard, pg_temp
        AS $fn$
        DECLARE v_epoch bigint;
        BEGIN
            IF p_instance IS NULL OR p_name IS NULL
               OR current_setting('role', true) IS DISTINCT FROM
                    'butler_' || p_name || '_rw' THEN
                RAISE EXCEPTION 'boot registration requires the matching runtime role'
                    USING ERRCODE = '42501';
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM switchboard.butler_registry WHERE name = p_name
            ) THEN
                RAISE EXCEPTION 'unknown roster registration'
                    USING ERRCODE = '22023';
            END IF;
            INSERT INTO switchboard.butler_registry_control_plane (name)
            VALUES (p_name) ON CONFLICT (name) DO NOTHING;
            UPDATE switchboard.butler_registry_control_plane
               SET boot_epoch = boot_epoch + 1,
                   boot_instance_id = p_instance,
                   observed_state = 'observer_unknown',
                   observed_boot_epoch = NULL,
                   route_compatible = NULL,
                   accepting_routes = NULL,
                   probe_failure_class = NULL,
                   updated_at = clock_timestamp()
             WHERE name = p_name
             RETURNING boot_epoch INTO v_epoch;
            RETURN v_epoch;
        END;
        $fn$
    """)

    _execute("""
        CREATE OR REPLACE FUNCTION public.reserve_butler_probe(p_name text)
        RETURNS TABLE(boot_epoch bigint, probe_sequence bigint)
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, switchboard, pg_temp
        AS $fn$
        BEGIN
            IF current_setting('role', true) IS DISTINCT FROM
                    'butler_switchboard_rw'
               AND NOT (
                    current_setting('role', true) = 'none'
                    AND session_user = current_user
               ) THEN
                RAISE EXCEPTION 'probe reservation requires a control-plane role'
                    USING ERRCODE = '42501';
            END IF;
            RETURN QUERY
            UPDATE switchboard.butler_registry_control_plane AS c
               SET probe_sequence = c.probe_sequence + 1,
                   updated_at = clock_timestamp()
             WHERE c.name = p_name AND c.boot_epoch > 0
             RETURNING c.boot_epoch, c.probe_sequence;
        END;
        $fn$
    """)

    _execute("""
        CREATE OR REPLACE FUNCTION public.record_butler_probe(
            p_name text, p_epoch bigint, p_sequence bigint,
            p_healthy boolean, p_compatible boolean, p_accepting boolean,
            p_failure_class text
        ) RETURNS boolean
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, switchboard, pg_temp
        AS $fn$
        DECLARE v_updated boolean;
        BEGIN
            IF current_setting('role', true) IS DISTINCT FROM
                    'butler_switchboard_rw'
               AND NOT (
                    current_setting('role', true) = 'none'
                    AND session_user = current_user
               ) THEN
                RAISE EXCEPTION 'probe recording requires a control-plane role'
                    USING ERRCODE = '42501';
            END IF;
            IF p_healthy IS NULL OR p_epoch IS NULL OR p_sequence IS NULL
               OR (p_healthy AND (p_compatible IS NULL OR p_accepting IS NULL)) THEN
                RAISE EXCEPTION 'probe outcome and fence are required'
                    USING ERRCODE = '22023';
            END IF;
            UPDATE switchboard.butler_registry_control_plane AS c
               SET recorded_probe_sequence = p_sequence,
                   last_probe_at = clock_timestamp(),
                   healthy_observed_at = CASE
                       WHEN p_healthy THEN clock_timestamp()
                       ELSE c.healthy_observed_at END,
                   observed_boot_epoch = p_epoch,
                   observed_state = CASE
                       WHEN p_healthy THEN 'healthy' ELSE 'unavailable' END,
                   route_compatible = CASE WHEN p_healthy THEN p_compatible ELSE NULL END,
                   accepting_routes = CASE WHEN p_healthy THEN p_accepting ELSE NULL END,
                   probe_failure_class = CASE
                       WHEN p_healthy THEN NULL
                       WHEN p_failure_class IN (
                           'timeout', 'connection', 'invalid_identity',
                           'invalid_contract', 'not_accepting'
                       ) THEN p_failure_class
                       ELSE 'observer_error' END,
                   updated_at = clock_timestamp()
             WHERE c.name = p_name
               AND c.boot_epoch = p_epoch
               AND c.boot_epoch > 0
               AND c.probe_sequence = p_sequence
               AND c.recorded_probe_sequence < p_sequence;
            v_updated := FOUND;
            RETURN v_updated;
        END;
        $fn$
    """)

    _execute("""
        CREATE OR REPLACE FUNCTION public.set_butler_registry_policy(
            p_name text, p_policy text
        ) RETURNS text
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, switchboard, pg_temp
        AS $fn$
        DECLARE v_legacy text;
        BEGIN
            IF current_setting('role', true) IS DISTINCT FROM 'none'
               OR session_user IS DISTINCT FROM current_user THEN
                RAISE EXCEPTION 'operator policy requires the dashboard owner boundary'
                    USING ERRCODE = '42501';
            END IF;
            IF p_policy NOT IN ('active', 'paused', 'quarantined', 'review_required')
               OR p_policy IS NULL THEN
                RAISE EXCEPTION 'invalid registry policy' USING ERRCODE = '22023';
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM switchboard.butler_registry WHERE name = p_name
            ) THEN
                RAISE EXCEPTION 'unknown registry target' USING ERRCODE = '22023';
            END IF;
            INSERT INTO switchboard.butler_registry_control_plane (name)
            VALUES (p_name) ON CONFLICT (name) DO NOTHING;
            UPDATE switchboard.butler_registry_control_plane
               SET policy_state = p_policy,
                   policy_actor = 'owner',
                   policy_reason = 'operator_action',
                   policy_changed_at = clock_timestamp(),
                   policy_provenance = 'operator',
                   updated_at = clock_timestamp()
             WHERE name = p_name;
            v_legacy := CASE WHEN p_policy = 'active' THEN 'active'
                             ELSE 'quarantined' END;
            UPDATE switchboard.butler_registry
               SET eligibility_state = v_legacy,
                   eligibility_updated_at = clock_timestamp(),
                   quarantined_at = CASE WHEN v_legacy = 'active'
                                         THEN NULL ELSE clock_timestamp() END,
                   quarantine_reason = CASE WHEN v_legacy = 'active' THEN NULL
                                            ELSE 'operator_' || p_policy END
             WHERE name = p_name;
            RETURN v_legacy;
        END;
        $fn$
    """)

    for signature in (
        "public.register_butler_boot(text, uuid)",
        "public.reserve_butler_probe(text)",
        "public.record_butler_probe(text, bigint, bigint, boolean, boolean, boolean, text)",
        "public.set_butler_registry_policy(text, text)",
    ):
        _execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
    for role in _RUNTIME_ROLES:
        _execute(f"""
            DO $grant$
            BEGIN
                IF to_regrole('{role}') IS NOT NULL THEN
                    GRANT EXECUTE ON FUNCTION public.register_butler_boot(text, uuid)
                    TO {role};
                END IF;
            END
            $grant$
        """)
    _execute("""
        GRANT EXECUTE ON FUNCTION public.reserve_butler_probe(text)
        TO butler_switchboard_rw
    """)
    _execute("""
        GRANT EXECUTE ON FUNCTION public.record_butler_probe(
            text, bigint, bigint, boolean, boolean, boolean, text
        ) TO butler_switchboard_rw
    """)
    # No runtime role may invoke set_butler_registry_policy; the authenticated
    # dashboard's migration login owns it and is also checked inside the body.


def downgrade() -> None:
    # A downgrade changes code, not authority history.  Retain the new table,
    # triggers, functions, grants and RLS fence so old code cannot clear an
    # owner quarantine or resurrect a previously fenced boot epoch.
    pass
