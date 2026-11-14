"""Vendor-door registry, authority functions and the registrable-domain table (bu-jv1moa.40).

Revision ID: core_336
Revises: core_335
Create Date: 2026-11-14 00:00:00.000000

``public.vendor_doors`` holds owner-confirmed vendor doors and the inert proposals around them
(``openspec/changes/specify-vendor-doors``). Nothing here is reachable by a model and nothing
resolves a door for delivery: this revision is the schema and authority layer only, dark behind
``VENDOR_DOORS_LIVE``.

* No role holds INSERT, UPDATE, DELETE or TRUNCATE on the table, the migration owner included at
  run time, and a trigger refuses DELETE and TRUNCATE outright. Every write is a SECURITY DEFINER
  function owned by the migration role with a pinned ``search_path``.
* ``vendor_door_propose`` (Switchboard and Finance) derives the proposal's origin from
  ``current_setting('role', true)`` (``email`` for Switchboard, ``extracted`` for Finance, any other
  value 42501), reads an email proposal's sender address and stored verdict from
  ``public.ingestion_events`` by event id, and takes neither as an argument. The verified tier needs
  an ``authenticated`` verdict AND the sender's registrable domain to equal the link host's.
* ``vendor_door_create``, ``_confirm``, ``_revoke`` and ``_reject`` run only under the NOLOGIN
  capability role ``dashboard_vendor_doors`` (``scripts/init-db.sql``), checked the same way. The
  shared login can ``SET ROLE`` to any of them: the same trust boundary as core_193.
* Each function's ``REVOKE ALL ... FROM PUBLIC`` is a standalone statement, never inside the
  tolerant grant block; ``GRANT EXECUTE`` then names exactly one role set.
* The resolver view ``public.vendor_doors_current`` exposes confirmed rows of the live owner only.
* ``public.public_suffix_rules`` and ``public.registrable_domain`` load the vendored Public Suffix
  List snapshot; ``butlers.core.registrable_domain`` is the Python twin and a parity test pins them.

The core chain runs once per butler schema over these shared objects, so every statement is
idempotent under an advisory lock and only the last schema still at core_336 drops them. The
downgrade refuses while any door row exists.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "core_336"
down_revision = "core_335"
branch_labels = None
depends_on = None

_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_336:vendor-doors', 0))"
_ROLLBACK_REFUSAL = "core_336 downgrade refused: vendor doors exist"
_DASH = "dashboard_vendor_doors"

_TABLES = """
CREATE TABLE IF NOT EXISTS public.public_suffix_rules (
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('normal', 'wildcard', 'exception')),
    PRIMARY KEY (kind, name)
);

CREATE TABLE IF NOT EXISTS public.vendor_doors (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    version            INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    owner_entity_id    UUID NOT NULL REFERENCES public.entities (id),
    kind               TEXT NOT NULL CHECK (kind IN ('cancel', 'manage', 'return', 'check_in')),
    vendor_entity_id   UUID REFERENCES public.entities (id),
    host               TEXT NOT NULL CHECK (host ~ '^[a-z0-9.-]{3,253}$'),
    template           TEXT NOT NULL CHECK (char_length(template) <= 300),
    slot_types         JSONB NOT NULL DEFAULT '{}'::jsonb
                           CHECK (jsonb_typeof(slot_types) = 'object'),
    status             TEXT NOT NULL
                           CHECK (status IN ('proposed', 'confirmed', 'revoked', 'rejected')),
    origin             TEXT NOT NULL
                           CHECK (origin IN ('dashboard', 'legacy_owner_set', 'extracted',
                                             'email')),
    tier               TEXT NOT NULL
                           CHECK (tier IN ('verified', 'unverified', 'extracted', 'exempt')),
    ingestion_event_id UUID,
    sender_verdict     TEXT CHECK (sender_verdict IN ('authenticated', 'unverified', 'failed')),
    sender_domain      TEXT,
    lineage            JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(lineage) = 'object'),
    supersedes_id      UUID REFERENCES public.vendor_doors (id),
    expires_at         TIMESTAMPTZ,
    confirmed_at       TIMESTAMPTZ,
    confirmed_by_session TEXT,
    revoked_at         TIMESTAMPTZ,
    rejected_at        TIMESTAMPTZ,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT vendor_doors_expiry_while_pending
        CHECK ((status = 'proposed') = (expires_at IS NOT NULL)),
    CONSTRAINT vendor_doors_confirmed_stamp
        CHECK ((status IN ('confirmed', 'revoked')) = (confirmed_at IS NOT NULL)),
    CONSTRAINT vendor_doors_email_fields
        CHECK (origin = 'email' OR (sender_verdict IS NULL AND sender_domain IS NULL)),
    CONSTRAINT vendor_doors_tier_origin CHECK (
        (tier = 'exempt') = (origin IN ('dashboard', 'legacy_owner_set'))
        AND (tier <> 'extracted' OR origin = 'extracted')
        AND (tier NOT IN ('verified', 'unverified') OR origin = 'email')
    )
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_vendor_doors_live_template
    ON public.vendor_doors (owner_entity_id, kind, template)
    WHERE status IN ('proposed', 'confirmed');
