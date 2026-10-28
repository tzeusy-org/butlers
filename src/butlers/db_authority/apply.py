"""Locked migrator convergence of declared objects, never discovery-derived grants."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from .grants import (
    ObjectAuthority,
    ObjectKind,
    compile_function_manifest,
    compile_relation_manifest,
    relation_security_flags,
    select_migration_manifest,
)
from .roles import AuthorityError

# Discovery is a refusal input. Only a source-declared ObjectAuthority authorizes
# the GRANT statements below; catalog names cannot extend that set.
_RELATIONS = """
SELECT n.nspname AS schema, c.relname AS name, c.relkind AS kind,
       owner.rolname AS owner, c.relrowsecurity AS rls, c.relforcerowsecurity AS forced_rls,
       c.oid AS oid, c.relispartition AS partition
FROM pg_catalog.pg_class c
JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
JOIN pg_catalog.pg_roles owner ON owner.oid = c.relowner
WHERE n.nspname = ANY(:schemas) AND c.relkind IN ('r','p','v','m','S','f')
AND NOT EXISTS (SELECT FROM pg_catalog.pg_depend d
    WHERE d.classid = 'pg_catalog.pg_class'::regclass AND d.objid = c.oid AND d.deptype = 'e')
ORDER BY n.nspname, c.relname
"""
_RELATION_ACL = """
SELECT CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE r.rolname END AS role,
       a.privilege_type AS verb, a.is_grantable AS grantable
FROM pg_catalog.pg_class c
CROSS JOIN LATERAL pg_catalog.aclexplode(
    COALESCE(c.relacl, pg_catalog.acldefault(CASE WHEN c.relkind='S' THEN 's'::"char"
                                              ELSE 'r'::"char" END,c.relowner))) a
LEFT JOIN pg_catalog.pg_roles r ON r.oid=a.grantee
WHERE c.oid=:oid AND a.grantee<>c.relowner
"""
_COLUMN_ACL = """
SELECT col.attname AS name, CASE WHEN acl.grantee=0 THEN 'PUBLIC' ELSE r.rolname END AS role,
       acl.privilege_type AS verb, acl.is_grantable AS grantable
FROM pg_catalog.pg_attribute col
CROSS JOIN LATERAL pg_catalog.aclexplode(col.attacl) acl
LEFT JOIN pg_catalog.pg_roles r ON r.oid=acl.grantee
JOIN pg_catalog.pg_class c ON c.oid=col.attrelid
WHERE col.attrelid=:oid AND col.attnum>0 AND NOT col.attisdropped AND acl.grantee<>c.relowner
"""


def quote_identifier(value: str) -> str:
    """Quote catalog identifiers without turning observed names into authority."""
    if not isinstance(value, str) or not value or "\0" in value:
        raise AuthorityError("Database catalog identity is unavailable")
    return '"' + value.replace('"', '""') + '"'


def _qualified(row: ObjectAuthority) -> str:
    return f"{quote_identifier(row.schema)}.{quote_identifier(row.name)}"


def _rows(connection, sql: str, **params):
    return tuple(connection.execute(text(sql), params).mappings())


def _relation_kind(value: str) -> ObjectKind:
    if value in ("r", "p"):
        return ObjectKind.TABLE
    if value == "v":
        return ObjectKind.VIEW
    if value == "m":
        return ObjectKind.MATERIALIZED_VIEW
    if value == "S":
        return ObjectKind.SEQUENCE
    raise AuthorityError("Managed database object is not declared")


def _relation_ddl_kind(row: ObjectAuthority) -> str:
    """Ownership grammar is distinct from TABLE privilege grammar for views."""
    return {
        ObjectKind.TABLE: "TABLE",
        ObjectKind.VIEW: "VIEW",
        ObjectKind.MATERIALIZED_VIEW: "MATERIALIZED VIEW",
        ObjectKind.SEQUENCE: "SEQUENCE",
    }[row.kind]


def _partition_owner_transition(row):
    from .grants import ORDINARY_PARTITION_OWNERS

    if ORDINARY_PARTITION_OWNERS.get((row.schema, row.name)) == row.owner:
        return True
    if row.sequence_parent is not None:
        return ORDINARY_PARTITION_OWNERS.get(row.sequence_parent[:2]) == row.owner
    return any(
        row.schema == schema
        and row.owner == owner
        and row.producer == "fixed partition parent: " + schema + "." + name
        for (schema, name), owner in ORDINARY_PARTITION_OWNERS.items()
    )


def _relation_failure_note(observed, declared, *, phase, migration_owner, maintenance_owner):
    """Build bounded hashed v2 exception notes without changing admission."""
    import hashlib
    import json

    if phase not in {"bind_partitions", "validate_relations"}:
        return None
    if not isinstance(observed, (tuple, list)) or not isinstance(declared, (tuple, list)):
        return None
    if len(observed) > 4096 or len(declared) > 4096:
        return None

    def identifier(value):
        if type(value) is not str or not value or len(value.encode("utf-8")) > 1024:
            raise ValueError("Unprojectable identifier")
        return value

    def digests(schema, name):
        schema, name = identifier(schema), identifier(name)
        identity = json.dumps([schema, name], ensure_ascii=True, separators=(",", ":"))
        return {
            "identity_sha256": hashlib.sha256(identity.encode("utf-8")).hexdigest(),
            "schema_sha256": hashlib.sha256(schema.encode("utf-8")).hexdigest(),
            "name_sha256": hashlib.sha256(name.encode("utf-8")).hexdigest(),
        }

    identifier(migration_owner)
    identifier(maintenance_owner)
    expected = {}
    for row in declared:
        identifier(row.schema)
        identifier(row.name)
        identifier(row.owner)
        if row.kind not in {
            ObjectKind.TABLE,
            ObjectKind.VIEW,
            ObjectKind.MATERIALIZED_VIEW,
            ObjectKind.SEQUENCE,
        }:
            return None
        if type(row.required) is not bool or row.identity in expected:
            return None
        expected[row.identity] = row
    kinds = {
        "r": ("table", "table"),
        "p": ("partitioned_table", "table"),
        "v": ("view", "view"),
        "m": ("materialized_view", "materialized_view"),
        "S": ("sequence", "sequence"),
        "f": ("foreign_table", "other"),
    }
    rows, seen = [], set()
    for actual in observed:
        schema, name = identifier(actual["schema"]), identifier(actual["name"])
        owner = identifier(actual["owner"])
        kind, identity_kind = kinds[actual["kind"]]
        identity = (schema, name, identity_kind, "")
        if identity in seen:
            return None
        seen.add(identity)
        row = expected.get(identity)
        enabled, forced = relation_security_flags(row) if row is not None else (False, False)
        rows.append(
            {
                **digests(schema, name),
                "kind": kind,
                "declared_identity": row is not None,
                "owner_is_expected": row is not None and owner == row.owner,
                "owner_is_migration": owner == migration_owner,
                "owner_is_maintenance": owner == maintenance_owner,
                "actor_observation_available": True,
                "actual_enabled_is_bool": type(actual["rls"]) is bool,
                "actual_enabled": actual["rls"] is True,
                "actual_forced_is_bool": type(actual["forced_rls"]) is bool,
                "actual_forced": actual["forced_rls"] is True,
                "expected_flags_available": row is not None,
                "expected_enabled": enabled is True,
                "expected_forced": forced is True,
            }
        )
    # The unchanged v2 missing-kind protocol cannot express materialized views.
    # Keep the optional diagnostic unavailable rather than emitting an invalid
    # payload; validate_relations still refuses the missing required object.
    if any(
        row.required and row.kind == ObjectKind.MATERIALIZED_VIEW and row.identity not in seen
        for row in declared
    ):
        return None
    missing = [
        {**digests(row.schema, row.name), "declared_kind": row.kind.value}
        for row in declared
        if row.required and row.identity not in seen
    ]
    payload = {
        "version": 2,
        "phase": phase,
        "exception": "AuthorityError",
        "observed_count": len(rows),
        "declared_count": len(declared),
        "rows": sorted(rows, key=lambda row: (row["identity_sha256"], row["kind"])),
        "missing_required_count": len(missing),
        "missing_required": sorted(
            missing, key=lambda row: (row["identity_sha256"], row["declared_kind"])
        ),
    }
    note = "DB_AUTHORITY_RELATION_DIAGNOSTIC " + json.dumps(
        payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    )
    return note if len(note.encode("utf-8")) <= 1024 * 1024 else None


def _add_relation_failure_note(error, observed, declared, **context):
    # Observation is subordinate to the original validator, including if the
    # bounded projection or the exception's standard note transport fails.
    try:
        note = _relation_failure_note(observed, declared, **context)
        if note is not None:
            error.add_note(note)
    except Exception:
        pass


def validate_relations(
    observed,
    declared: tuple[ObjectAuthority, ...],
    *,
    administrative_history=False,
    transition_owner=None,
    historical_audit=None,
):
    """Check source identities/owners before effects; serving always requires RLS.

    A bounded old revision cannot be asserted to have a later RLS shape. This
    sole administrative-history mode accepts no serving permission whatsoever;
    its selected graph/revision has already been proved by the maintenance
    caller. It closes ACLs rather than certifying any runtime catalog.
    """
    if administrative_history:
        from .grants import HistoricalPriorityAuditAuthority

        if historical_audit is not None and not isinstance(
            historical_audit, HistoricalPriorityAuditAuthority
        ):
            raise AuthorityError("Historical audit source profile is unavailable")
        allowed = (
            {row.identity: row.permissions for row in historical_audit.dependencies}
            if historical_audit is not None
            else {}
        )
        if any(
            row.permissions and row.permissions != allowed.get(row.identity) for row in declared
        ):
            raise AuthorityError("Historical administration cannot grant a serving privilege")
    expected = {row.identity: row for row in declared}
    if len(expected) != len(declared):
        raise AuthorityError("Database object authority is ambiguous")
    bound = []
    seen = set()
    for actual in observed:
        identity = actual["schema"], actual["name"], _relation_kind(actual["kind"]).value, ""
        if identity in seen:
            raise AuthorityError("Database catalog identity is ambiguous")
        seen.add(identity)
        row = expected.get(identity)
        if row is None:
            raise AuthorityError("Managed database object is not declared")
        if actual["owner"] != row.owner and not (
            transition_owner is not None
            and actual["owner"] == transition_owner
            and _partition_owner_transition(row)
        ):
            # Ownership transfer is an explicit operator prerequisite, never an
            # automatic adoption of a compatible-looking or hostile object.
            raise AuthorityError("Managed database object ownership is unavailable")
        enabled, forced = relation_security_flags(row)
        if administrative_history:
            if type(actual["rls"]) is not bool or type(actual["forced_rls"]) is not bool:
                raise AuthorityError("Historical database row security is unavailable")
        elif actual["rls"] is not enabled or actual["forced_rls"] is not forced:
            raise AuthorityError("Required database row security is unavailable")
        bound.append((actual, row))
    if any(row.required and row.identity not in seen for row in declared):
        raise AuthorityError("Required database object is unavailable")
    return tuple(bound)


# A new partition is an implementation child of one of these fixed producers,
# not a new permission source. Child ACLs are empty: operations through the
# declared parent retain their parent privilege checks.
_PARTITION_PARENTS = frozenset(
    {
        ("public", "token_usage_ledger"),
        ("connectors", "filtered_events"),
        ("switchboard", "message_inbox"),
        ("switchboard", "connector_heartbeat_log"),
    }
)
_PARTITION_LINKS = """
SELECT child.oid, pn.nspname AS parent_schema, parent.relname AS parent_name
FROM pg_catalog.pg_class child
JOIN pg_catalog.pg_inherits link ON link.inhrelid=child.oid
JOIN pg_catalog.pg_class parent ON parent.oid=link.inhparent
JOIN pg_catalog.pg_namespace pn ON pn.oid=parent.relnamespace
WHERE child.oid=ANY(:oids) AND child.relispartition
"""
_SEQUENCE_LINKS = """
SELECT seq.oid, n.nspname AS parent_schema, parent.relname AS parent_name,
 col.attname AS parent_column, parent_owner.rolname AS parent_owner, dependency.deptype
FROM pg_catalog.pg_class seq
JOIN pg_catalog.pg_depend dependency ON dependency.classid='pg_catalog.pg_class'::regclass
 AND dependency.objid=seq.oid AND dependency.refclassid='pg_catalog.pg_class'::regclass
 AND dependency.deptype IN ('a','i')
JOIN pg_catalog.pg_class parent ON parent.oid=dependency.refobjid
JOIN pg_catalog.pg_roles parent_owner ON parent_owner.oid=parent.relowner
JOIN pg_catalog.pg_namespace n ON n.oid=parent.relnamespace
JOIN pg_catalog.pg_attribute col ON col.attrelid=parent.oid
 AND col.attnum=dependency.refobjsubid AND NOT col.attisdropped
