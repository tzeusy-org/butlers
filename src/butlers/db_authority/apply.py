"""Locked migrator convergence of declared objects, never discovery-derived grants."""

from __future__ import annotations

from sqlalchemy import text

from .grants import ObjectAuthority, ObjectKind, public_objects
from .roles import AuthorityError, runtime_profiles

# Discovery is a refusal input. Only a source-declared ObjectAuthority authorizes
# the GRANT statements below; catalog names cannot extend that set.
_RELATIONS = """
SELECT n.nspname AS schema, c.relname AS name, c.relkind AS kind,
       owner.rolname AS owner, c.relrowsecurity AS rls, c.relforcerowsecurity AS forced_rls,
       c.oid AS oid
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
    if value == "S":
        return ObjectKind.SEQUENCE
    raise AuthorityError("Managed database object is not declared")


def validate_relations(observed, declared: tuple[ObjectAuthority, ...]):
    """Perform every name/kind/owner/RLS precondition before the first ACL write."""
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
        if actual["owner"] != row.owner:
            # Ownership transfer is an explicit operator prerequisite, never an
            # automatic adoption of a compatible-looking or hostile object.
            raise AuthorityError("Managed database object ownership is unavailable")
        if row.forced_rls and (actual["rls"] is not True or actual["forced_rls"] is not True):
            raise AuthorityError("Required database row security is unavailable")
        bound.append((actual, row))
    if any(row.required and row.identity not in seen for row in declared):
        raise AuthorityError("Required database object is unavailable")
    return tuple(bound)


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


def converge_authority(connection) -> None:
    """Apply exact ACL rows after the complete catalog has passed classification."""
    database, owner = connection.exec_driver_sql("SELECT current_database(), current_user").one()
    profiles = runtime_profiles(database)
    schemas = (
        "public",
        "connectors",
        *(profile.primary_schema for profile in profiles),
        *(schema for profile in profiles for schema in profile.private_schemas),
    )
    declared = public_objects(database, owner)
    observed = _rows(connection, _RELATIONS, schemas=list(schemas))
    bound = validate_relations(observed, declared)
    for actual, row in bound:
        _revoke_relation(connection, actual, row)
        _grant_relation(connection, row)
    for actual, row in bound:
        verify_relation(connection, actual, row)
    connection.commit()