CREATE INDEX IF NOT EXISTS ix_vendor_doors_pending
    ON public.vendor_doors (owner_entity_id, tier, host, kind)
    WHERE status = 'proposed';
CREATE INDEX IF NOT EXISTS ix_vendor_doors_pending_sender
    ON public.vendor_doors (owner_entity_id, sender_domain)
    WHERE status = 'proposed' AND tier = 'verified';
"""

# The SQL twin of butlers.core.registrable_domain (the parity test pins them together).
_DOMAIN_FUNCTIONS = r"""
CREATE OR REPLACE FUNCTION public.registrable_domain(p_name text) RETURNS text
LANGUAGE plpgsql STABLE SET search_path = pg_catalog, pg_temp AS $$
DECLARE
    labels text[]; n integer; i integer; l text; cand text; parent text; suffix_len integer;
BEGIN
    IF p_name IS NULL OR p_name = '' OR char_length(p_name) > 253 THEN RETURN NULL; END IF;
    -- ASCII only, checked before lower(): a non-ASCII letter such as the Kelvin sign lowercases
    -- to an ASCII one and would otherwise pass for it.
    IF p_name ~ '[^\x01-\x7F]' THEN RETURN NULL; END IF;
    labels := string_to_array(lower(p_name), '.');
    n := cardinality(labels);
    IF n < 2 THEN RETURN NULL; END IF;
    FOREACH l IN ARRAY labels LOOP
        IF l !~ '^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$' OR l LIKE 'xn--%' THEN RETURN NULL; END IF;
    END LOOP;
    IF NOT EXISTS (SELECT 1 FROM unnest(labels) AS x WHERE x !~ '^[0-9]+$') THEN
        RETURN NULL;
    END IF;
    FOR i IN 1..n LOOP
        cand := array_to_string(labels[i:n], '.');
        IF EXISTS (SELECT 1 FROM public.public_suffix_rules
                    WHERE kind = 'exception' AND name = cand) THEN
            suffix_len := n - i;
            EXIT;
        END IF;
    END LOOP;
    IF suffix_len IS NULL THEN
        FOR i IN 1..n LOOP
            cand := array_to_string(labels[i:n], '.');
            parent := array_to_string(labels[i + 1:n], '.');
            IF EXISTS (SELECT 1 FROM public.public_suffix_rules
                        WHERE kind = 'normal' AND name = cand)
               OR (parent <> '' AND EXISTS (SELECT 1 FROM public.public_suffix_rules
                                             WHERE kind = 'wildcard' AND name = parent)) THEN
                suffix_len := n - i + 1;
                EXIT;
            END IF;
        END LOOP;
    END IF;
    IF suffix_len IS NULL OR suffix_len >= n THEN RETURN NULL; END IF;
    RETURN array_to_string(labels[n - suffix_len:n], '.');
END $$;

CREATE OR REPLACE FUNCTION public.vendor_door_sender_domain(p_address text) RETURNS text
LANGUAGE plpgsql STABLE SET search_path = pg_catalog, pg_temp AS $$
DECLARE addr text;
BEGIN
    IF p_address IS NULL THEN RETURN NULL; END IF;
    addr := btrim(p_address);
    IF addr !~ '^[^@[:space:]<>,;]+@[^@[:space:]]+$' THEN RETURN NULL; END IF;
    RETURN public.registrable_domain(split_part(addr, '@', 2));
END $$;
"""

_VALIDATION_FUNCTIONS = r"""
CREATE OR REPLACE FUNCTION public.vendor_door_normalize(p_template text) RETURNS text
LANGUAGE sql IMMUTABLE SET search_path = pg_catalog, pg_temp AS $$
    SELECT CASE WHEN p_template LIKE 'https://%' THEN
        'https://' || lower(split_part(substr(p_template, 9), '/', 1))
        || substr(substr(p_template, 9), char_length(split_part(substr(p_template, 9), '/', 1)) + 1)
    ELSE p_template END