WHERE seq.oid=ANY(:oids) AND seq.relkind='S'
"""


def _add_partition_precondition_note(error, actual, parents):
    """Attach a bounded hashed refusal reason; never change partition admission."""
    try:
        import hashlib
        import json

        schema, name = actual["schema"], actual["name"]
        if any(
            type(value) is not str or not value or len(value.encode("utf-8")) > 1024
            for value in (schema, name)
        ):
            return
        kind = {
            "r": "table",
            "p": "partitioned_table",
            "v": "view",
            "m": "materialized_view",
            "S": "sequence",
            "f": "foreign_table",
        }.get(actual["kind"], "UNKNOWN")
        identity = json.dumps([schema, name], ensure_ascii=True, separators=(",", ":"))
        count = len(parents)
        payload = {
            "version": 1,
            "phase": "bind_partitions_precondition",
            "exception": "AuthorityError",
            "reason": "unlisted_not_single_table_partition",
            "identity_sha256": hashlib.sha256(identity.encode("utf-8")).hexdigest(),
            "schema_sha256": hashlib.sha256(schema.encode("utf-8")).hexdigest(),
            "name_sha256": hashlib.sha256(name.encode("utf-8")).hexdigest(),
            "kind": kind,
            "declared_key": False,
            "partition_is_true": actual.get("partition") is True,
            "parent_link_count": "zero" if count == 0 else "one" if count == 1 else "many",
        }
        error.add_note(
            "DB_AUTHORITY_PARTITION_BINDING_DIAGNOSTIC "
            + json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        )
    except Exception:
        pass


def bind_partition_relations(observed, declared, links, *, transition_owner=None):
    """Admit only actual children of fixed source parents, with no direct grants."""
    expected = {(row.schema, row.name): row for row in declared}
    by_oid = {}
    for link in links:
        by_oid.setdefault(link["oid"], []).append(link)
    result = list(declared)
    for actual in observed:
        if (actual["schema"], actual["name"]) in expected:
            continue
        parents = by_oid.get(actual["oid"], ())
        if actual.get("partition") is not True or actual["kind"] != "r" or len(parents) != 1:
            error = AuthorityError("Managed database object is not declared")
            _add_partition_precondition_note(error, actual, parents)
            raise error
        parent_key = parents[0]["parent_schema"], parents[0]["parent_name"]
        parent = expected.get(parent_key)
        if (
            parent_key not in _PARTITION_PARENTS
            or parent is None
            or parent.kind != ObjectKind.TABLE
            or actual["schema"] != parent.schema
            or actual["owner"] not in {parent.owner, transition_owner}
        ):
            raise AuthorityError("Managed partition does not match its source producer")
        result.append(
            ObjectAuthority(
                actual["schema"],
                actual["name"],
                ObjectKind.TABLE,
                parent.owner,
                (),
                "fixed partition parent: " + ".".join(parent_key),
                required=False,
            )
        )
    return tuple(result)


def validate_sequence_bindings(bound, links, *, transition_owner=None) -> None:
    """An identically named sequence is insufficient without its source dependency."""
    by_oid = {}
    for link in links:
        by_oid.setdefault(link["oid"], []).append(link)
    for actual, row in bound:
        if row.kind != ObjectKind.SEQUENCE:
            continue
        parents = by_oid.get(actual["oid"], ())
        if row.sequence_parent is None or len(parents) != 1:
            raise AuthorityError("Managed sequence dependency is unavailable")
        parent = parents[0]
        if (
            parent["parent_schema"],
            parent["parent_name"],
            parent["parent_column"],
        ) != row.sequence_parent or parent["parent_owner"] not in {row.owner, transition_owner}:
            raise AuthorityError("Managed sequence dependency differs from source")


def _revoke_relation(connection, actual, row: ObjectAuthority) -> None:
    kind = "SEQUENCE" if row.kind == ObjectKind.SEQUENCE else "TABLE"
    target = _qualified(row)
    for acl in _rows(connection, _RELATION_ACL, oid=actual["oid"]):
        grantee = "PUBLIC" if acl["role"] == "PUBLIC" else quote_identifier(acl["role"])
        connection.exec_driver_sql(f"REVOKE ALL PRIVILEGES ON {kind} {target} FROM {grantee}")
    if row.kind != ObjectKind.SEQUENCE:
        # Table ACLs do not subsume column grants. A stale column grant must
        # not survive a seemingly correct table-level REVOKE/verification.
        for acl in _rows(connection, _COLUMN_ACL, oid=actual["oid"]):
            grantee = "PUBLIC" if acl["role"] == "PUBLIC" else quote_identifier(acl["role"])
            column = quote_identifier(acl["name"])
            connection.exec_driver_sql(f"REVOKE ALL ({column}) ON TABLE {target} FROM {grantee}")


def _grant_relation(connection, row: ObjectAuthority) -> None:
    kind = "SEQUENCE" if row.kind == ObjectKind.SEQUENCE else "TABLE"
    for permission in row.permissions:
        verbs = ", ".join(permission.verbs)
        if permission.columns:
            columns = ", ".join(quote_identifier(value) for value in permission.columns)
            verbs = ", ".join(f"{verb} ({columns})" for verb in permission.verbs)
        connection.exec_driver_sql(
            f"GRANT {verbs} ON {kind} {_qualified(row)} TO {quote_identifier(permission.role)}"
        )


def verify_relation(connection, actual, row: ObjectAuthority) -> None:
    expected = {
        (permission.role, verb)
        for permission in row.permissions
        if not permission.columns
        for verb in permission.verbs
    }
    observed = _rows(connection, _RELATION_ACL, oid=actual["oid"])
    if any(acl["grantable"] is not False for acl in observed):
        raise AuthorityError("Managed database privilege differs from authority")
    if {(acl["role"], acl["verb"]) for acl in observed} != expected:
        raise AuthorityError("Managed database privilege differs from authority")
    expected_columns = {
        (permission.role, column, verb)
        for permission in row.permissions
        for column in permission.columns
        for verb in permission.verbs
    }
    columns = _rows(connection, _COLUMN_ACL, oid=actual["oid"])
    if any(acl["grantable"] is not False for acl in columns):
        raise AuthorityError("Managed database column privilege differs from authority")
    if {(acl["role"], acl["name"], acl["verb"]) for acl in columns} != expected_columns:
        raise AuthorityError("Managed database column privilege differs from authority")


def converge_authority(
    connection, *, source_database: str | None = None, plans: tuple | None = None
) -> None:
    """Converge the selected source manifest on the one lock-owning session.

    Discovery supplies refusal/revocation inputs only. Every GRANT below comes
    from the source compiler; no observed relation, partition, sequence, owner
    or privilege can append a capability. Completion follows actual readback
    and commit; the caller separately owns successful lock/session release.
    """
    from butlers.migrations import maintenance_profile

    from .interfaces import (
        inspect_retired_interfaces,
        install_protected_state,
        retire_predecessor_interfaces,
        selected_protected_functions,
        verify_declared_security_shapes,
    )

    migration_owner = maintenance_profile(connection)
    database, maintenance_owner = connection.exec_driver_sql(
        "SELECT current_database(), session_user"
    ).one()
    source = source_database or connection.info.get("butlers.source_database", database)
    from .grants import (
        _owner_lookup_source_template_identity,
        compile_historical_priority_audit,
    )
    from .interfaces import _owner_lookup_revision_vector, validate_historical_priority_audit

    source_identity = _owner_lookup_source_template_identity()
    full_manifest = compile_relation_manifest(
        database, migration_owner, maintenance_owner, source_database=source
    )
    # Validate desired source selection without classifying an installed global
    # component from that partial/future target. Stamps still verify separately.
    select_migration_manifest(full_manifest, plans, installed_audit_vector=())
    verify_selected_revisions(connection, plans)
    installed = _owner_lookup_revision_vector(
        connection, full_manifest, source_identity=source_identity, selected_plans=plans
    )
    if _owner_lookup_source_template_identity() != source_identity:
        raise AuthorityError("Installed revision source changed during admission")
    manifest = select_migration_manifest(full_manifest, plans, installed_audit_vector=installed)
    historical_audit = compile_historical_priority_audit(
        manifest.profiles, migration_owner, installed
    )
    public_selected = "public" in manifest.schemas
    audit_context = manifest if public_selected else full_manifest
    # A selected global installer may normalize its exact ordinary preimage.
    # A separate module retains an already-closed component and cannot adopt
    # incomplete global authority or rewrite its function/dependencies.
    validate_historical_priority_audit(
        connection, audit_context, historical_audit, allow_legacy=public_selected
    )

    current_core = any(chain == "core" and revision is None for chain, _, revision in plans)
    historical = any(revision is not None for _, _, revision in plans)
    definitions = selected_protected_functions(manifest.profiles, migration_owner, plans)
    retired = (
        inspect_retired_interfaces(connection, migration_owner) if current_core else ((), (), False)
    )
    retired_identities = {(row[0], row[1], row[2]) for row in retired[0]}
    schemas = tuple(sorted(set(manifest.schemas) | {row.schema for row in definitions}))
    # Known source functions in an already-present unselected module remain
    # source-declared; a selected current chain determines required presence.
    from .grants import deployment_function_plans

    deployment_plans = (("core", None, None), *deployment_function_plans(manifest.profiles))
    current_ordinary = compile_function_manifest(manifest.profiles, migration_owner, plans)
    known_ordinary = compile_function_manifest(
        manifest.profiles, migration_owner, tuple(deployment_plans) + tuple(plans)
    )
    known_ordinary = tuple(row for row in known_ordinary if row.schema in schemas)
    if historical_audit is not None and public_selected:
        known_ordinary += (historical_audit.definition,)
    if historical:
        from dataclasses import replace

        current_ordinary = tuple(replace(row, executors=()) for row in current_ordinary)
        known_ordinary = tuple(replace(row, executors=()) for row in known_ordinary)
    # D13 attention/step-up retain their independently validated no-input
    # installer protocol. Their namespaces are outside this ordinary registry.
    function_schemas = tuple(
        schema for schema in schemas if schema not in {"dashboard_attention", "dashboard_stepup"}
    )
    function_population = tuple(
        row
        for row in _rows(connection, _FUNCTIONS, schemas=list(function_schemas))
        if (row["schema"], row["name"], row["arguments"].replace(", ", ","))
        not in retired_identities
    )
    # Protected functions may be introduced by the fixed source installer below;
    # ordinary chain functions must already be present at their selected head.
    function_bound = validate_function_population(
        function_population,
        (*known_ordinary, *definitions),
        {row.identity for row in current_ordinary},
        migration_owner=migration_owner,
    )
    observed = _rows(connection, _RELATIONS, schemas=list(schemas))
    try:
        declared = bind_partition_relations(
            observed,
            manifest.relations,
            _rows(connection, _PARTITION_LINKS, oids=[row["oid"] for row in observed]),
            transition_owner=migration_owner if not historical else None,
        )
    except AuthorityError as error:
        _add_relation_failure_note(
            error,
            observed,
            manifest.relations,
            phase="bind_partitions",
            migration_owner=migration_owner,
            maintenance_owner=maintenance_owner,
        )
        raise
    try:
        bound = validate_relations(
            observed,
            declared,
            administrative_history=historical,
            transition_owner=migration_owner if not historical else None,
            historical_audit=historical_audit,
        )
    except AuthorityError as error:
        _add_relation_failure_note(
            error,
            observed,
            declared,
            phase="validate_relations",
            migration_owner=migration_owner,
            maintenance_owner=maintenance_owner,
        )
        raise
    validate_sequence_bindings(
        bound,
        _rows(
            connection,
            _SEQUENCE_LINKS,
            oids=[actual["oid"] for actual, row in bound if row.kind == ObjectKind.SEQUENCE],
        ),
        transition_owner=migration_owner if not historical else None,
    )
    namespace_owners = {schema: frozenset({migration_owner}) for schema in schemas}
    namespace_owners.update(
        {
            schema: frozenset(owners)
            for schema, owners in {
                "public": {migration_owner, "pg_database_owner"},
                "dnd_generation_private": {"dnd_generation_owner"},
                "restore_drill_executor": {"restore_drill_executor_owner"},
                "dashboard_attention": {maintenance_owner},
                "dashboard_stepup": {maintenance_owner},
            }.items()
            if schema in schemas
        }
    )
    namespaces = validate_namespaces(
        _rows(connection, _NAMESPACE_CATALOG, schemas=list(schemas)), namespace_owners
    )
    if any(revision is None for _, _, revision in plans):
        validate_ordinary_purpose_owners(connection, known_ordinary)
        verify_declared_security_shapes(
            connection, manifest, observed, exclude_protected=True, allow_previous_ordinary=True
        )
    # Every current object/dependency/namespace/revision above is checked before
    # the first installer or ACL effect. The installer checks complete fixed
    # protected shapes and owners before changing any managed definition.
    if _owner_lookup_source_template_identity() != source_identity:
        raise AuthorityError("Installed revision source changed before convergence")
    if historical_audit is not None and public_selected:
        from .interfaces import normalize_historical_priority_audit

        normalize_historical_priority_audit(connection, historical_audit)
    if current_core:
        install_protected_state(connection, manifest.profiles, migration_owner)
        retire_predecessor_interfaces(connection, retired)
        verify_declared_security_shapes(
            connection, manifest, observed, allow_previous_ordinary=True
        )
    ordinary_bound = tuple(
        (actual, row)
        for actual, row in function_bound
        if row.identity in {definition.identity for definition in known_ordinary}
        and (historical_audit is None or row.identity != historical_audit.definition.identity)
    )
    if any(revision is None for _, _, revision in plans):
        current_identities = {row.identity for row in current_ordinary}
        converge_ordinary_functions(
            connection,
            tuple(
                (actual, row)
                for actual, row in ordinary_bound
                if not historical or row.identity in current_identities
            ),
        )
        if not historical:
            from .interfaces import converge_ordinary_security_policies

            converge_ordinary_security_policies(
                connection,
                manifest,
                only={
                    (actual["schema"], actual["name"])
                    for actual, row in bound
                    if row.kind == ObjectKind.TABLE
                },
            )
            for actual, row in bound:
                if actual["owner"] != row.owner:
                    kind = _relation_ddl_kind(row)
                    connection.exec_driver_sql(
                        f"ALTER {kind} {_qualified(row)} OWNER TO {quote_identifier(row.owner)}"
                    )
            verify_declared_security_shapes(connection, manifest, observed)
    if historical:
        # A bounded administrative catalog cannot confer serving authority. Do
        # not rewrite historical ordinary bodies to a later revision merely to
        # close their existing ACLs; all observed identities were source-bound
        # and owner-checked above before these revocation-only effects.
        close_administrative_function_privileges(connection, function_bound)
    permissions = {schema: set(roles) for schema, roles in namespace_permissions(declared).items()}
    for row in declared:
        permissions.setdefault(row.schema, set()).add(row.owner)
    for definition in (*definitions, *known_ordinary):
        permissions.setdefault(definition.schema, set()).update(
            (definition.owner, *definition.executors)
        )
    if "dashboard_auth" in schemas:
        permissions["dashboard_auth"].add("dashboard_auth_api")
    for schema in ("dashboard_attention", "dashboard_stepup"):
        if schema in schemas:
            permissions.setdefault(schema, set()).update(("dashboard_auth_api", migration_owner))
            permissions[schema].update(profile.role for profile in manifest.profiles)
    owners = tuple(
        sorted(
            {row.owner for row in declared}
            | {row.owner for row in (*definitions, *known_ordinary)}
            | {migration_owner, maintenance_owner}
        )
    )
    converge_default_privileges(connection, schemas, owners)
    from .grants import ordinary_schema_creators

    converge_namespaces(
        connection,
        namespaces,
        permissions,
        creators=ordinary_schema_creators(declared) if not historical else {},
    )
    for actual, row in bound:
        _revoke_relation(connection, actual, row)
        _grant_relation(connection, row)
    for actual, row in bound:
        verify_relation(connection, actual, row)
    after = _rows(connection, _RELATIONS, schemas=list(schemas))
    validate_relations(
        after, declared, administrative_history=historical, historical_audit=historical_audit
    )
    if {(row["oid"], row["schema"], row["name"]) for row in after} != {
        (row["oid"], row["schema"], row["name"]) for row in observed
    }:
        raise AuthorityError("Managed database population changed during convergence")
    from .grants import historical_priority_audit_capabilities
    from .interfaces import _fixed_purpose_capability_matches

    if not _fixed_purpose_capability_matches(
        connection,
        historical_priority_audit_capabilities(audit_context, historical_audit),
        purpose="priority_contact_audit_owner",
    ):
        raise AuthorityError("Final historical priority audit capabilities differ from source")
    connection.commit()


_NAMESPACE_CATALOG = """
SELECT n.oid, n.nspname AS name, r.rolname AS owner
FROM pg_catalog.pg_namespace n
JOIN pg_catalog.pg_roles r ON r.oid=n.nspowner
WHERE n.nspname=ANY(:schemas)
ORDER BY n.nspname
"""
_NAMESPACE_ACL = """
SELECT CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE r.rolname END AS role,
       a.privilege_type AS verb, a.is_grantable AS grantable
FROM pg_catalog.pg_namespace n CROSS JOIN LATERAL pg_catalog.aclexplode(
    COALESCE(n.nspacl,pg_catalog.acldefault('n',n.nspowner))) a
LEFT JOIN pg_catalog.pg_roles r ON r.oid=a.grantee
WHERE n.oid=:oid AND a.grantee<>n.nspowner
"""
_DEFAULT_ACL = """
SELECT owner.rolname AS owner, n.nspname AS schema, d.defaclobjtype AS kind,
       CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE grantee.rolname END AS role,
       a.privilege_type AS verb
FROM pg_catalog.pg_default_acl d
JOIN pg_catalog.pg_roles owner ON owner.oid=d.defaclrole
LEFT JOIN pg_catalog.pg_namespace n ON n.oid=d.defaclnamespace
CROSS JOIN LATERAL pg_catalog.aclexplode(d.defaclacl) a
LEFT JOIN pg_catalog.pg_roles grantee ON grantee.oid=a.grantee
WHERE (n.nspname=ANY(:schemas) OR (d.defaclnamespace=0 AND owner.rolname=ANY(:owners)))
 AND a.grantee<>d.defaclrole
"""
_DEFAULT_KINDS = {"r": "TABLES", "S": "SEQUENCES", "f": "FUNCTIONS", "T": "TYPES", "n": "SCHEMAS"}


def namespace_permissions(declared: tuple[ObjectAuthority, ...]) -> dict[str, frozenset[str]]:
    """USAGE follows fixed source operations; names from the catalog add nothing."""
    result: dict[str, set[str]] = {}
    for row in declared:
        result.setdefault(row.schema, set()).update(
            permission.role for permission in row.permissions
        )
    return {schema: frozenset(roles) for schema, roles in result.items()}


def validate_namespaces(observed, expected_owners: dict[str, frozenset[str]]):
    """Do not turn a compatible schema label into an ownership handoff."""
    seen = set()
    bound = []
    for actual in observed:
        name = actual["name"]
        if name in seen or name not in expected_owners:
            raise AuthorityError("Managed namespace identity is ambiguous")
        seen.add(name)
        if actual["owner"] not in expected_owners[name]:
            raise AuthorityError("Managed namespace ownership prerequisite is unavailable")
        bound.append(actual)
    if seen != set(expected_owners):
        raise AuthorityError("Required managed namespace is unavailable")
    return tuple(bound)


def converge_namespaces(
    connection, bound, permissions: dict[str, frozenset[str]], *, creators=None
) -> None:
    """Remove observed excess authority; grant only the separately compiled USAGE set."""
    creators = creators or {}
    for actual in bound:
        target = quote_identifier(actual["name"])
        for acl in _rows(connection, _NAMESPACE_ACL, oid=actual["oid"]):
            grantee = "PUBLIC" if acl["role"] == "PUBLIC" else quote_identifier(acl["role"])
            connection.exec_driver_sql(f"REVOKE ALL ON SCHEMA {target} FROM {grantee}")
        for role in sorted(permissions.get(actual["name"], ())):
            if role == actual["owner"]:
                continue
            connection.exec_driver_sql(
                f"GRANT USAGE ON SCHEMA {target} TO {quote_identifier(role)}"
            )
        for role in sorted(creators.get(actual["name"], ())):
            connection.exec_driver_sql(
                f"GRANT CREATE ON SCHEMA {target} TO {quote_identifier(role)}"
            )
    for actual in bound:
        expected = {
            (role, "USAGE")
            for role in permissions.get(actual["name"], ())
            if role != actual["owner"]
        }
        expected.update((role, "CREATE") for role in creators.get(actual["name"], ()))
        rows = _rows(connection, _NAMESPACE_ACL, oid=actual["oid"])
        if (
            any(row["grantable"] is not False for row in rows)
            or {(row["role"], row["verb"]) for row in rows} != expected
        ):
            raise AuthorityError("Managed namespace privileges differ from source authority")


def converge_default_privileges(connection, schemas: tuple[str, ...], owners: tuple[str, ...]):
    """New managed objects start closed, never with a future-object role grant.

    Observed ACLs supply only revocation targets. No discovered owner, role,
    privilege or object can create a GRANT row. Source owners' implicit PUBLIC
    function/type defaults are revoked explicitly even without a catalog row.
    """
    rows = _rows(connection, _DEFAULT_ACL, schemas=list(schemas), owners=list(owners))
    if any(row["kind"] not in _DEFAULT_KINDS for row in rows):
        raise AuthorityError("Managed default privilege type is unavailable")
    for row in rows:
        owner = quote_identifier(row["owner"])
        schema = "" if row["schema"] is None else " IN SCHEMA " + quote_identifier(row["schema"])
        grantee = "PUBLIC" if row["role"] == "PUBLIC" else quote_identifier(row["role"])
        connection.exec_driver_sql(
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner}{schema} "
            f"REVOKE ALL ON {_DEFAULT_KINDS[row['kind']]} FROM {grantee}"
        )
    for owner in owners:
        for kind in ("FUNCTIONS", "TYPES"):
            connection.exec_driver_sql(
                f"ALTER DEFAULT PRIVILEGES FOR ROLE {quote_identifier(owner)} "
                f"REVOKE ALL ON {kind} FROM PUBLIC"
            )
    if _rows(connection, _DEFAULT_ACL, schemas=list(schemas), owners=list(owners)):
        raise AuthorityError("Managed default privileges remain available")


def verify_selected_revisions(connection, plans: tuple | None) -> None:
    """Verify each selected chain's exact source revision on the held session."""
    from butlers.migrations import get_chain_head, get_chain_revision_ids

    if plans is None:
        raise AuthorityError("A complete selected migration plan is required")
    by_schema = {}
    for chain, schema, revision in plans:
        target = "public" if schema is None else schema
        revisions = get_chain_revision_ids(chain)
        expected = get_chain_head(chain) if revision is None else revision
        if expected not in revisions:
            raise AuthorityError("Selected migration revision is not source-owned")
        if target not in by_schema:
            rows = connection.exec_driver_sql(
                f"SELECT version_num FROM {quote_identifier(target)}.alembic_version"
            )
            by_schema[target] = frozenset(row[0] for row in rows)
        if by_schema[target] & revisions != {expected}:
            raise AuthorityError("Selected migration revision is not installed")


_FUNCTIONS = """
SELECT p.oid, n.nspname AS schema, p.proname AS name,
       pg_catalog.oidvectortypes(p.proargtypes) AS arguments,
       r.rolname AS owner
FROM pg_catalog.pg_proc p
JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
JOIN pg_catalog.pg_roles r ON r.oid=p.proowner
WHERE n.nspname=ANY(:schemas)
AND NOT EXISTS(SELECT FROM pg_catalog.pg_depend dependency
 WHERE dependency.classid='pg_catalog.pg_proc'::regclass AND dependency.objid=p.oid
 AND dependency.deptype='e')
ORDER BY n.nspname,p.proname,p.oid
"""


def validate_function_population(observed, definitions, required, *, migration_owner=None):
    """Overloads and owners are preconditions; discovery cannot authorize EXECUTE."""
    indexed = {definition.identity: definition for definition in definitions}
    if len(indexed) != len(definitions):
        raise AuthorityError("Source function authority is ambiguous")
    result = []
    seen = set()
    for row in observed:
        identity = row["schema"], row["name"], row["arguments"].replace(", ", ",")
        definition = indexed.get(identity)
        if definition is None or identity in seen:
            raise AuthorityError("Managed function is not declared")
        seen.add(identity)
        from .roles import ORDINARY_PURPOSE_OWNERS

        if row["owner"] != definition.owner and not (
            migration_owner is not None
            and row["owner"] == migration_owner
            and definition.security_definer
            and definition.owner in ORDINARY_PURPOSE_OWNERS
        ):
            raise AuthorityError("Managed function ownership prerequisite is unavailable")
        result.append((row, definition))
    if not set(required) <= seen:
        raise AuthorityError("Required managed function is unavailable")
    return tuple(result)


def close_administrative_function_privileges(connection, bound) -> None:
    """Close historical function ACLs without adopting or changing their bodies."""
    from .interfaces import _FUNCTION_ACL, _signature

    for actual, definition in bound:
        target = _signature(definition)
        for acl in _rows(connection, _FUNCTION_ACL, oid=actual["oid"]):
            role = "PUBLIC" if acl["role"] == "PUBLIC" else quote_identifier(acl["role"])
            connection.exec_driver_sql(f"REVOKE ALL ON FUNCTION {target} FROM {role}")
        if _rows(connection, _FUNCTION_ACL, oid=actual["oid"]):
            raise AuthorityError("Historical database function privilege remains available")


def validate_ordinary_purpose_owners(connection, definitions) -> None:
    """Check actual owner profiles independently of compiler/catalog equality."""
    from .roles import ORDINARY_PURPOSE_OWNERS

    owners = sorted({row.owner for row in definitions if row.security_definer})
    if any(owner not in ORDINARY_PURPOSE_OWNERS for owner in owners):
        raise AuthorityError("Ordinary definer purpose is not declared")
    if not owners:
        return
    valid = connection.execute(
        text("""
        WITH owners AS (SELECT * FROM pg_catalog.pg_roles WHERE rolname=ANY(:owners))
        SELECT (SELECT count(*)=:count AND bool_and(NOT rolcanlogin AND NOT rolinherit
          AND NOT rolsuper AND NOT rolcreaterole AND NOT rolcreatedb
          AND NOT rolreplication AND NOT rolbypassrls) FROM owners)
          AND NOT EXISTS(SELECT FROM pg_catalog.pg_auth_members
            WHERE roleid IN (SELECT oid FROM owners) OR member IN (SELECT oid FROM owners))
    """),
        {"owners": owners, "count": len(owners)},
    ).scalar_one()
    if valid is not True:
        raise AuthorityError("Ordinary definer owner profile is unavailable")