$$;

CREATE OR REPLACE FUNCTION public.vendor_door_host(p_template text) RETURNS text
LANGUAGE sql STABLE SET search_path = pg_catalog, pg_temp AS $$
    SELECT public.registrable_domain(split_part(substr(p_template, 9), '/', 1))
$$;

-- NULL means the template is acceptable; otherwise the name of the first rule it breaks.
CREATE OR REPLACE FUNCTION public.vendor_door_template_problem(
    p_template text, p_slot_types jsonb
) RETURNS text
LANGUAGE plpgsql STABLE SET search_path = pg_catalog, pg_temp AS $$
DECLARE
    rest text; host text; path text; stripped text; slot text; slot_names text[];
    expanded integer;
BEGIN
    IF p_template IS NULL OR char_length(p_template) > 300 THEN RETURN 'length'; END IF;
    IF p_template NOT LIKE 'https://%' THEN RETURN 'scheme'; END IF;
    rest := substr(p_template, 9);
    host := split_part(rest, '/', 1);
    path := substr(rest, char_length(host) + 1);
    IF host !~ '^[a-z0-9.-]+$' OR public.registrable_domain(host) IS NULL THEN
        RETURN 'host';
    END IF;
    IF path = '' OR path !~ '^(/[A-Za-z0-9._~{}-]*)+$' THEN RETURN 'path'; END IF;
    IF path ~ '(^|/)\.{1,2}(/|$)' THEN RETURN 'dot_segment'; END IF;
    stripped := regexp_replace(path, '\{[a-z_]+\}', '', 'g');
    IF stripped ~ '[{}]' THEN RETURN 'slot_syntax'; END IF;
    IF p_slot_types IS NULL OR jsonb_typeof(p_slot_types) <> 'object' THEN
        RETURN 'slot_types';
    END IF;
    SELECT coalesce(array_agg(DISTINCT m[1]), ARRAY[]::text[]) INTO slot_names
      FROM regexp_matches(path, '\{([a-z_]+)\}', 'g') AS m;
    FOR slot IN SELECT jsonb_object_keys(p_slot_types) LOOP
        IF NOT (slot = ANY (slot_names)) THEN RETURN 'slot_types'; END IF;
        IF p_slot_types ->> slot NOT IN ('order_ref', 'booking_ref', 'account_ref') THEN
            RETURN 'slot_type';
        END IF;
    END LOOP;
    FOREACH slot IN ARRAY slot_names LOOP
        IF NOT (p_slot_types ? slot) THEN RETURN 'slot_types'; END IF;
    END LOOP;
    -- Each slot value is at most 64 characters, so bound the substituted length.
    expanded := char_length(p_template)
        + (SELECT coalesce(sum(64 - char_length(m[1]) - 2), 0)
             FROM regexp_matches(path, '\{([a-z_]+)\}', 'g') AS m);
    IF expanded > 300 THEN RETURN 'length'; END IF;
    RETURN NULL;
END $$;
"""

_AUTHORITY_FUNCTIONS = r"""
CREATE OR REPLACE FUNCTION public.vendor_door_owner() RETURNS uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
    SELECT id FROM public.entities WHERE 'owner' = ANY (roles) ORDER BY created_at, id LIMIT 1
$$;

CREATE OR REPLACE FUNCTION public.vendor_door_caller_origin() RETURNS text
LANGUAGE plpgsql STABLE SET search_path = pg_catalog, pg_temp AS $$
DECLARE caller text := current_setting('role', true);
BEGIN
    IF caller = 'butler_switchboard_rw' THEN RETURN 'email'; END IF;
    IF caller = 'butler_finance_rw' THEN RETURN 'extracted'; END IF;
    RAISE EXCEPTION 'vendor-door proposals are not authorized for this runtime role'
        USING ERRCODE = '42501';
END $$;

CREATE OR REPLACE FUNCTION public.vendor_door_require_dashboard() RETURNS void
LANGUAGE plpgsql STABLE SET search_path = pg_catalog, pg_temp AS $$
BEGIN
    IF current_setting('role', true) IS DISTINCT FROM 'dashboard_vendor_doors' THEN
        RAISE EXCEPTION 'vendor-door control is not authorized for this runtime role'
            USING ERRCODE = '42501';
    END IF;
END $$;

CREATE OR REPLACE FUNCTION public.vendor_door_state(p_id uuid, p_outcome text, p_reason text)
RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
    SELECT jsonb_strip_nulls(jsonb_build_object(
        'outcome', p_outcome, 'reason', p_reason, 'id', d.id, 'status', d.status,
        'version', d.version, 'tier', d.tier))
      FROM (SELECT 1) AS one LEFT JOIN public.vendor_doors AS d ON d.id = p_id
$$;

-- NULL when the new pending proposal fits its tier's caps (the row being taken over is not
-- counted against itself), otherwise the cap that is full.
CREATE OR REPLACE FUNCTION public.vendor_door_cap_problem(
    p_owner uuid, p_tier text, p_host text, p_kind text, p_sender_domain text, p_exclude uuid
) RETURNS text
LANGUAGE plpgsql STABLE SET search_path = pg_catalog, pg_temp AS $$
DECLARE pending integer;
BEGIN
    IF p_tier = 'exempt' THEN RETURN NULL; END IF;
    SELECT count(*) INTO pending FROM public.vendor_doors AS d
     WHERE d.owner_entity_id = p_owner AND d.status = 'proposed'
       AND d.expires_at > clock_timestamp() AND d.tier = p_tier AND d.host = p_host
       AND d.kind = p_kind AND d.id IS DISTINCT FROM p_exclude;
    IF pending >= 5 THEN RETURN 'host_cap'; END IF;
    IF p_tier = 'verified' THEN
        SELECT count(*) INTO pending FROM public.vendor_doors AS d
         WHERE d.owner_entity_id = p_owner AND d.status = 'proposed'
           AND d.expires_at > clock_timestamp() AND d.tier = 'verified'
           AND d.sender_domain = p_sender_domain AND d.id IS DISTINCT FROM p_exclude;
        IF pending >= 5 THEN RETURN 'sender_cap'; END IF;
    ELSE
        SELECT count(*) INTO pending FROM public.vendor_doors AS d
         WHERE d.owner_entity_id = p_owner AND d.status = 'proposed'
           AND d.expires_at > clock_timestamp() AND d.tier = p_tier
           AND d.id IS DISTINCT FROM p_exclude;
        IF pending >= 50 THEN RETURN 'owner_cap'; END IF;
    END IF;
    RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION public.vendor_door_insert(
    p_origin text, p_tier text, p_kind text, p_template text, p_slot_types jsonb,
    p_vendor_entity_id uuid, p_event uuid, p_verdict text, p_sender_domain text,
    p_lineage jsonb, p_supersedes uuid
) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE
    owner_id uuid := public.vendor_door_owner();
    tmpl text; problem text; door_host text; existing public.vendor_doors%ROWTYPE;
    new_id uuid; rank_new integer; rank_old integer; target public.vendor_doors%ROWTYPE;
    had_existing boolean;