def _add_ordinary_ddl_failure_note(error, definition) -> None:
    """Add a bounded hashed failure note without changing admission or the error.

    Only source-function identity/body digests and a closed driver category/code
    cross this diagnostic boundary. SQL, messages, parameters and actors do not.
    """
    try:
        import hashlib
        import json
        import re

        identity = definition.identity
        sql = definition.sql
        if (
            not isinstance(identity, tuple)
            or len(identity) != 3
            or any(
                not isinstance(value, str) or len(value.encode("utf-8")) > 4096
                for value in identity
            )
            or not isinstance(sql, str)
            or len(sql.encode("utf-8")) > 1024 * 1024
        ):
            return
        original = error.orig
        driver_class = type(original).__name__
        allowed_classes = {
            "ProgrammingError",
            "UndefinedFunction",
            "UndefinedTable",
            "UndefinedColumn",
            "SyntaxError",
            "InvalidFunctionDefinition",
            "InvalidSchemaName",
            "InsufficientPrivilege",
            "DatatypeMismatch",
            "DuplicateFunction",
            "InvalidParameterValue",
            "DependentObjectsStillExist",
            "InvalidObjectDefinition",
        }
        sqlstate = getattr(original, "sqlstate", None)
        if not isinstance(sqlstate, str) or re.fullmatch(r"[0-9A-Z]{5}", sqlstate) is None:
            # psycopg2 is the current migration driver; psycopg uses sqlstate.
            sqlstate = getattr(original, "pgcode", None)
        if not isinstance(sqlstate, str) or re.fullmatch(r"[0-9A-Z]{5}", sqlstate) is None:
            sqlstate = "UNKNOWN"
        payload = {
            "version": 1,
            "phase": "ordinary_ddl",
            "exception": "ProgrammingError",
            "identity_sha256": hashlib.sha256(
                json.dumps(identity, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
            "sql_sha256": hashlib.sha256(sql.encode("utf-8")).hexdigest(),
            "driver_class": driver_class if driver_class in allowed_classes else "UNKNOWN",
            "sqlstate": sqlstate,
        }
        error.add_note(
            "DB_AUTHORITY_ORDINARY_DDL_DIAGNOSTIC "
            + json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        )
    except Exception:
        # Failure observation is subordinate to the exact original exception.
        pass


def converge_ordinary_functions(connection, bound) -> None:
    """Apply only fixed current bodies/ACLs under explicit maintenance authority."""
    from .interfaces import (
        _FUNCTION_ACL,
        _signature,
        function_matches,
        inspect_function,
        require_maintenance,
    )

    validate_ordinary_purpose_owners(connection, tuple(definition for _, definition in bound))
    changes = []
    for row, definition in bound:
        actual = inspect_function(connection, definition)
        if actual is None or actual["oid"] != row["oid"] or actual["owner"] != row["owner"]:
            raise AuthorityError("Managed function population changed during convergence")
        if not function_matches(connection, definition, actual):
            changes.append((row, definition))
    if changes:
        require_maintenance(connection)
    for row, definition in changes:
        try:
            connection.exec_driver_sql(definition.sql.replace("%", "%%"))
        except ProgrammingError as error:
            _add_ordinary_ddl_failure_note(error, definition)
            raise
        target = _signature(definition)
        # The complete preflight admits only an exact fixed purpose owner or
        # the independently proved normal migration owner at this transition.
        connection.exec_driver_sql(
            f"ALTER FUNCTION {target} OWNER TO {quote_identifier(definition.owner)}"
        )
        for acl in _rows(connection, _FUNCTION_ACL, oid=row["oid"]):
            role = "PUBLIC" if acl["role"] == "PUBLIC" else quote_identifier(acl["role"])
            connection.exec_driver_sql(f"REVOKE ALL ON FUNCTION {target} FROM {role}")
        for role in definition.executors:
            if role != definition.owner:
                connection.exec_driver_sql(
                    f"GRANT EXECUTE ON FUNCTION {target} TO {quote_identifier(role)}"
                )
        if not function_matches(connection, definition, inspect_function(connection, definition)):
            raise AuthorityError("Managed function catalog differs from source authority")


# Operator-selected R0/R1 use the same session lock as migrations. A capsule is
# nonsecret catalog metadata, never a serving capability or arbitrary SQL file.
_PREDECESSOR_COMMIT = "30b376895e1108977bf1b80bf54fcbdaaa3d64ad"
_PREDECESSOR_REPLAY_SHA256 = "2d3ed1a4b7e887074c6ac5362e6c0f4ce7741683c69a3dd3c4ce63e667ba75ad"
_ROLLBACK_FUNCTIONS = """
SELECT n.nspname AS schema, p.proname AS name,
 replace(pg_catalog.oidvectortypes(p.proargtypes),', ', ',') AS signature,
 r.rolname AS owner, p.prosecdef AS definer, p.proconfig AS settings,
 pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(p.prosrc,'UTF8')),'hex') AS digest
FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
JOIN pg_catalog.pg_roles r ON r.oid=p.proowner
WHERE n.nspname=ANY(:schemas) AND NOT EXISTS(
 SELECT FROM pg_catalog.pg_depend d WHERE d.classid='pg_catalog.pg_proc'::regclass
 AND d.objid=p.oid AND d.deptype='e') ORDER BY n.nspname,p.proname,p.proargtypes::text
"""
_ROLLBACK_ROLES = """
SELECT rolname AS name, rolcanlogin AS login, rolinherit AS inherit,
 rolsuper AS super, rolcreatedb AS createdb, rolcreaterole AS createrole,
 rolreplication AS replication, rolbypassrls AS bypassrls
FROM pg_catalog.pg_roles WHERE rolname=ANY(:roles) ORDER BY rolname
"""
_ROLLBACK_EFFECTIVE_DEFAULTS = """
WITH explicit AS (
 SELECT d.defaclrole AS owner,d.defaclnamespace AS namespace,
        d.defaclobjtype AS kind,d.defaclacl AS acl
 FROM pg_catalog.pg_default_acl d LEFT JOIN pg_catalog.pg_namespace n ON n.oid=d.defaclnamespace
 WHERE (n.nspname=ANY(:schemas) OR (d.defaclnamespace=0 AND
   d.defaclrole IN(SELECT oid FROM pg_catalog.pg_roles WHERE rolname=ANY(:owners))))
), effective AS (
 SELECT * FROM explicit
 UNION ALL
 SELECT r.oid,0,k.kind,pg_catalog.acldefault(k.kind,r.oid)
 FROM pg_catalog.pg_roles r CROSS JOIN (VALUES ('f'::"char"),('T'::"char")) k(kind)
 WHERE r.rolname=ANY(:owners) AND NOT EXISTS(
   SELECT FROM explicit e WHERE e.owner=r.oid AND e.namespace=0 AND e.kind=k.kind)
)
SELECT owner.rolname AS owner,n.nspname AS schema,e.kind,
 CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE grantee.rolname END AS role,
 a.privilege_type AS verb,a.is_grantable AS grantable
FROM effective e JOIN pg_catalog.pg_roles owner ON owner.oid=e.owner
LEFT JOIN pg_catalog.pg_namespace n ON n.oid=e.namespace
CROSS JOIN LATERAL pg_catalog.aclexplode(e.acl) a
LEFT JOIN pg_catalog.pg_roles grantee ON grantee.oid=a.grantee
WHERE a.grantee<>e.owner ORDER BY owner,schema,e.kind,role,verb
"""
_ROLLBACK_RELATION_SHAPE = """
SELECT pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(jsonb_build_object(
 'columns',(SELECT COALESCE(jsonb_agg(jsonb_build_array(a.attname,
   pg_catalog.format_type(a.atttypid,a.atttypmod),a.attnotnull,a.attidentity,a.attgenerated,
   pg_catalog.pg_get_expr(d.adbin,d.adrelid)) ORDER BY a.attnum),'[]'::jsonb)
   FROM pg_catalog.pg_attribute a LEFT JOIN pg_catalog.pg_attrdef d
    ON d.adrelid=a.attrelid AND d.adnum=a.attnum
   WHERE a.attrelid=:oid AND a.attnum>0 AND NOT a.attisdropped),
 'constraints',(SELECT COALESCE(jsonb_agg(jsonb_build_array(c.conname,c.contype,
   pg_catalog.pg_get_constraintdef(c.oid),c.convalidated,c.condeferrable,c.condeferred)
   ORDER BY c.conname),'[]'::jsonb) FROM pg_catalog.pg_constraint c WHERE c.conrelid=:oid),
 'indexes',(SELECT COALESCE(jsonb_agg(pg_catalog.pg_get_indexdef(i.indexrelid)
   ORDER BY pg_catalog.pg_get_indexdef(i.indexrelid)),'[]'::jsonb)
   FROM pg_catalog.pg_index i WHERE i.indrelid=:oid),
 'policies',(SELECT COALESCE(jsonb_agg(jsonb_build_array(p.polname,p.polcmd,p.polpermissive,
   (SELECT array_agg(CASE WHEN role=0 THEN 'PUBLIC' ELSE pg_catalog.pg_get_userbyid(role) END
     ORDER BY role) FROM unnest(p.polroles) role),
   pg_catalog.pg_get_expr(p.polqual,p.polrelid),pg_catalog.pg_get_expr(p.polwithcheck,p.polrelid))
   ORDER BY p.polname),'[]'::jsonb) FROM pg_catalog.pg_policy p WHERE p.polrelid=:oid),
 'triggers',(SELECT COALESCE(jsonb_agg(jsonb_build_array(t.tgname,t.tgenabled,
   pg_catalog.pg_get_triggerdef(t.oid,true)) ORDER BY t.tgname),'[]'::jsonb)
   FROM pg_catalog.pg_trigger t WHERE t.tgrelid=:oid AND NOT t.tgisinternal)
)::text,'UTF8')),'hex')
"""
_ROLLBACK_DATABASE_ACL = """
SELECT CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE role.rolname END AS role,
       a.privilege_type AS verb,a.is_grantable AS grantable
FROM pg_catalog.pg_database d CROSS JOIN LATERAL pg_catalog.aclexplode(
 COALESCE(d.datacl,pg_catalog.acldefault('d',d.datdba))) a
LEFT JOIN pg_catalog.pg_roles role ON role.oid=a.grantee
WHERE d.datname=current_database() AND a.grantee<>d.datdba
ORDER BY role,verb
"""
_ROLLBACK_MEMBERSHIPS = """
SELECT parent.rolname AS role, member.rolname AS member, m.admin_option AS admin,
 COALESCE((to_jsonb(m)->>'inherit_option')::boolean,true) AS inherit,
 COALESCE((to_jsonb(m)->>'set_option')::boolean,true) AS set
FROM pg_catalog.pg_auth_members m JOIN pg_catalog.pg_roles parent ON parent.oid=m.roleid
JOIN pg_catalog.pg_roles member ON member.oid=m.member
WHERE parent.rolname=ANY(:roles) OR member.rolname=ANY(:roles)
ORDER BY parent.rolname,member.rolname
"""
_ALLOWED_ACL_VERBS = frozenset(
    {
        "SELECT",
        "INSERT",
        "UPDATE",
        "DELETE",
        "TRUNCATE",
        "REFERENCES",
        "TRIGGER",
        "USAGE",
        "CREATE",
        "CONNECT",
        "TEMPORARY",
        "EXECUTE",
    }
)


def predecessor_function_sources(profiles):
    """Bind pinned administrative bodies to actual declared local schemas."""
    import hashlib
    import re

    from .grants import _MODULE_MIGRATION_CHAINS
    from .interfaces import _PREDECESSOR_LOCAL_FUNCTION_CHAINS, _PREDECESSOR_ROLLBACK_SOURCES

    result = {}
    for schema, name, signature, header, body in _PREDECESSOR_ROLLBACK_SOURCES:
        targets = {schema}
        if schema == "__SELECTED_SCHEMA__":
            chain = _PREDECESSOR_LOCAL_FUNCTION_CHAINS[name]
            targets = {"public"} if chain == "core" else set()
            for profile in profiles:
                if chain in {"core", profile.primary_schema} or chain in {
                    _MODULE_MIGRATION_CHAINS.get(module, module) for module in profile.modules
                }:
                    targets.update(
                        profile.private_schemas
                        if chain == "memory" and profile.private_schemas
                        else (profile.primary_schema,)
                    )
        for selected in sorted(targets):
            actual_body = body.replace("__SELECTED_SCHEMA__", selected)
            actual_header = header.replace("__SELECTED_SCHEMA__", selected)
            actual_header = re.sub(
                r"CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION",
                "CREATE OR REPLACE FUNCTION",
                actual_header,
                count=1,
                flags=re.I,
            )
            digest = hashlib.sha256(actual_body.encode()).hexdigest()
            result[selected, name, signature, digest] = (
                actual_header + "AS $preserving_rollback$" + actual_body + "$preserving_rollback$;"
            )

    return result


def _rollback_actor(connection) -> str:
    from .interfaces import require_maintenance

    require_maintenance(connection)
    return connection.exec_driver_sql("SELECT session_user").scalar_one()


def _rollback_source_manifest(connection, migration_role: str):
    from .roles import bootstrap_role_manifest

    database, actor = connection.exec_driver_sql("SELECT current_database(), session_user").one()
    source = connection.info.get("butlers.source_database", database)
    bootstrap_role_manifest(database, migration_role, source_database=source)
    return compile_relation_manifest(database, migration_role, actor, source_database=source)


def require_serving_quiescence(connection, roles: tuple[str, ...]) -> None:
    """Observe actual sessions whose LOGIN can reach a managed serving role."""
    quiet = connection.execute(
        text("""
        WITH RECURSIVE principals(oid) AS (
          SELECT oid FROM pg_catalog.pg_roles WHERE rolname=ANY(:roles)
          UNION SELECT m.member FROM pg_catalog.pg_auth_members m
                 JOIN principals p ON p.oid=m.roleid
        )
        SELECT NOT EXISTS(SELECT FROM pg_catalog.pg_stat_activity a
          JOIN pg_catalog.pg_roles r ON r.rolname=a.usename
          JOIN principals p ON p.oid=r.oid
          WHERE a.pid<>pg_catalog.pg_backend_pid())
    """),
        {"roles": list(roles)},
    ).scalar_one()
    if quiet is not True:
        raise AuthorityError("Serving quiescence is unavailable; retain stopped rollback state")


def _rollback_roles(manifest, actor: str) -> set[str]:
    from .roles import PURPOSE_OWNERS

    return (
        {p.role for p in manifest.profiles}
        | set(PURPOSE_OWNERS)
        | {
            manifest.migration_owner,
            actor,
            "dashboard_api",
            "dashboard_auth_api",
            "connector_writer",
            "credential_store_writer",
            "restore_drill_executor",
        }
    )


def _metadata_rows(rows, keys, identity):
    """Reject malformed/duplicate metadata rather than interpolating a capsule."""
    if not isinstance(rows, list):
        raise AuthorityError("Rollback catalog population is malformed")
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != set(keys):
            raise AuthorityError("Rollback catalog row is malformed")
        key = tuple(row[name] for name in identity)
        try:
            duplicate = key in seen
            seen.add(key)
        except TypeError as exc:
            raise AuthorityError("Rollback catalog identity is malformed") from exc
        if duplicate:
            raise AuthorityError("Rollback catalog identity is duplicated")
    return rows


def _validate_rollback_metadata(catalog, manifest, actor, roles):
    from .interfaces import _RETIRED_ADMIN_SCHEMAS

    attributes = {"login", "inherit", "super", "createdb", "createrole", "replication", "bypassrls"}
    for row in _metadata_rows(catalog["roles"], {"name", *attributes}, ("name",)):
        if row["name"] not in roles or any(type(row[key]) is not bool for key in attributes):
            raise AuthorityError("Rollback role is not source-bound")
        unsafe = {
            key
            for key in ("super", "createdb", "createrole", "replication", "bypassrls")
            if row[key]
        }
        if row["name"] == "restore_drill_executor":
            unsafe.discard("createdb")
        if row["name"] != actor and unsafe:
            raise AuthorityError("Rollback cannot introduce privileged serving authority")
    if manifest.migration_owner not in {row["name"] for row in catalog["roles"]}:
        raise AuthorityError("Predecessor migration role is unavailable")
    _metadata_rows(catalog["schemas"], {"name", "owner", "acl"}, ("name",))
    _metadata_rows(
        catalog["relations"],
        {
            "schema",
            "name",
            "kind",
            "owner",
            "rls",
            "forced_rls",
            "partition",
            "parents",
            "shape",
            "acl",
            "columns",
        },
        ("schema", "name"),
    )
    _metadata_rows(
        catalog["functions"],
        {"schema", "name", "signature", "owner", "definer", "settings", "digest", "acl"},
        ("schema", "name", "signature"),
    )
    _metadata_rows(
        catalog["defaults"],
        {"owner", "schema", "kind", "role", "verb", "grantable"},
        ("owner", "schema", "kind", "role", "verb"),
    )
    for row in _metadata_rows(
        catalog["memberships"], {"role", "member", "admin", "inherit", "set"}, ("role", "member")
    ):
        if any(type(row[key]) is not bool for key in ("admin", "inherit", "set")):
            raise AuthorityError("Rollback membership options are malformed")
    for row in _metadata_rows(catalog["versions"], {"schema", "revisions"}, ("schema",)):
        if (
            not isinstance(row["revisions"], list)
            or not row["revisions"]
            or any(not isinstance(v, str) for v in row["revisions"])
            or row["revisions"] != sorted(set(row["revisions"]))
        ):
            raise AuthorityError("Rollback version vector is malformed")
    for row in _metadata_rows(catalog["configurations"], {"schema", "values"}, ("schema",)):
        expected = {"singleton", "migration_role", "bootstrap_role"}
        if row["schema"] == "runtime_attention_admin":
            expected |= {"interface_version", "producers_enabled", "producer_activated_at"}
        if row["schema"] not in _RETIRED_ADMIN_SCHEMAS or not isinstance(row["values"], list):
            raise AuthorityError("Rollback configuration is not source-bound")
        if len(row["values"]) != 1 or set(row["values"][0]) != expected:
            raise AuthorityError("Rollback configuration population is not source-bound")
        value = row["values"][0]
        if (
            value["singleton"] is not True
            or value["migration_role"] != manifest.migration_owner
            or value["bootstrap_role"] != actor
        ):
            raise AuthorityError("Rollback configuration actor is not source-bound")
        if row["schema"] == "runtime_attention_admin" and (
            type(value["interface_version"]) is not int
            or value["interface_version"] not in (1, 2)
            or type(value["producers_enabled"]) is not bool
        ):
            raise AuthorityError("Rollback configuration version is unsupported")
    for row in catalog["relations"]:
        import re

        if (
            not isinstance(row["shape"], str)
            or re.fullmatch(r"[0-9a-f]{64}", row["shape"]) is None
            or not isinstance(row["parents"], list)
            or any(
                not isinstance(parent, list)
                or len(parent) != 2
                or any(not isinstance(value, str) for value in parent)
                for parent in row["parents"]
            )
        ):
            raise AuthorityError("Rollback structural metadata is malformed")
        if row["kind"] not in {"r", "p", "v", "m", "S", "f"} or any(
            type(row[key]) is not bool for key in ("rls", "forced_rls", "partition")
        ):
            raise AuthorityError("Rollback relation shape is malformed")
    for row in catalog["functions"]:
        if (
            type(row["definer"]) is not bool
            or row["settings"] is not None
            and (
                not isinstance(row["settings"], list)
                or any(not isinstance(value, str) for value in row["settings"])
            )
        ):
            raise AuthorityError("Rollback function shape is malformed")


def _snapshot_authority_catalog(connection, manifest):
    """Stable catalog deparse, never application values or executable capsule SQL."""
    previous = connection.exec_driver_sql("SHOW search_path").scalar_one()
    connection.execute(text("SELECT pg_catalog.set_config('search_path','pg_catalog',true)"))
    result = _read_authority_catalog(connection, manifest)
    connection.execute(
        text("SELECT pg_catalog.set_config('search_path',:path,true)"), {"path": previous}
    )
    return result


def _read_authority_catalog(connection, manifest):
    """Read authority only: no passwords, application rows or secret values."""
    from .interfaces import _RETIRED_ADMIN_SCHEMAS

    schemas = tuple(sorted(set(manifest.schemas) | set(_RETIRED_ADMIN_SCHEMAS)))
    actor = connection.exec_driver_sql("SELECT session_user").scalar_one()
    roles = tuple(sorted(_rollback_roles(manifest, actor)))
    namespaces = [dict(row) for row in _rows(connection, _NAMESPACE_CATALOG, schemas=list(schemas))]
    for row in namespaces:
        row["acl"] = [dict(acl) for acl in _rows(connection, _NAMESPACE_ACL, oid=row.pop("oid"))]
    relations = [dict(row) for row in _rows(connection, _RELATIONS, schemas=list(schemas))]
    parents = {}
    for link in _rows(connection, _PARTITION_LINKS, oids=[row["oid"] for row in relations]):
        parents.setdefault(link["oid"], []).append([link["parent_schema"], link["parent_name"]])
    for row in relations:
        row["parents"] = parents.get(row["oid"], [])
        row["shape"] = connection.execute(
            text(_ROLLBACK_RELATION_SHAPE), {"oid": row["oid"]}
        ).scalar_one()
        row["acl"] = [dict(acl) for acl in _rows(connection, _RELATION_ACL, oid=row["oid"])]
        row["columns"] = [dict(acl) for acl in _rows(connection, _COLUMN_ACL, oid=row.pop("oid"))]
    functions = [dict(row) for row in _rows(connection, _ROLLBACK_FUNCTIONS, schemas=list(schemas))]
    for row in functions:
        target = quote_identifier(row["schema"]) + "." + quote_identifier(row["name"])
        oid = connection.execute(
            text("SELECT pg_catalog.to_regprocedure(:name)::oid"),
            {"name": target + "(" + row["signature"] + ")"},
        ).scalar_one()
        from .interfaces import _FUNCTION_ACL

        row["acl"] = [dict(acl) for acl in _rows(connection, _FUNCTION_ACL, oid=oid)]
    versions = []
    for schema in sorted(set(manifest.schemas)):
        target = quote_identifier(schema) + ".alembic_version"
        if (
            connection.execute(
                text("SELECT pg_catalog.to_regclass(:target) IS NOT NULL"), {"target": target}
            ).scalar_one()
            is True
        ):
            versions.append(
                dict(
                    schema=schema,
                    revisions=sorted(
                        row[0]
                        for row in connection.exec_driver_sql(f"SELECT version_num FROM {target}")
                    ),
                )
            )
    configurations = []
    for schema in _RETIRED_ADMIN_SCHEMAS:
        if any(row["schema"] == schema for row in relations):
            configurations.append(
                dict(
                    schema=schema,
                    values=[
                        dict(row)
                        for row in connection.exec_driver_sql(
                            f"SELECT * FROM {quote_identifier(schema)}.bootstrap_configuration"
                        ).mappings()
                    ],
                )
            )
    database = (
        connection.exec_driver_sql("""
        SELECT d.datname AS name, owner.rolname AS owner
        FROM pg_catalog.pg_database d JOIN pg_catalog.pg_roles owner ON owner.oid=d.datdba
        WHERE datname=current_database()
    """)
        .mappings()
        .one()
    )
    database = dict(database)
    database["acl"] = [dict(row) for row in _rows(connection, _ROLLBACK_DATABASE_ACL)]
    return dict(
        database=database,
        schemas=namespaces,
        relations=relations,
        functions=functions,
        roles=[dict(row) for row in _rows(connection, _ROLLBACK_ROLES, roles=list(roles))],
        memberships=[
            dict(row)
            for row in _rows(connection, _ROLLBACK_MEMBERSHIPS, roles=sorted(set(roles) - {actor}))
        ],
        defaults=[
            dict(row)
            for row in _rows(
                connection,
                _ROLLBACK_EFFECTIVE_DEFAULTS,
                schemas=list(schemas),
                owners=list(roles),
            )
        ],
        versions=versions,
        configurations=configurations,
    )


# Compiled without execution from the complete predecessor_commit migration
# graph and roster TOMLs. This rollback profile deliberately does not follow
# the live graph: core_281 and later/unmerged heads cannot authorize old-init.
_PREDECESSOR_CHAIN_HEADS = {
    "approvals": "approvals_018",
    "chronicler": "chronicler_026",
    "concierge": "concierge_002",
    "contacts": "contacts_005",
    "core": "core_280",
    "education": "education_006",
    "finance": "finance_018",
    "general": "gen_003",
    "google_drive": "google_drive_001",
    "health": "health_005",
    "home": "home_004",
    "lifestyle": "lifestyle_002",
    "memory": "mem_015",
    "messenger": "msg_004",
    "relationship": "rel_037",
    "switchboard": "sw_051",
    "travel": "travel_003",
    "whatsapp": "whatsapp_002",
}
_PREDECESSOR_PROFILE_LAYOUT = {
    "butler_chronicler_rw": ("chronicler", ("chronicler_mem",), ("chronicler", "memory")),
    "butler_concierge_rw": ("concierge", (), ("dashboard_read",)),
    "butler_education_rw": ("education", (), ("contacts", "education", "memory")),
    "butler_finance_rw": ("finance", (), ("calendar", "finance", "memory")),
    "butler_general_rw": ("general", (), ("calendar", "contacts", "general", "memory")),
    "butler_health_rw": (
        "health",
        (),
        (
            "approvals",
            "calendar",
            "contacts",
            "google_drive",
            "google_health",
            "health",
            "home_assistant",
            "memory",
        ),
    ),
    "butler_home_rw": (
        "home",
        (),
        ("approvals", "contacts", "google_drive", "home_assistant", "memory"),
    ),
    "butler_lifestyle_rw": (
        "lifestyle",
        (),
        ("calendar", "contacts", "lifestyle", "memory", "spotify", "steam"),
    ),
    "butler_messenger_rw": (
        "messenger",
        (),
        ("approvals", "calendar", "email", "telegram", "whatsapp"),
    ),
    "butler_qa_rw": ("qa", (), ("qa",)),
    "butler_relationship_rw": (
        "relationship",
        (),
        ("approvals", "calendar", "contacts", "memory", "relationship"),
    ),
    "butler_switchboard_rw": (
        "switchboard",
        (),
        (
            "approvals",
            "insight_broker",
            "memory",
            "owner_conditions_broker",
            "pipeline",
            "switchboard",
        ),
    ),
    "butler_travel_rw": ("travel", (), ("calendar", "email", "memory", "travel")),
}


def predecessor_revision_vectors(manifest):
    """Only fully migrated, pinned selected roster/private profiles can roll back."""
    result = {}
    if not manifest.profiles:
        raise AuthorityError("Pinned predecessor deployment is unavailable")
    for profile in manifest.profiles:
        layout = (profile.primary_schema, profile.private_schemas, profile.modules)
        if _PREDECESSOR_PROFILE_LAYOUT.get(profile.role) != layout:
            raise AuthorityError("Predecessor deployment profile differs from pinned source")
        chains = {"core"}
        if profile.primary_schema in _PREDECESSOR_CHAIN_HEADS:
            chains.add(profile.primary_schema)
        for module in profile.modules:
            chain = "home" if module == "home_assistant" else module
            if chain not in _PREDECESSOR_CHAIN_HEADS:
                continue
            if chain == "memory" and profile.private_schemas:
                for private in profile.private_schemas:
                    result[private] = (_PREDECESSOR_CHAIN_HEADS[chain],)
            else:
                chains.add(chain)
        result[profile.primary_schema] = tuple(sorted(_PREDECESSOR_CHAIN_HEADS[c] for c in chains))
    return result


def _validate_predecessor_revision_vectors(versions, manifest):
    expected = predecessor_revision_vectors(manifest)
    actual = {row["schema"]: tuple(row["revisions"]) for row in versions}
    # The old administrative core-only entry point may also have been run in
    # public. It cannot substitute for any required selected/private chain.
    if "public" in actual:
        expected["public"] = (_PREDECESSOR_CHAIN_HEADS["core"],)
    if actual != expected:
        raise AuthorityError("Predecessor complete revision profile is not pinned source")


def validate_rollback_capsule(capsule, manifest, actor: str):
    """Validate fixed structure/source bindings before the first reverse effect."""
    import hashlib
    import json

    from .interfaces import _RETIRED_ADMIN_SCHEMAS

    if (
        not isinstance(capsule, dict)
        or set(capsule)
        != {
            "format",
            "predecessor_commit",
            "migration_role",
            "actor",
            "release_references",
            "catalog",
            "catalog_sha256",
        }
        or capsule["format"] != "butlers-authority-rollback-v1"
    ):
        raise AuthorityError("Preserving rollback capsule is unsupported")
    if (
        capsule["predecessor_commit"] != _PREDECESSOR_COMMIT
        or capsule["migration_role"] != manifest.migration_owner
        or capsule["actor"] != actor
    ):
        raise AuthorityError("Preserving rollback source/actor binding is unavailable")
    reference = capsule["release_references"]
    if not isinstance(reference, dict) or set(reference) != {"chart", "image", "old_secret"}:
        raise AuthorityError("Pinned predecessor deployment references are required")
    if any(
        not isinstance(value, str)
        or not value
        or len(value) > 256
        or any(c.isspace() for c in value)
        or "://" in value
        for value in reference.values()
    ):
        raise AuthorityError("Predecessor references must be nonsecret artifact/key identities")
    catalog = capsule["catalog"]
    if not isinstance(catalog, dict) or set(catalog) != {
        "database",
        "schemas",
        "relations",
        "functions",
        "roles",
        "memberships",
        "defaults",
        "versions",
        "configurations",
    }:
        raise AuthorityError("Preserving rollback catalog is incomplete")
    digest = hashlib.sha256(json.dumps(catalog, sort_keys=True, default=str).encode()).hexdigest()
    if digest != capsule["catalog_sha256"]:
        raise AuthorityError("Preserving rollback catalog integrity differs")
    if (
        not isinstance(catalog["database"], dict)
        or set(catalog["database"]) != {"name", "owner", "acl"}
        or catalog["database"]["name"] != manifest.database
        or catalog["database"]["owner"] != actor
    ):
        # Supported source30b predecessor is the explicitly selected old
        # bootstrap-owned DB. A different old owner needs its actual proof.
        raise AuthorityError("Pinned predecessor database owner is unavailable")
    roles = _rollback_roles(manifest, actor) | {"PUBLIC", "pg_database_owner"}
    _validate_rollback_metadata(catalog, manifest, actor, roles)
    for acl in _metadata_rows(
        catalog["database"]["acl"], {"role", "verb", "grantable"}, ("role", "verb")
    ):
        if (
            acl["role"] not in roles
            or acl["verb"] not in {"CONNECT", "CREATE", "TEMPORARY"}
            or acl["grantable"] is not False
        ):
            raise AuthorityError("Rollback database privilege is not source-bound")
    schemas = set(manifest.schemas) | set(_RETIRED_ADMIN_SCHEMAS)
    declared = {(row.schema, row.name) for row in manifest.relations}
    fixed_functions = predecessor_function_sources(manifest.profiles)
    for row in catalog["schemas"]:
        if row["name"] not in schemas or row["owner"] not in roles:
            raise AuthorityError("Predecessor namespace is not source-bound")
    declared_by_name = {(r.schema, r.name): r for r in manifest.relations}
    for row in catalog["relations"]:
        child = row["partition"] is True and row["kind"] == "r" and len(row["parents"]) == 1
        if child:
            parent = tuple(row["parents"][0])
            definition = declared_by_name.get(parent)
            child = (
                parent in _PARTITION_PARENTS
                and definition is not None
                and row["schema"] == definition.schema
                and row["owner"]
                == (
                    manifest.migration_owner
                    if _partition_owner_transition(definition)
                    else definition.owner
                )
            )
        if (
            (row["schema"], row["name"]) not in declared
            and not child
            and (
                row["schema"] not in _RETIRED_ADMIN_SCHEMAS
                or row["name"] != "bootstrap_configuration"
            )
        ) or row["owner"] not in roles:
            raise AuthorityError("Predecessor relation is not source-bound")
    for row in catalog["functions"]:
        key = row["schema"], row["name"], row["signature"], row["digest"]
        if key not in fixed_functions or row["owner"] not in roles:
            raise AuthorityError("Predecessor function is not pinned source")
    for category in ("schemas", "relations", "functions"):
        for row in catalog[category]:
            verbs = (
                {"USAGE", "CREATE"}
                if category == "schemas"
                else (
                    {"EXECUTE"}
                    if category == "functions"
                    else (
                        {"USAGE", "SELECT", "UPDATE"}
                        if row["kind"] == "S"
                        else {
                            "SELECT",
                            "INSERT",
                            "UPDATE",
                            "DELETE",
                            "TRUNCATE",
                            "REFERENCES",
                            "TRIGGER",
                        }
                    )
                )
            )
            acl_keys = {"role", "grantable", "privilege" if category == "functions" else "verb"}
            for acl in _metadata_rows(
                row["acl"], acl_keys, ("role", *sorted(acl_keys - {"role", "grantable"}))
            ):
                verb = acl.get("verb", acl.get("privilege"))
                if acl["role"] not in roles or verb not in verbs or acl["grantable"] is not False:
                    raise AuthorityError("Predecessor ACL is not source-bound")
            for acl in row.get("columns", ()):
                if (
                    acl["role"] not in roles
                    or acl["verb"] not in {"SELECT", "INSERT", "UPDATE", "REFERENCES"}
                    or acl["grantable"]
                    or not isinstance(acl["name"], str)
                ):
                    raise AuthorityError("Predecessor column ACL is not source-bound")
    for row in catalog["memberships"]:
        if (
            row["role"] not in roles - {actor, "PUBLIC", "pg_database_owner"}
            or row["member"] not in roles - {actor, "PUBLIC", "pg_database_owner"}
            or row["admin"]
        ):
            raise AuthorityError("Predecessor role reach is not source-bound")
    for row in catalog["defaults"]:
        if (
            row["owner"] not in roles
            or row["role"] not in roles
            or row["schema"] not in (None, *schemas)
            or row["kind"] not in _DEFAULT_KINDS
            or row["verb"] not in _ALLOWED_ACL_VERBS
            or row["grantable"] is not False
        ):
            raise AuthorityError("Predecessor default ACL is not source-bound")
    _validate_predecessor_revision_vectors(catalog["versions"], manifest)
    return catalog


def capture_rollback_capsule(connection, migration_role: str, release_references: dict):
    """Capture R0 BEFORE ownership/credential/bootstrap transition, without data."""
    import hashlib
    import json

    actor = _rollback_actor(connection)
    require_rollback_lock(connection)
    manifest = _rollback_source_manifest(connection, migration_role)
    require_serving_quiescence(
        connection,
        tuple(sorted(_rollback_roles(manifest, actor) - {actor})),
    )
    from .interfaces import inspect_retired_interfaces

    inspect_retired_interfaces(connection, migration_role)
    catalog = _snapshot_authority_catalog(connection, manifest)
    capsule = dict(
        format="butlers-authority-rollback-v1",
        predecessor_commit=_PREDECESSOR_COMMIT,
        migration_role=migration_role,
        actor=actor,
        release_references=release_references,
        catalog=catalog,
        catalog_sha256=hashlib.sha256(
            json.dumps(catalog, sort_keys=True, default=str).encode()
        ).hexdigest(),
    )
    validate_rollback_capsule(capsule, manifest, actor)
    return capsule


def pinned_predecessor_initializer() -> str:
    """Load only the pinned rollback artifact, never operator-supplied SQL."""
    import hashlib
    from pathlib import Path

    source = Path(__file__).resolve().parents[3] / "scripts" / "db_authority_cutover.sql"
    if source.is_symlink():
        raise AuthorityError("Pinned predecessor initializer is unavailable")
    data = source.read_bytes()
    if hashlib.sha256(data).hexdigest() != _PREDECESSOR_REPLAY_SHA256:
        raise AuthorityError("Pinned predecessor initializer differs from released source")
    # This one psql client directive has no server-side SQL meaning. All other
    # bytes remain the released initializer, including its refusal branches.
    lines = data.decode().splitlines(keepends=True)
    directives = [line for line in lines if line.startswith("\\")]
    if directives != ["\\set ON_ERROR_STOP on\n"]:
        raise AuthorityError("Unsupported predecessor client directive")
    return "".join(line for line in lines if not line.startswith("\\"))


def _rollback_data_fingerprints(connection, catalog):
    """Compare rows inside the held transaction; return no row values to Python.

    These digests are ephemeral and are never written into the catalog capsule
    or a CLI receipt. Version/configuration rows belong to the explicit reverse
    protocol; every other stored row, including credentials and protected
    generation/receipt/outbox history, must remain unchanged.
    """
    result = {}
    for row in catalog["relations"]:
        import re

        if (
            not isinstance(row["shape"], str)
            or re.fullmatch(r"[0-9a-f]{64}", row["shape"]) is None
            or not isinstance(row["parents"], list)
            or any(
                not isinstance(parent, list)
                or len(parent) != 2
                or any(not isinstance(value, str) for value in parent)
                for parent in row["parents"]
            )
        ):
            raise AuthorityError("Rollback structural metadata is malformed")
        if row["kind"] not in {"r", "p"} or row["name"] == "alembic_version":
            continue
        if row["name"] == "bootstrap_configuration" and row["schema"].endswith("_admin"):
            continue
        target = quote_identifier(row["schema"]) + "." + quote_identifier(row["name"])
        result[row["schema"], row["name"]] = connection.exec_driver_sql(
            "SELECT count(*),pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to("
            "COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text)::text,'[]'),"
            f"'UTF8')),'hex') FROM {target} t"
        ).one()
    return result


def _rollback_catalog_shape(catalog):
    """R0 may repair privileges; it may not adopt a changed object or version."""
    result = {
        key: [{k: v for k, v in row.items() if k not in {"acl", "columns"}} for row in catalog[key]]
        for key in ("schemas", "relations", "functions", "versions", "configurations", "roles")
    }
    result["database"] = {k: v for k, v in catalog["database"].items() if k != "acl"}
    return result


def _replay_predecessor_initializer(connection, migration_role: str, source: str):
    """The misuse marker is local to the already-proven maintenance session."""
    previous = connection.execute(
        text("SELECT current_setting('butlers.connecting_user',true)")
    ).scalar_one()
    connection.execute(
        text("SELECT pg_catalog.set_config('butlers.connecting_user',:role,true)"),
        {"role": migration_role},
    )
    connection.exec_driver_sql(
        "SELECT pg_catalog.set_config('butlers.authority_rollback_verified',"
        "'pinned-30b-preserving-rollback',true)"
    )
    # SQLAlchemy text() would reinterpret colon tokens in the fixed SQL.
    # A native cursor without parameters preserves percent signs and the same
    # physical session. On error the owner rolls back; markers are LOCAL.
    with connection.connection.driver_connection.cursor() as cursor:
        cursor.execute(source)
    connection.execute(
        text("SELECT pg_catalog.set_config('butlers.connecting_user',:role,true)"),
        {"role": previous or ""},
    )
    connection.exec_driver_sql(
        "SELECT pg_catalog.set_config('butlers.authority_rollback_verified','',true)"
    )


def _rollback_grantee(role: str) -> str:
    return "PUBLIC" if role == "PUBLIC" else quote_identifier(role)


def _restore_predecessor_privileges(connection, catalog, manifest):
    """Apply typed, validated captured ACLs, never execute capsule SQL."""
    source_functions = predecessor_function_sources(manifest.profiles)
    for row in catalog["functions"]:
        key = row["schema"], row["name"], row["signature"], row["digest"]
        connection.exec_driver_sql(source_functions[key])
        target = quote_identifier(row["schema"]) + "." + quote_identifier(row["name"])
        target += "(" + row["signature"] + ")"
        connection.exec_driver_sql(
            f"ALTER FUNCTION {target} OWNER TO {quote_identifier(row['owner'])}"
        )
        oid = connection.execute(
            text("SELECT pg_catalog.to_regprocedure(:target)::oid"), {"target": target}
        ).scalar_one()
        from .interfaces import _FUNCTION_ACL

        for acl in _rows(connection, _FUNCTION_ACL, oid=oid):
            connection.exec_driver_sql(
                f"REVOKE ALL ON FUNCTION {target} FROM {_rollback_grantee(acl['role'])}"
            )
        for acl in row["acl"]:
            connection.exec_driver_sql(
                f"GRANT EXECUTE ON FUNCTION {target} TO {_rollback_grantee(acl['role'])}"
            )
    for row in catalog["schemas"]:
        target = quote_identifier(row["name"])
        connection.exec_driver_sql(
            f"ALTER SCHEMA {target} OWNER TO {quote_identifier(row['owner'])}"
        )
        oid = connection.execute(
            text("SELECT oid FROM pg_catalog.pg_namespace WHERE nspname=:name"),
            {"name": row["name"]},
        ).scalar_one()
        for acl in _rows(connection, _NAMESPACE_ACL, oid=oid):
            connection.exec_driver_sql(
                f"REVOKE ALL ON SCHEMA {target} FROM {_rollback_grantee(acl['role'])}"
            )
        for acl in row["acl"]:
            connection.exec_driver_sql(
                f"GRANT {acl['verb']} ON SCHEMA {target} TO {_rollback_grantee(acl['role'])}"
            )
    for row in catalog["relations"]:
        kind = {"S": "SEQUENCE", "v": "VIEW", "m": "MATERIALIZED VIEW", "f": "FOREIGN TABLE"}.get(
            row["kind"], "TABLE"
        )
        target = quote_identifier(row["schema"]) + "." + quote_identifier(row["name"])
        connection.exec_driver_sql(
            f"ALTER {kind} {target} OWNER TO {quote_identifier(row['owner'])}"
        )
        oid = connection.execute(
            text("SELECT pg_catalog.to_regclass(:target)::oid"), {"target": target}
        ).scalar_one()
        acl_kind = "SEQUENCE" if row["kind"] == "S" else "TABLE"
        for acl in _rows(connection, _RELATION_ACL, oid=oid):
            connection.exec_driver_sql(
                f"REVOKE ALL ON {acl_kind} {target} FROM {_rollback_grantee(acl['role'])}"
            )
        for acl in _rows(connection, _COLUMN_ACL, oid=oid):
            connection.exec_driver_sql(
                f"REVOKE ALL ({quote_identifier(acl['name'])}) ON TABLE {target} "
                f"FROM {_rollback_grantee(acl['role'])}"
            )
        for acl in row["acl"]:
            connection.exec_driver_sql(
                f"GRANT {acl['verb']} ON {acl_kind} {target} TO {_rollback_grantee(acl['role'])}"
            )
        for acl in row["columns"]:
            connection.exec_driver_sql(
                f"GRANT {acl['verb']} ({quote_identifier(acl['name'])}) ON TABLE {target} "
                f"TO {_rollback_grantee(acl['role'])}"
            )
    # Revoke all current source-owned defaults first, including the implicit
    # PUBLIC function/type defaults which PostgreSQL represents without a row.
    owners = tuple(sorted({r["name"] for r in catalog["roles"]}))
    schemas = tuple(sorted(r["name"] for r in catalog["schemas"]))
    converge_default_privileges(connection, schemas, owners)
    for row in catalog["defaults"]:
        target_schema = (
            "" if row["schema"] is None else " IN SCHEMA " + quote_identifier(row["schema"])
        )
        connection.exec_driver_sql(
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {quote_identifier(row['owner'])}{target_schema} "
            f"GRANT {row['verb']} ON {_DEFAULT_KINDS[row['kind']]} "
            f"TO {_rollback_grantee(row['role'])}"
        )


def _restore_predecessor_role_state(connection, catalog, manifest, actor):
    """Restore fixed roles; observed foreign reach authorizes revocation only.

    A newly provisioned login can be detached from a managed capability. It is
    never altered, dropped or granted anything by the reverse path. Excluding
    the maintenance actor also preserves its unrelated administrative edges.
    """
    current = _rows(
        connection, _ROLLBACK_MEMBERSHIPS, roles=sorted(_rollback_roles(manifest, actor) - {actor})
    )
    for row in current:
        connection.exec_driver_sql(
            f"REVOKE {quote_identifier(row['role'])} FROM {quote_identifier(row['member'])}"
        )
    for row in catalog["roles"]:
        if row["name"] == actor:
            continue
        options = " ".join(
            ("" if row[field] else "NO") + keyword
            for field, keyword in (
                ("login", "LOGIN"),
                ("inherit", "INHERIT"),
                ("super", "SUPERUSER"),
                ("createdb", "CREATEDB"),
                ("createrole", "CREATEROLE"),
                ("replication", "REPLICATION"),
                ("bypassrls", "BYPASSRLS"),
            )
        )
        connection.exec_driver_sql(f"ALTER ROLE {quote_identifier(row['name'])} {options}")
    version = int(connection.exec_driver_sql("SHOW server_version_num").scalar_one())
    for row in catalog["memberships"]:
        statement = f"GRANT {quote_identifier(row['role'])} TO {quote_identifier(row['member'])}"
        if version >= 160000:
            statement += " WITH INHERIT " + str(row["inherit"]).upper()
            statement += ", SET " + str(row["set"]).upper()
        elif row["inherit"] is not True or row["set"] is not True:
            raise AuthorityError("Captured membership options need their actual server version")
        connection.exec_driver_sql(statement)


def _stage_predecessor_configuration(connection, catalog, actor):
    """Recreate only the three source-retired metadata rows, never user data."""
    from datetime import datetime

    for row in catalog["configurations"]:
        namespace = quote_identifier(row["schema"])
        exists = connection.execute(
            text("SELECT EXISTS(SELECT FROM pg_catalog.pg_namespace WHERE nspname=:name)"),
            {"name": row["schema"]},
        ).scalar_one()
        if exists:
            raise AuthorityError("R1 predecessor configuration unexpectedly exists")
        connection.exec_driver_sql(
            f"CREATE SCHEMA {namespace} AUTHORIZATION {quote_identifier(actor)}"
        )
        columns = (
            "singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),"
            "migration_role name NOT NULL, bootstrap_role name NOT NULL"
        )
        value = row["values"][0].copy()
        if row["schema"] == "runtime_attention_admin":
            columns += (
                ",interface_version integer NOT NULL DEFAULT 1 CHECK(interface_version IN(1,2)),"
                "producers_enabled boolean NOT NULL DEFAULT false,"
                "producer_activated_at timestamptz"
            )
            if isinstance(value["producer_activated_at"], str):
                value["producer_activated_at"] = datetime.fromisoformat(
                    value["producer_activated_at"]
                )
        connection.exec_driver_sql(f"CREATE TABLE {namespace}.bootstrap_configuration ({columns})")
        keys = sorted(value)
        connection.execute(
            text(
                f"INSERT INTO {namespace}.bootstrap_configuration ("
                + ",".join(quote_identifier(key) for key in keys)
                + ") VALUES ("
                + ",".join(":" + key for key in keys)
                + ")"
            ),
            value,
        )


def _catalog_equivalent(actual, expected):
    """Compare row populations canonically, independent of catalog row ordering."""
    import json
    from datetime import date, datetime

    def canonical(value):
        if isinstance(value, dict):
            return {k: canonical(v) for k, v in sorted(value.items())}
        if isinstance(value, (tuple, list)):
            return sorted(
                (canonical(v) for v in value),
                key=lambda v: json.dumps(v, sort_keys=True, default=str),
            )
        if isinstance(value, (datetime, date)):
            # Capture files use JSON's explicit default=str encoding. A real
            # native timestamp readback must equal its recorded representation.
            return str(value)
        return value

    return canonical(actual) == canonical(expected)


def _predecessor_catalog_projection(actual, expected, manifest, actor):
    """Leave newly introduced, inert cluster roles in place without hiding reach.

    Rollback owns this database, not a DROP ROLE across the cluster. A new
    source capability may remain only when it is closed and has no authority
    anywhere in the captured database or global membership graph. Its implicit
    PUBLIC defaults are harmless while nobody can become that owner. Any other
    difference still fails the complete predecessor readback.
    """
    old_roles = {row["name"] for row in expected["roles"]}
    extras = {row["name"] for row in actual["roles"]} - old_roles
    if not extras:
        return actual
    if not extras <= _rollback_roles(manifest, actor) - {actor, manifest.migration_owner}:
        raise AuthorityError("Rollback leaves an undeclared role")
    for row in actual["roles"]:
        if row["name"] in extras and any(value for key, value in row.items() if key != "name"):
            raise AuthorityError("Rollback capability remains usable")
    if any(row["role"] in extras or row["member"] in extras for row in actual["memberships"]):
        raise AuthorityError("Rollback capability retains role reach")
    for category in ("schemas", "relations", "functions"):
        for row in actual[category]:
            if row["owner"] in extras or any(
                acl["role"] in extras for acl in (*row["acl"], *row.get("columns", ()))
            ):
                raise AuthorityError("Rollback capability retains object authority")
    if actual["database"]["owner"] in extras or any(
        acl["role"] in extras for acl in actual["database"]["acl"]
    ):
        raise AuthorityError("Rollback capability retains database authority")
    for row in actual["defaults"]:
        if (
            row["role"] in extras
            or row["owner"] in extras
            and (
                row["schema"] is not None
                or row["role"] != "PUBLIC"
                or (row["kind"], row["verb"]) not in {("f", "EXECUTE"), ("T", "USAGE")}
                or row["grantable"] is not False
            )
        ):
            raise AuthorityError("Rollback capability retains default authority")
    return actual | {
        "roles": [row for row in actual["roles"] if row["name"] not in extras],
        "defaults": [row for row in actual["defaults"] if row["owner"] not in extras],
    }


def _verify_r1_callable_sources(manifest, current, predecessor):
    """No admin replay can call an unverified current ordinary function."""
    import hashlib
    import re

    from .grants import deployment_function_plans
    from .interfaces import _RETIRED_ADMIN_SCHEMAS, final_functions

    ordinary = compile_function_manifest(
        manifest.profiles,
        manifest.migration_owner,
        (*deployment_function_plans(manifest.profiles), ("core", None, None)),
    )
    definitions = {
        row.identity: row
        for row in (*ordinary, *final_functions(manifest.profiles, manifest.migration_owner))
    }
    previous = {
        (row["schema"], row["name"], row["signature"]): row for row in predecessor["functions"]
    }
    required = {key for key in previous if key[0] not in _RETIRED_ADMIN_SCHEMAS} | {
        ("restore_drill_executor", "write_audit_projection", "text")
    }
    if {(r["schema"], r["name"], r["signature"]) for r in current["functions"]} != required:
        raise AuthorityError("R1 callable population differs from the source transition")
    for row in current["functions"]:
        identity = row["schema"], row["name"], row["signature"]
        definition = definitions.get(identity)
        if definition is None:
            # Independent D13 interfaces keep their actual source-pinned
            # predecessor bodies/owner; ordinary convergence excludes them.
            if row["schema"] not in {"dashboard_attention", "dashboard_stepup"}:
                raise AuthorityError("R1 function population contains an unlisted object")
            before = previous.get(identity)
            if before is None or any(
                row[key] != before[key] for key in ("owner", "definer", "settings", "digest")
            ):
                raise AuthorityError("R1 independent protected interface differs")
            continue
        digest = hashlib.sha256(definition.body.encode()).hexdigest()
        settings = getattr(definition, "settings", ("search_path=pg_catalog, pg_temp",))
        # Values come from fixed source, never a catalog-discovered definition.
        if (
            row["owner"] != definition.owner
            or row["definer"] is not definition.security_definer
            or tuple(row["settings"] or ()) != settings
            or row["digest"] != digest
            or re.fullmatch(r"[0-9a-f]{64}", row["digest"]) is None
        ):
            raise AuthorityError("R1 callable body/owner differs from fixed source")


def replay_predecessor_r0(connection, capsule):
    """Positive no-transition rollback: actual pinned init restores old grants.

    A narrowed ACL is a supported repair; a changed owner/body/shape/stamp is
    not. Commit/release and external old chart/image/key restoration belong to
    the operator wrapper, never a serving process or this predicate.
    """
    actor = _rollback_actor(connection)
    require_rollback_lock(connection)
    manifest = _rollback_source_manifest(connection, capsule.get("migration_role"))
    expected = validate_rollback_capsule(capsule, manifest, actor)
    source = pinned_predecessor_initializer()
    require_serving_quiescence(
        connection, tuple(sorted(_rollback_roles(manifest, actor) - {actor}))
    )
    actual = _snapshot_authority_catalog(connection, manifest)
    if not _catalog_equivalent(_rollback_catalog_shape(actual), _rollback_catalog_shape(expected)):
        raise AuthorityError("R0 predecessor object/version combination differs")
    fingerprints = _rollback_data_fingerprints(connection, actual)
    _replay_predecessor_initializer(connection, manifest.migration_owner, source)
    after = _snapshot_authority_catalog(connection, manifest)
    if not _catalog_equivalent(after, expected) or fingerprints != _rollback_data_fingerprints(
        connection, after
    ):
        raise AuthorityError("R0 predecessor restoration did not preserve its catalog/data")
    return dict(
        state="predecessor_restored", transition="R0", references=capsule["release_references"]
    )


_PREDECESSOR_MARKER_TRIGGER = (
    "CREATE TRIGGER runtime_attention_plant_legacy_debounce_marker_trigger "
    "BEFORE INSERT ON public.model_dispatch_attempts FOR EACH ROW "
    "EXECUTE FUNCTION public.runtime_attention_plant_legacy_debounce_marker()"
)


def _preview_predecessor_marker_shape(connection, manifest, expected, before):
    """Prove the ONE source retirement by native full-shape readback in a savepoint.

    Neither an opaque captured hash nor a known trigger name authorizes ignoring
    shape. Recreate the pinned function/trigger temporarily, compare the complete
    native relation hash, then roll back the preview even when it succeeds. No
    old upgrader/producer is called and no application row is touched.
    """
    identity = ("public", "runtime_attention_plant_legacy_debounce_marker", "")
    matches = [
        r for r in expected["functions"] if (r["schema"], r["name"], r["signature"]) == identity
    ]
    if len(matches) != 1:
        raise AuthorityError("Predecessor marker function is not captured source")
    row = matches[0]
    key = (*identity, row["digest"])
    definition = predecessor_function_sources(manifest.profiles).get(key)
    if definition is None:
        raise AuthorityError("Predecessor marker function differs from pinned source")
    absent = connection.exec_driver_sql(
        "SELECT pg_catalog.to_regprocedure("
        "'public.runtime_attention_plant_legacy_debounce_marker()') IS NULL"
    ).scalar_one()
    if absent is not True:
        raise AuthorityError("Retired predecessor marker function unexpectedly remains")
    preview = connection.begin_nested()
    primary = None
    try:
        # Capture hashes use pg_catalog-only deparsing. The savepoint also
        # restores the caller's search_path after this identical native read.
        connection.exec_driver_sql("SET LOCAL search_path=pg_catalog")
        connection.exec_driver_sql(definition)
        connection.exec_driver_sql(_PREDECESSOR_MARKER_TRIGGER)
        oid = connection.exec_driver_sql(
            "SELECT 'public.model_dispatch_attempts'::pg_catalog.regclass::oid"
        ).scalar_one()
        shape = connection.execute(text(_ROLLBACK_RELATION_SHAPE), {"oid": oid}).scalar_one()
        if shape != before["shape"]:
            raise AuthorityError("R1 predecessor marker transition does not explain full shape")
    except BaseException as exc:
        primary = exc
        raise
    finally:
        # SQLAlchemy rolls back the native savepoint. All preview DDL is gone
        # before the operator is permitted to start permanent reverse effects.
        try:
            preview.rollback()
        except BaseException as cleanup:
            if primary is None:
                raise
            primary.add_note("Rollback marker preview savepoint cleanup was incomplete")
            # The owning operator rolls back/closes the held session. Preserve
            # the original refusal or cancellation rather than masking it.
            del cleanup


def _preview_predecessor_purpose_policy_shape(connection, manifest, before):
    """Prove the exact policy transition without weakening full relation hashes."""
    from .interfaces import converge_ordinary_security_policies

    preview = connection.begin_nested()
    primary = None
    try:
        connection.exec_driver_sql("SET LOCAL search_path=pg_catalog")
        converge_ordinary_security_policies(
            connection, manifest, predecessor=True, only={(before["schema"], before["name"])}
        )
        oid = connection.execute(
            text("SELECT pg_catalog.to_regclass(:target)::oid"),
            {"target": quote_identifier(before["schema"]) + "." + quote_identifier(before["name"])},
        ).scalar_one()
        shape = connection.execute(text(_ROLLBACK_RELATION_SHAPE), {"oid": oid}).scalar_one()
        if shape != before["shape"]:
            raise AuthorityError("R1 predecessor purpose policy does not explain full shape")
    except BaseException as exc:
        primary = exc
        raise
    finally:
        try:
            preview.rollback()
        except BaseException:
            if primary is None:
                raise
            primary.add_note("Rollback purpose policy savepoint cleanup was incomplete")


def _verify_r1_relation_shapes(connection, manifest, current, expected):
    from .interfaces import _PROTECTED_GROUPS, _RETIRED_ADMIN_SCHEMAS

    old_relations = {
        (r["schema"], r["name"]): r
        for r in expected["relations"]
        if r["schema"] not in _RETIRED_ADMIN_SCHEMAS
    }
    if {(r["schema"], r["name"]) for r in current["relations"]} != set(old_relations):
        raise AuthorityError("R1 relation population differs from the predecessor")
    protected = {(s, n) for group in _PROTECTED_GROUPS.values() for s, n, _ in group}
    from .interfaces import ordinary_security_policy_changes

    policy_targets = {key[:2] for key in ordinary_security_policy_changes(manifest)}
    restore_marker = False
    for row in current["relations"]:
        identity = row["schema"], row["name"]
        before = old_relations[identity]
        if any(row[k] != before[k] for k in ("kind", "rls", "forced_rls", "partition", "parents")):
            raise AuthorityError("R1 retained relation security/shape differs")
        if identity not in protected and row["shape"] != before["shape"]:
            if identity in policy_targets:
                _preview_predecessor_purpose_policy_shape(connection, manifest, before)
                continue
            if identity != ("public", "model_dispatch_attempts"):
                raise AuthorityError("R1 retained relation security/shape differs")
            _preview_predecessor_marker_shape(connection, manifest, expected, before)
            restore_marker = True
    return restore_marker


def restore_predecessor_r1(connection, capsule):
    """Reverse only this rebuild's catalog effects, retaining every stored row.

    The owning migration wrapper must subsequently execute the genuine Alembic
    core_281→core_280 edge in each captured core schema and independently verify
    the complete original vector. This function never writes a version table.
    """
    from .interfaces import (
        _RETIRED_ADMIN_SCHEMAS,
        final_functions,
        function_matches,
        inspect_function,
        verify_protected_relation_shapes,
    )

    actor = _rollback_actor(connection)
    require_rollback_lock(connection)
    manifest = _rollback_source_manifest(connection, capsule.get("migration_role"))
    expected = validate_rollback_capsule(capsule, manifest, actor)
    source = pinned_predecessor_initializer()
    require_serving_quiescence(
        connection, tuple(sorted(_rollback_roles(manifest, actor) - {actor}))
    )
    current = _snapshot_authority_catalog(connection, manifest)
    old_vectors = {row["schema"]: row["revisions"] for row in expected["versions"]}
    if {row["schema"]: row["revisions"] for row in current["versions"]} != {
        schema: sorted("core_281" if rev == "core_280" else rev for rev in revisions)
        for schema, revisions in old_vectors.items()
    }:
        raise AuthorityError("R1 selected revision vector is not the exact rebuild")
    if current["database"]["owner"] != manifest.migration_owner:
        raise AuthorityError("R1 selected normal owner is unavailable")
    if any(row["name"] in _RETIRED_ADMIN_SCHEMAS for row in current["schemas"]):
        raise AuthorityError("R1 retired administrative namespace unexpectedly remains")
    # No reverse starts from a guessed protected state or foreign function.
    for group in ("dnd", "attention", "restore"):
        verify_protected_relation_shapes(connection, group, profiles=manifest.profiles)
    definitions = final_functions(manifest.profiles, manifest.migration_owner)
    for definition in definitions:
        if not function_matches(connection, definition, inspect_function(connection, definition)):
            raise AuthorityError("R1 final protected interface differs from source")
    _verify_r1_callable_sources(manifest, current, expected)
    from .interfaces import converge_ordinary_security_policies, verify_declared_security_shapes

    verify_declared_security_shapes(connection, manifest, current["relations"])
    # Match every retained relation before any owner or privilege change. Only
    # source-retired administrative metadata is allowed to be absent in R1.
    restore_marker = _verify_r1_relation_shapes(connection, manifest, current, expected)
    fingerprints = _rollback_data_fingerprints(connection, current)
    _restore_predecessor_role_state(connection, expected, manifest, actor)
    _stage_predecessor_configuration(connection, expected, actor)
    connection.exec_driver_sql(
        f"ALTER DATABASE {quote_identifier(manifest.database)} OWNER TO {quote_identifier(actor)}"
    )
    # The initializer's existing owner checks are preserved, so restore the
    # captured namespace/relation ownership before replaying that exact source.
    for row in expected["schemas"]:
        connection.exec_driver_sql(
            f"ALTER SCHEMA {quote_identifier(row['name'])} "
            f"OWNER TO {quote_identifier(row['owner'])}"
        )
    for row in expected["relations"]:
        kind = {"S": "SEQUENCE", "v": "VIEW", "m": "MATERIALIZED VIEW", "f": "FOREIGN TABLE"}.get(
            row["kind"], "TABLE"
        )
        target = quote_identifier(row["schema"]) + "." + quote_identifier(row["name"])
        connection.exec_driver_sql(
            f"ALTER {kind} {target} OWNER TO {quote_identifier(row['owner'])}"
        )
    converge_ordinary_security_policies(connection, manifest, predecessor=True)
    _replay_predecessor_initializer(connection, manifest.migration_owner, source)
    _restore_predecessor_privileges(connection, expected, manifest)
    if restore_marker:
        # The predecessor callable/owner/security/ACL is now restored. Attach
        # only the pinned trigger whose complete shape was proven above.
        connection.exec_driver_sql(_PREDECESSOR_MARKER_TRIGGER)
    connection.exec_driver_sql(
        "DROP FUNCTION restore_drill_executor.write_audit_projection(text) RESTRICT"
    )
    # Replaying old init can reintroduce its normal source memberships; restoring
    # the captured set a second time both closes extras and preserves SET/INHERIT.
    _restore_predecessor_role_state(connection, expected, manifest, actor)
    for row in _rows(connection, _ROLLBACK_DATABASE_ACL):
        connection.exec_driver_sql(
            f"REVOKE ALL ON DATABASE {quote_identifier(manifest.database)} "
            f"FROM {_rollback_grantee(row['role'])}"
        )
    for row in expected["database"]["acl"]:
        connection.exec_driver_sql(
            f"GRANT {row['verb']} ON DATABASE {quote_identifier(manifest.database)} "
            f"TO {_rollback_grantee(row['role'])}"
        )
    after = _predecessor_catalog_projection(
        _snapshot_authority_catalog(connection, manifest), expected, manifest, actor
    )
    if not _catalog_equivalent(
        after | {"versions": expected["versions"]}, expected
    ) or fingerprints != _rollback_data_fingerprints(connection, current):
        raise AuthorityError("R1 predecessor catalog/data restoration did not complete")
    # Bound to this exact live connection and capsule. The actual Alembic edge
    # checks this state; a generic --downgrade invocation is not the operator path.
    connection.info["butlers.preserving_reverse"] = capsule
    return manifest


def verify_predecessor_restored(connection, capsule):
    actor = _rollback_actor(connection)
    require_rollback_lock(connection)
    manifest = _rollback_source_manifest(connection, capsule.get("migration_role"))
    expected = validate_rollback_capsule(capsule, manifest, actor)
    actual = _predecessor_catalog_projection(
        _snapshot_authority_catalog(connection, manifest), expected, manifest, actor
    )
    if not _catalog_equivalent(actual, expected):
        raise AuthorityError("Predecessor catalog/version verification failed")
    return dict(
        state="predecessor_restored", transition="R1", references=capsule["release_references"]
    )


def require_rollback_lock(connection):
    from butlers.migrations import _MIGRATION_LOCK

    held = connection.execute(
        text("""SELECT EXISTS(SELECT FROM pg_catalog.pg_locks
        WHERE locktype='advisory' AND pid=pg_catalog.pg_backend_pid() AND granted
          AND classid=:family AND objid=:operation AND objsubid=2
          AND database=(SELECT oid FROM pg_catalog.pg_database
                        WHERE datname=current_database()))"""),
        {"family": _MIGRATION_LOCK[0], "operation": _MIGRATION_LOCK[1]},
    ).scalar_one()
    if held is not True:
        raise AuthorityError("Operator rollback does not own the actual migration lock")


def require_preserving_reverse(connection):
    """The real downgrade edge accepts only already-restored source authority."""
    actor = _rollback_actor(connection)
    require_rollback_lock(connection)
    capsule = connection.info.get("butlers.preserving_reverse")
    if not isinstance(capsule, dict):
        raise AuthorityError("Explicit preserving operator reverse is required")
    manifest = _rollback_source_manifest(connection, capsule.get("migration_role"))
    expected = validate_rollback_capsule(capsule, manifest, actor)
    actual = _predecessor_catalog_projection(
        _snapshot_authority_catalog(connection, manifest), expected, manifest, actor
    )
    old = {r["schema"]: r["revisions"] for r in expected["versions"]}
    for row in actual["versions"]:
        acceptable = old.get(row["schema"])
        if (
            acceptable is None
            or sorted(
                "core_280" if revision == "core_281" else revision for revision in row["revisions"]
            )
            != acceptable
        ):
            raise AuthorityError("Preserving reverse version vector differs")
    if not _catalog_equivalent(actual | {"versions": expected["versions"]}, expected):
        raise AuthorityError("Preserving reverse catalog is not the pinned predecessor")


def install_final_revision(connection):
    """The final edge removes only source-verified retired maintenance surfaces."""
    from butlers.migrations import maintenance_profile

    from .interfaces import (
        inspect_retired_interfaces,
        install_protected_state,
        retire_predecessor_interfaces,
    )
    from .roles import compile_deployment_profiles

    owner = maintenance_profile(connection)
    database, previous = connection.exec_driver_sql("SELECT current_database(),current_user").one()
    profiles = compile_deployment_profiles(
        database, source_database=connection.info.get("butlers.source_database", database)
    )
    connection.exec_driver_sql("RESET ROLE")
    primary = None
    try:
        retired = inspect_retired_interfaces(connection, owner)
        install_protected_state(connection, profiles, owner)
        retire_predecessor_interfaces(connection, retired)
    except BaseException as exc:
        primary = exc
        raise
    finally:
        try:
            connection.exec_driver_sql(f"SET ROLE {quote_identifier(previous)}")
        except BaseException:
            if primary is None:
                raise
            primary.add_note("Final revision role restoration was incomplete")


_ATTENTION_MARKER_TRIGGER = (
    "CREATE TRIGGER runtime_attention_condition_marker_sync_trigger "
    "AFTER UPDATE OF lifecycle_state ON public.runtime_attention_outbox "
    "FOR EACH ROW WHEN (NEW.source='control_plane_condition' AND "
    "NEW.lifecycle_state IS DISTINCT FROM OLD.lifecycle_state) "
    "EXECUTE FUNCTION public.runtime_attention_sync_condition_marker()"
)


def _verify_attention_transition_source(connection, profiles, owner, *, marker_present):
    """Verify the complete source shape, including a disabled marker in a savepoint.

    Only the exact missing marker pair is a supported disabled shape. A temporary
    reconstruction is rolled back before the transition; it never excuses a
    different policy, relation, function body, ACL, overload or unknown trigger.
    """
    from .interfaces import (
        _protected_group_state,
        function_matches,
        inspect_function,
        install_functions,
        protected_group_functions,
    )

    definitions = protected_group_functions(profiles, owner, ("attention",))
    marker = next(d for d in definitions if d.name == "runtime_attention_sync_condition_marker")
    for definition in definitions:
        if definition is marker and not marker_present:
            continue
        if not function_matches(connection, definition, inspect_function(connection, definition)):
            raise AuthorityError("Attention transition function source differs")
    # With every body/ACL already proved exact this validates the isolated
    # NOLOGIN owner attributes without permitting any function repair.
    install_functions(
        connection, tuple(d for d in definitions if marker_present or d is not marker)
    )
    names = [d.name for d in definitions]
    identities = set(
        connection.execute(
            text("""SELECT p.proname, replace(pg_catalog.oidvectortypes(p.proargtypes), ', ', ',')
            FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname='public' AND p.proname=ANY(:names)"""),
            {"names": names},
        ).all()
    )
    expected = {(d.name, d.signature.replace(", ", ",")) for d in definitions}
    if not marker_present:
        expected.remove((marker.name, marker.signature))
    if identities != expected:
        raise AuthorityError("Attention transition overload population differs")
    preview = connection.begin_nested()
    try:
        if not marker_present:
            install_functions(connection, (marker,))
            connection.exec_driver_sql(_ATTENTION_MARKER_TRIGGER)
        if not _protected_group_state(connection, "attention", profiles=profiles):
            raise AuthorityError("Attention transition protected state is unavailable")
    finally:
        preview.rollback()
    return marker


def transition_runtime_attention(connection, *, component: str, enabled: bool):
    """Operator-only, preserving current producer transitions on the held session.

    This is the executing survivor of the retired upgrade/deactivate entry
    points, not a stored runtime capability or an Alembic restamp. The caller
    selects maintenance explicitly, stops serving actors, and commits only on
    success. Every data/receipt row is retained; enabling the marker backfills
    only its delivery-state projection from the authoritative existing episode.
    """
    from butlers.migrations import maintenance_profile

    from .interfaces import install_functions

    if (
        component not in {"producers", "reissue", "conditions", "marker"}
        or type(enabled) is not bool
    ):
        raise AuthorityError("Attention transition selection is unavailable")
    owner = maintenance_profile(connection)
    actor = _rollback_actor(connection)
    require_rollback_lock(connection)
    manifest = _rollback_source_manifest(connection, owner)
    require_serving_quiescence(
        connection, tuple(sorted(_rollback_roles(manifest, actor) - {actor}))
    )
    transaction = connection.begin_nested()
    try:
        # Held migration ownership serializes operators; physical table locks
        # also fence an already-running administrative writer before snapshots.
        connection.exec_driver_sql(
            "LOCK TABLE public.runtime_attention_condition_control, "
            "public.runtime_attention_condition_episodes, public.runtime_attention_delivery_lease, "
            "public.runtime_attention_operator_control, public.runtime_attention_outbox, "
            "public.runtime_attention_producer_control IN ACCESS EXCLUSIVE MODE"
        )
        marker_state = connection.exec_driver_sql(
            "SELECT to_regprocedure('public.runtime_attention_sync_condition_marker()') "
            "IS NOT NULL, "
            "EXISTS(SELECT FROM pg_catalog.pg_trigger WHERE "
            "tgrelid='public.runtime_attention_outbox'::regclass AND "
            "tgname='runtime_attention_condition_marker_sync_trigger' AND NOT tgisinternal)"
        ).one()
        if marker_state not in {(True, True), (False, False)}:
            raise AuthorityError("Attention marker transition state is incomplete")
        marker = _verify_attention_transition_source(
            connection, manifest.profiles, owner, marker_present=marker_state[0]
        )
        if component == "marker":
            if enabled:
                if not marker_state[0]:
                    install_functions(connection, (marker,))
                    connection.exec_driver_sql(_ATTENTION_MARKER_TRIGGER)
                connection.exec_driver_sql("""
                    UPDATE public.runtime_attention_condition_episodes AS marker
                    SET last_delivery_state=episode.lifecycle_state, updated_at=now()
                    FROM public.runtime_attention_outbox AS episode
                    WHERE episode.id=marker.episode_id
                      AND marker.last_delivery_state IS DISTINCT FROM episode.lifecycle_state""")
            elif marker_state[0]:
                connection.exec_driver_sql(
                    "DROP TRIGGER runtime_attention_condition_marker_sync_trigger "
                    "ON public.runtime_attention_outbox"
                )
                connection.exec_driver_sql(
                    "DROP FUNCTION public.runtime_attention_sync_condition_marker()"
                )
        else:
            table, field = {
                "producers": ("runtime_attention_producer_control", "producers_enabled"),
                "reissue": ("runtime_attention_operator_control", "reissue_enabled"),
                "conditions": ("runtime_attention_condition_control", "producer_enabled"),
            }[component]
            activation = (
                ", producer_activated_at=CASE WHEN :enabled AND NOT producers_enabled "
                "THEN clock_timestamp() ELSE producer_activated_at END"
                if component == "producers"
                else ""
            )
            result = connection.execute(
                text(
                    f"UPDATE public.{table} SET {field}=:enabled{activation} WHERE singleton "
                    f"RETURNING {field}"
                ),
                {"enabled": enabled},
            ).all()
            if result != [(enabled,)]:
                raise AuthorityError("Attention transition control row is unavailable")
        # Reverify after the actual transition; disabled marker is proved by the
        # same transactional reconstruction, never an ignored-trigger exception.
        _verify_attention_transition_source(
            connection,
            manifest.profiles,
            owner,
            marker_present=enabled if component == "marker" else marker_state[0],
        )
        transaction.commit()
    except BaseException as primary:
        try:
            transaction.rollback()
        except BaseException:
            primary.add_note("Attention transition rollback was incomplete")
        raise
    return {"component": component, "enabled": enabled}