BEGIN
    IF owner_id IS NULL THEN
        RETURN jsonb_build_object('outcome', 'refused', 'reason', 'no_owner');
    END IF;
    IF p_kind IS NULL OR p_kind NOT IN ('cancel', 'manage', 'return', 'check_in') THEN
        RETURN jsonb_build_object('outcome', 'refused', 'reason', 'kind');
    END IF;
    tmpl := public.vendor_door_normalize(p_template);
    problem := public.vendor_door_template_problem(tmpl, coalesce(p_slot_types, '{}'::jsonb));
    IF problem IS NOT NULL THEN
        RETURN jsonb_build_object('outcome', 'refused', 'reason', problem);
    END IF;
    door_host := public.vendor_door_host(tmpl);
    -- Only the dashboard replaces a door, and only one of the same owner, kind and host.
    IF p_supersedes IS NOT NULL AND (p_origin <> 'dashboard' OR NOT EXISTS (
        SELECT 1 FROM public.vendor_doors AS s
         WHERE s.id = p_supersedes AND s.owner_entity_id = owner_id AND s.kind = p_kind
           AND s.host = door_host)) THEN
        RETURN jsonb_build_object('outcome', 'refused', 'reason', 'supersedes');
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended('vendor_doors:' || owner_id::text, 0));

    SELECT * INTO existing FROM public.vendor_doors
     WHERE owner_entity_id = owner_id AND kind = p_kind AND template = tmpl
       AND status IN ('proposed', 'confirmed');
    had_existing := FOUND;
    IF had_existing AND existing.status = 'confirmed' THEN
        RETURN public.vendor_door_state(existing.id, 'exists', NULL);
    END IF;

    IF p_origin <> 'dashboard' AND EXISTS (
        SELECT 1 FROM public.vendor_doors
         WHERE owner_entity_id = owner_id AND kind = p_kind AND template = tmpl
           AND status = 'rejected') THEN
        RETURN jsonb_build_object('outcome', 'suppressed');
    END IF;

    IF had_existing THEN
        -- A pending row is taken over by a higher-precedence sighting (dashboard, then
        -- verified, then extracted or unverified), and an expired one by any sighting, so an
        -- early low-trust proposal can neither squat a template nor block the real one.
        rank_new := CASE p_tier WHEN 'exempt' THEN 3 WHEN 'verified' THEN 2 ELSE 1 END;
        rank_old := CASE existing.tier WHEN 'exempt' THEN 3 WHEN 'verified' THEN 2 ELSE 1 END;
        IF existing.expires_at > clock_timestamp() AND rank_new <= rank_old THEN
            RETURN public.vendor_door_state(existing.id, 'exists', NULL);
        END IF;
        problem := public.vendor_door_cap_problem(
            owner_id, p_tier, door_host, p_kind, p_sender_domain, existing.id);
        IF problem IS NOT NULL THEN
            RETURN jsonb_build_object('outcome', 'dropped', 'reason', problem);
        END IF;
        UPDATE public.vendor_doors AS d
           SET origin = p_origin, tier = p_tier, ingestion_event_id = p_event,
               sender_verdict = p_verdict, sender_domain = p_sender_domain,
               lineage = coalesce(p_lineage, '{}'::jsonb),
               vendor_entity_id = coalesce(p_vendor_entity_id, d.vendor_entity_id),
               supersedes_id = p_supersedes, version = d.version + 1,
               expires_at = clock_timestamp() + interval '30 days'
         WHERE d.id = existing.id
        RETURNING d.* INTO target;
        RETURN public.vendor_door_state(target.id, 'exists', NULL);
    END IF;

    problem := public.vendor_door_cap_problem(
        owner_id, p_tier, door_host, p_kind, p_sender_domain, NULL);
    IF problem IS NOT NULL THEN
        RETURN jsonb_build_object('outcome', 'dropped', 'reason', problem);
    END IF;
    INSERT INTO public.vendor_doors (
        owner_entity_id, kind, vendor_entity_id, host, template, slot_types, status, origin,
        tier, ingestion_event_id, sender_verdict, sender_domain, lineage, supersedes_id,
        expires_at)
    VALUES (owner_id, p_kind, p_vendor_entity_id, door_host, tmpl,
        coalesce(p_slot_types, '{}'::jsonb), 'proposed', p_origin, p_tier, p_event, p_verdict,
        p_sender_domain, coalesce(p_lineage, '{}'::jsonb), p_supersedes,
        clock_timestamp() + interval '30 days')
    RETURNING id INTO new_id;
    RETURN public.vendor_door_state(new_id, 'stored', NULL);
END $$;

CREATE OR REPLACE FUNCTION public.vendor_door_propose(
    p_kind text, p_template text, p_slot_types jsonb, p_ingestion_event_id uuid,
    p_vendor_entity_id uuid, p_lineage jsonb
) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE
    origin text := public.vendor_door_caller_origin();
    sender text; verdict text; sender_domain text; link_host text; tier text;
BEGIN
    IF origin = 'extracted' THEN
        RETURN public.vendor_door_insert('extracted', 'extracted', p_kind, p_template, p_slot_types,
            p_vendor_entity_id, NULL, NULL, NULL, p_lineage, NULL);
    END IF;
    -- The stored verdict and sender are read here, never accepted from the caller. A missing
    -- event, address or verdict fails closed to unverified. A proposer never supersedes a door.
    SELECT e.source_sender_identity, e.sender_authentication INTO sender, verdict
      FROM public.ingestion_events AS e WHERE e.id = p_ingestion_event_id;
    verdict := coalesce(verdict, 'unverified');
    sender_domain := public.vendor_door_sender_domain(sender);
    link_host := public.vendor_door_host(public.vendor_door_normalize(p_template));
    tier := CASE WHEN verdict = 'authenticated' AND sender_domain IS NOT NULL
                  AND sender_domain = link_host THEN 'verified' ELSE 'unverified' END;
    RETURN public.vendor_door_insert('email', tier, p_kind, p_template, p_slot_types,
        p_vendor_entity_id, p_ingestion_event_id, verdict, sender_domain, p_lineage, NULL);
END $$;

CREATE OR REPLACE FUNCTION public.vendor_door_create(
    p_kind text, p_template text, p_slot_types jsonb, p_vendor_entity_id uuid, p_supersedes uuid
) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
BEGIN
    PERFORM public.vendor_door_require_dashboard();
    RETURN public.vendor_door_insert('dashboard', 'exempt', p_kind, p_template, p_slot_types,
        p_vendor_entity_id, NULL, NULL, NULL, '{}'::jsonb, p_supersedes);
END $$;

CREATE OR REPLACE FUNCTION public.vendor_door_confirm(
    p_id uuid, p_expected_version integer, p_session_hash text
) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE
    owner_id uuid := public.vendor_door_owner();
    done public.vendor_doors%ROWTYPE; cur public.vendor_doors%ROWTYPE;
BEGIN
    PERFORM public.vendor_door_require_dashboard();
    UPDATE public.vendor_doors AS d
       SET status = 'confirmed', version = d.version + 1, expires_at = NULL,
           confirmed_at = clock_timestamp(), confirmed_by_session = p_session_hash
     WHERE d.id = p_id AND d.version = p_expected_version AND d.status = 'proposed'
       AND d.expires_at > clock_timestamp() AND d.owner_entity_id = owner_id
       AND public.vendor_door_template_problem(d.template, d.slot_types) IS NULL
    RETURNING d.* INTO done;
    IF FOUND THEN
        IF done.supersedes_id IS NOT NULL THEN
            UPDATE public.vendor_doors
               SET status = 'revoked', version = version + 1, revoked_at = clock_timestamp()
             WHERE id = done.supersedes_id AND status = 'confirmed' AND owner_entity_id = owner_id
               AND kind = done.kind AND host = done.host;
        END IF;
        RETURN public.vendor_door_state(done.id, 'ok', NULL);
    END IF;
    SELECT * INTO cur FROM public.vendor_doors WHERE id = p_id AND owner_entity_id = owner_id;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('outcome', 'refused', 'reason', 'not_found');
    END IF;
    IF cur.status = 'confirmed' THEN
        RETURN public.vendor_door_state(p_id, 'unchanged', NULL);
    END IF;
    RETURN public.vendor_door_state(p_id, 'refused', CASE
        WHEN cur.status <> 'proposed' THEN 'not_pending'
        WHEN cur.expires_at <= clock_timestamp() THEN 'expired'
        WHEN cur.version <> p_expected_version THEN 'stale_version'
        ELSE 'rule' END);
END $$;

CREATE OR REPLACE FUNCTION public.vendor_door_revoke(p_id uuid, p_expected_version integer)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE
    owner_id uuid := public.vendor_door_owner(); cur public.vendor_doors%ROWTYPE; hit uuid;
BEGIN
    PERFORM public.vendor_door_require_dashboard();
    UPDATE public.vendor_doors AS d
       SET status = 'revoked', version = d.version + 1, revoked_at = clock_timestamp()
     WHERE d.id = p_id AND d.version = p_expected_version AND d.status = 'confirmed'
       AND d.owner_entity_id = owner_id
    RETURNING d.id INTO hit;
    IF hit IS NOT NULL THEN RETURN public.vendor_door_state(hit, 'ok', NULL); END IF;
    SELECT * INTO cur FROM public.vendor_doors WHERE id = p_id AND owner_entity_id = owner_id;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('outcome', 'refused', 'reason', 'not_found');
    END IF;
    IF cur.status = 'revoked' THEN RETURN public.vendor_door_state(p_id, 'unchanged', NULL); END IF;
    RETURN public.vendor_door_state(p_id, 'refused', CASE
        WHEN cur.status <> 'confirmed' THEN 'not_confirmed' ELSE 'stale_version' END);
END $$;

CREATE OR REPLACE FUNCTION public.vendor_door_reject(p_id uuid, p_expected_version integer)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE
    owner_id uuid := public.vendor_door_owner(); cur public.vendor_doors%ROWTYPE; hit uuid;
BEGIN
    PERFORM public.vendor_door_require_dashboard();
    UPDATE public.vendor_doors AS d
       SET status = 'rejected', version = d.version + 1, expires_at = NULL,
           rejected_at = clock_timestamp()
     WHERE d.id = p_id AND d.version = p_expected_version AND d.status = 'proposed'
       AND d.owner_entity_id = owner_id
    RETURNING d.id INTO hit;
    IF hit IS NOT NULL THEN RETURN public.vendor_door_state(hit, 'ok', NULL); END IF;
    SELECT * INTO cur FROM public.vendor_doors WHERE id = p_id AND owner_entity_id = owner_id;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('outcome', 'refused', 'reason', 'not_found');
    END IF;
    IF cur.status = 'rejected' THEN
        RETURN public.vendor_door_state(p_id, 'unchanged', NULL);
    END IF;
    RETURN public.vendor_door_state(p_id, 'refused', CASE
        WHEN cur.status <> 'proposed' THEN 'not_pending' ELSE 'stale_version' END);
END $$;

CREATE OR REPLACE FUNCTION public.vendor_doors_no_delete() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
BEGIN
    RAISE EXCEPTION '% rows are retained', TG_TABLE_NAME USING ERRCODE = '42501';
END $$;
"""

_VIEW = """
-- The owner subquery is inline: a view checks EXECUTE on the functions it calls as the invoking
-- role, and no butler role holds EXECUTE on the helpers.
CREATE OR REPLACE VIEW public.vendor_doors_current AS
SELECT d.id, d.version, d.owner_entity_id, d.kind, d.vendor_entity_id, d.host, d.template,
       d.slot_types
  FROM public.vendor_doors AS d
 WHERE d.status = 'confirmed' AND d.owner_entity_id = (
           SELECT e.id FROM public.entities AS e WHERE 'owner' = ANY (e.roles)
            ORDER BY e.created_at, e.id LIMIT 1)
"""

_TRIGGERS = """
DROP TRIGGER IF EXISTS vendor_doors_no_delete ON public.vendor_doors;
CREATE TRIGGER vendor_doors_no_delete BEFORE DELETE OR TRUNCATE
    ON public.vendor_doors FOR EACH STATEMENT EXECUTE FUNCTION public.vendor_doors_no_delete();
"""

# Public (EXECUTE-granted) functions and their one role set; helpers are granted to nobody.
_PUBLIC_FUNCTIONS = {
    "public.vendor_door_propose(text, text, jsonb, uuid, uuid, jsonb)": (
        "butler_switchboard_rw",
        "butler_finance_rw",
    ),
    "public.vendor_door_create(text, text, jsonb, uuid, uuid)": (_DASH,),
    "public.vendor_door_confirm(uuid, integer, text)": (_DASH,),
    "public.vendor_door_revoke(uuid, integer)": (_DASH,),
    "public.vendor_door_reject(uuid, integer)": (_DASH,),
}
_HELPER_FUNCTIONS = (
    "public.registrable_domain(text)",
    "public.vendor_door_sender_domain(text)",
    "public.vendor_door_normalize(text)",
    "public.vendor_door_host(text)",
    "public.vendor_door_template_problem(text, jsonb)",
    "public.vendor_door_owner()",
    "public.vendor_door_caller_origin()",
    "public.vendor_door_require_dashboard()",
    "public.vendor_door_state(uuid, text, text)",
    "public.vendor_door_insert(text, text, text, text, jsonb, uuid, uuid, text, text, jsonb, uuid)",
    "public.vendor_door_cap_problem(uuid, text, text, text, text, uuid)",
    "public.vendor_doors_no_delete()",
)
_VIEW_READERS = (
    "butler_finance_rw",
    "butler_switchboard_rw",
    "butler_messenger_rw",
    _DASH,
)


def _grants() -> None:
    # init-db.sql's default privileges hand every runtime role DML on new public tables, so each
    # relation starts from nothing. The REVOKEs act on objects this migration owns, so they are
    # never wrapped in a tolerant block; only a grant to a role this login cannot administer may
    # be skipped, role by role.
    for relation in (
        "public.vendor_doors",
        "public.vendor_doors_current",
        "public.public_suffix_rules",
    ):
        op.execute(f"REVOKE ALL PRIVILEGES ON TABLE {relation} FROM PUBLIC")
    for function in (*_PUBLIC_FUNCTIONS, *_HELPER_FUNCTIONS):
        op.execute(f"REVOKE ALL PRIVILEGES ON FUNCTION {function} FROM PUBLIC")
    op.execute(
        """
        DO $$
        DECLARE role_name TEXT;
        BEGIN
            FOR role_name IN SELECT rolname FROM pg_roles
                WHERE rolname LIKE 'butler\\_%\\_rw' ESCAPE '\\' OR rolname = 'connector_writer'
                   OR rolname = 'dashboard_vendor_doors'
            LOOP
                EXECUTE format('REVOKE ALL PRIVILEGES ON TABLE public.vendor_doors FROM %I',
                               role_name);
                EXECUTE format('REVOKE ALL PRIVILEGES ON TABLE public.vendor_doors_current '
                               'FROM %I', role_name);
                EXECUTE format('REVOKE ALL PRIVILEGES ON TABLE public.public_suffix_rules '
                               'FROM %I', role_name);
            END LOOP;
        END $$
        """
    )
    # The capability role holds nothing else: schema USAGE is the one grant it needs to reach them.
    op.execute(_tolerant(f"GRANT USAGE ON SCHEMA public TO {_DASH}", _DASH))
    for function, roles in _PUBLIC_FUNCTIONS.items():
        for role in roles:
            op.execute(_tolerant(f"GRANT EXECUTE ON FUNCTION {function} TO {role}", role))
    for role in _VIEW_READERS:
        op.execute(_tolerant(f"GRANT SELECT ON TABLE public.vendor_doors_current TO {role}", role))
    op.execute(_tolerant(f"GRANT SELECT ON TABLE public.vendor_doors TO {_DASH}", _DASH))


def _tolerant(statement: str, role: str) -> str:
    """Run one grant when its role exists; skip only a role this login cannot administer."""
    escaped = statement.replace("'", "''")
    return f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                BEGIN
                    EXECUTE '{escaped}';
                EXCEPTION WHEN insufficient_privilege THEN NULL;
                END;
            ELSIF '{role}' = '{_DASH}' THEN
                RAISE EXCEPTION 'core_336 requires the {_DASH} role from scripts/init-db.sql';
            END IF;
        END $$
    """


def _load_suffix_rules() -> None:
    from butlers.core.registrable_domain import suffix_rules

    normal, wildcard, exceptions = suffix_rules()
    names = [*sorted(normal), *sorted(wildcard), *sorted(exceptions)]
    kinds = (
        ["normal"] * len(normal) + ["wildcard"] * len(wildcard) + ["exception"] * len(exceptions)
    )
    op.get_bind().execute(
        sa.text(
            "INSERT INTO public.public_suffix_rules (name, kind) "
            "SELECT n, k FROM unnest(CAST(:names AS text[]), CAST(:kinds AS text[])) AS t(n, k) "
            "ON CONFLICT DO NOTHING"
        ),
        {"names": names, "kinds": kinds},
    )


def _installation_count() -> int:
    """Count schema-local core chains that still depend on the shared objects."""
    bind = op.get_bind()
    schemas = bind.exec_driver_sql(
        """
        SELECT namespace.nspname
        FROM pg_class version_table
        JOIN pg_namespace namespace ON namespace.oid = version_table.relnamespace
        WHERE version_table.relname = 'alembic_version'
          AND version_table.relkind IN ('r', 'p')
          AND left(namespace.nspname, 3) <> 'pg_'
          AND namespace.nspname <> 'information_schema'
        """
    ).scalars()
    quote = bind.dialect.identifier_preparer.quote
    count = 0
    for schema in schemas:
        installed = bind.exec_driver_sql(
            f"""
            SELECT EXISTS (
                SELECT 1 FROM {quote(schema)}.alembic_version
                WHERE CASE WHEN version_num ~ '^core_[0-9]+$'
                    THEN substring(version_num FROM 6)::integer >= 336
                    ELSE false END
            )
            """
        ).scalar()
        count += int(bool(installed))
    return count


def upgrade() -> None:
    op.execute(_LOCK)
    op.execute(_TABLES)
    _load_suffix_rules()
    op.execute(_DOMAIN_FUNCTIONS)
    op.execute(_VALIDATION_FUNCTIONS)
    op.execute(_AUTHORITY_FUNCTIONS)
    op.execute(_VIEW)
    op.execute(_TRIGGERS)
    _grants()


def downgrade() -> None:
    op.execute(_LOCK)
    if _installation_count() > 1:
        return
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('public.vendor_doors') IS NOT NULL
               AND EXISTS (SELECT 1 FROM public.vendor_doors) THEN
                RAISE EXCEPTION '{_ROLLBACK_REFUSAL}';
            END IF;
        END
        $$
        """
    )
    op.execute("DROP VIEW IF EXISTS public.vendor_doors_current")
    op.execute("DROP TABLE IF EXISTS public.vendor_doors")
    for function in (*_PUBLIC_FUNCTIONS, *_HELPER_FUNCTIONS):
        op.execute(f"DROP FUNCTION IF EXISTS {function}")
    op.execute("DROP TABLE IF EXISTS public.public_suffix_rules")
