"""Install the dark, role-isolated canonical perception representation.

Revision ID: core_322
Revises: core_321
Create Date: 2026-11-09

All definitions and privileged ownership live in the fixed init-db installer.
The ordinary core migration can invoke that installer, never create/adopt a
writer or grant itself service membership. Shared objects survive an empty
logical downgrade; nonempty history refuses downgrade instead of losing truth.
"""

from __future__ import annotations

from alembic import op

revision = "core_322"
down_revision = "core_321"
branch_labels = None
depends_on = None

_TRUSTED_FINALIZED_INTERFACE_SQL = """
SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='perception_owner'
    AND NOT rolcanlogin AND NOT rolinherit AND NOT rolsuper AND NOT rolbypassrls
    AND NOT rolcreaterole AND NOT rolcreatedb AND NOT rolreplication)
AND NOT EXISTS (SELECT 1 FROM pg_roles r WHERE r.rolname IN
    ('perception_owner_reader','perception_availability_reader','perception_dots_writer',
     'perception_context_writer','perception_health_writer','perception_expect_writer',
     'perception_sensing_writer','perception_attention_writer') AND
    (r.rolinherit OR r.rolsuper OR r.rolbypassrls OR r.rolcreatedb
     OR r.rolcreaterole OR r.rolreplication
     -- A superuser has effective membership without a pg_auth_members edge.
     -- Only the existing trusted admin-schema owner may use that bootstrap path;
     -- actual grants below remain forbidden even for that privileged caller.
     OR (pg_has_role(current_user,r.oid,'MEMBER') AND NOT EXISTS (
         SELECT 1 FROM pg_roles bootstrap
         JOIN pg_namespace admin ON admin.nspowner=bootstrap.oid
         WHERE bootstrap.rolname=current_user AND bootstrap.rolsuper
             AND admin.nspname='perception_admin'))
     OR EXISTS(SELECT 1 FROM pg_auth_members m WHERE m.roleid=r.oid OR m.member=r.oid)))
AND NOT EXISTS (SELECT 1 FROM pg_auth_members
    WHERE roleid='perception_owner'::regrole OR member='perception_owner'::regrole)
AND NOT EXISTS (
    SELECT 1 FROM (VALUES ('public','situation_slots'),('public','timeline'),
        ('public','owner_modes'),('public','perception_conditions'),
        ('public','perception_suppressions'),('perception_private','kinds'),
        ('perception_private','sources'),
        ('perception_private','source_refs'),('perception_private','suppression_refs'),
        ('perception_private','receipts')) required(ns,tbl)
    LEFT JOIN pg_namespace n ON n.nspname=required.ns
    LEFT JOIN pg_class c ON c.relnamespace=n.oid AND c.relname=required.tbl
    WHERE c.oid IS NULL OR c.relowner<>'perception_owner'::regrole
        OR NOT c.relrowsecurity OR NOT c.relforcerowsecurity
        OR EXISTS (SELECT 1 FROM aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) a
            WHERE a.grantee<>c.relowner AND a.privilege_type IN
                ('SELECT','INSERT','UPDATE','DELETE','TRUNCATE','TRIGGER','REFERENCES')))
AND NOT EXISTS (
    SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE (n.nspname='perception_private' OR n.nspname='public'
        AND p.proname IN ('perception_write','perception_read',
            'perception_timeline','perception_bind','perception_registry'))
    AND (p.proowner<>'perception_owner'::regrole
        OR p.proconfig IS DISTINCT FROM CASE WHEN n.nspname='perception_private'
            AND p.proname='backup_catalog' THEN
                ARRAY['search_path=pg_catalog, pg_temp','row_security=on']::text[]
            ELSE ARRAY['search_path=pg_catalog, pg_temp']::text[] END
        OR EXISTS (SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
            WHERE a.grantee=0)))
AND EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='public' AND p.proname='perception_write' AND p.prosecdef)
AND (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='perception_private')=6
AND NOT EXISTS (SELECT 1 FROM (VALUES
('perception_private','backup_catalog','',
 '53706967a0c035eb59818bac76b0d31a42e13b5841ecaf2002e1ad52938595df'),
('perception_private','canonical_json','3802',
 'b7f838abba7be235551748ebacde47f00614a2995b682c0c9ae38176e43788ca'),
('perception_private','immutable_event','',
 'ea89eaf74f44d44b24d0f398ba0c44039d8d86ef72a2787b165d05f0dd99c83f'),
('perception_private','instant','3802',
 'cab107ea46cd9b46865e4269b6179659841234cbf4712aa7bd7aadf389032c15'),
('perception_private','valid_predicate','3802 23',
 'c933e3415290dd1623b44708a04415fbef307abd88f98e94b92c5e0c1026fc36'),
('perception_private','valid_value','25 3802 25',
 '8e3438de8a269e65133448fae9ba2bfa75edab3e1f3bf473cf4a47eaaf36c36a'),
('public','perception_bind','3802',
 'af5292263aaebc1826bc0ea73bcec5034327553db0240bf07177abb2ed5d2f64'),
('public','perception_read','25 2950 1184',
 'e20ed96ebb3055f4dc4c05b85ae92e0259985c5b1c4e1438eac877d0b2252dc4'),
('public','perception_registry','',
 '6d561408bf82653b710db6b02601f9d687a16ee1a85387cc6c0c39c56c662a5f'),
('public','perception_timeline','1184 1184 25 2950 2950 23 16',
 '881fee42dcc0c5d9a82d813a1387d6ee3353951133132e4eef873700b556e1d4'),
('public','perception_write','3802',
 '944bf97cc61114854799209dcbb8aefc7edee2a34fea16db237d33f38c9e8242')
) expected(ns,objname,args,body_sha)
    LEFT JOIN pg_namespace n ON n.nspname=expected.ns
    LEFT JOIN pg_proc p ON p.pronamespace=n.oid AND p.proname=expected.objname
        AND p.proargtypes::text=expected.args
    WHERE p.oid IS NULL OR encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')<>expected.body_sha)
AND NOT EXISTS (SELECT 1 FROM (VALUES
('perception_private','kinds',ARRAY['kind','schema_version','owning_domain','writer_role','primitive','field_schema','categories','read_visibility','freshness_policy']::text[],3,1),
('perception_private','receipts',ARRAY['event_id','digest','receipt','created_at']::text[],2,1),
('perception_private','source_refs',ARRAY['id','source','ref_type','read_alias']::text[],5,2),
('perception_private','sources',ARRAY['source','writer','kind','subjects','null_subject','max_age_seconds','expectation_kinds','evidence_sources']::text[],5,1),
('perception_private','suppression_refs',ARRAY['id','source']::text[],3,1),
('public','owner_modes',ARRAY['id','kind','subject_entity','state','typed_value','source','observed_at','fresh_until','evidence_refs','labels','generation','writer','created_at','effective_span','cause']::text[],6,2),
('public','perception_conditions',ARRAY['id','kind','subject_entity','goal_ref','predicate','source_boundary','source','digest','starts_at','expires_at','generation','evidence_refs','labels','state','created_at']::text[],5,1),
('public','perception_suppressions',ARRAY['id','kind','subject_entity','goal_ref','predicate','source_boundary','source','digest','starts_at','expires_at','generation','evidence_refs','labels','state','created_at','authority_ref']::text[],4,1),
('public','situation_slots',ARRAY['id','kind','subject_entity','state','typed_value','source','observed_at','fresh_until','evidence_refs','labels','generation','writer','created_at']::text[],9,2),
('public','timeline',ARRAY['id','source','event_key','digest','kind','subject_entity','class','operation','retracts_event_id','at','confirmed_at','late','effective_span','observed_at','zone','lived_date','measures','typed_value','from_state','to_state','from_value','to_value','evidence_refs','labels','generation','writer','created_at']::text[],15,4)
) expected(ns,objname,columns,constraints,indexes)
    LEFT JOIN pg_namespace n ON n.nspname=expected.ns
    LEFT JOIN pg_class c ON c.relnamespace=n.oid AND c.relname=expected.objname
    WHERE c.oid IS NULL OR (SELECT array_agg(a.attname::text ORDER BY a.attnum)
        FROM pg_attribute a WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped)
            IS DISTINCT FROM expected.columns
      OR (SELECT count(*) FROM pg_constraint WHERE conrelid=c.oid)<>expected.constraints
      OR (SELECT count(*) FROM pg_index WHERE indrelid=c.oid)<>expected.indexes)

AND EXISTS (SELECT 1 FROM pg_namespace n WHERE n.nspname='perception_backup'
    AND n.nspowner=to_regrole('perception_owner') AND NOT EXISTS (
        SELECT 1 FROM aclexplode(coalesce(n.nspacl,acldefault('n',n.nspowner))) a
        WHERE a.is_grantable OR NOT (a.grantee=n.nspowner OR
            a.grantee IN (to_regrole('butlers_backup_export'),to_regrole('butlers_backup_restore'))
            AND a.privilege_type='USAGE')))
AND (SELECT count(*) FROM pg_proc p WHERE p.pronamespace=to_regnamespace('perception_backup'))=3
AND (SELECT count(*) FROM pg_proc p WHERE p.pronamespace=to_regnamespace('perception_backup')
    AND p.proowner=to_regrole('perception_owner') AND p.prosecdef
    AND p.proconfig=ARRAY['search_path=pg_catalog, pg_temp','row_security=on']
    AND ((p.proname='export_v1' AND p.pronargs=0 AND p.proretset
          AND p.prorettype=2249 AND p.provolatile='s')
      OR (p.proname='export_schema_v1' AND p.pronargs=0 AND p.proretset
          AND p.prorettype=25 AND p.provolatile='s')
      OR (p.proname='restore_v1' AND p.proargtypes='3802'::oidvector
          AND NOT p.proretset AND p.prorettype=2278 AND p.provolatile='v'))
    AND NOT EXISTS(SELECT 1 FROM aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a
        WHERE a.is_grantable OR NOT(a.grantee=p.proowner OR a.privilege_type='EXECUTE'
          AND a.grantee=CASE WHEN p.proname='restore_v1' THEN to_regrole('butlers_backup_restore')
                           ELSE to_regrole('butlers_backup_export') END)))=3

AND NOT EXISTS(SELECT 1 FROM (VALUES
('perception_private',
 'kinds',
 ARRAY['text',
 'integer',
 'text',
 'name',
 'text',
 'jsonb',
 'text[]',
 'text',
 'jsonb']::text[],
 ARRAY[true,
 true,
 true,
 true,
 true,
 true,
 true,
 true,
 true]::boolean[]),
('perception_private',
 'receipts',
 ARRAY['uuid',
 'text',
 'jsonb',
 'timestamp with time zone']::text[],
 ARRAY[true,
 true,
 true,
 true]::boolean[]),
('perception_private',
 'source_refs',
 ARRAY['uuid',
 'text',
 'text',
 'text']::text[],
 ARRAY[true,
 true,
 true,
 false]::boolean[]),
('perception_private',
 'sources',
 ARRAY['text',
 'name',
 'text',
 'uuid[]',
 'boolean',
 'integer',
 'text[]',
 'text[]']::text[],
 ARRAY[true,
 true,
 true,
 true,
 true,
 false,
 true,
 true]::boolean[]),
('perception_private',
 'suppression_refs',
 ARRAY['uuid',
 'text']::text[],
 ARRAY[true,
 true]::boolean[]),
('public',
 'owner_modes',
 ARRAY['uuid',
 'text',
 'uuid',
 'text',
 'jsonb',
 'text',
 'timestamp with time zone',
 'timestamp with time zone',
 'jsonb',
 'jsonb',
 'bigint',
 'name',
 'timestamp with time zone',
 'jsonb',
 'uuid']::text[],
 ARRAY[true,
 true,
 false,
 true,
 false,
 true,
 true,
 true,
 true,
 true,
 true,
 true,
 true,
 true,
 true]::boolean[]),
('public',
 'perception_conditions',
 ARRAY['uuid',
 'text',
 'uuid',
 'uuid',
 'jsonb',
 'jsonb',
 'text',
 'text',
 'timestamp with time zone',
 'timestamp with time zone',
 'bigint',
 'jsonb',
 'jsonb',
 'text',
 'timestamp with time zone']::text[],
 ARRAY[true,
 true,
 false,
 false,
 true,
 true,
 true,
 true,
 true,
 true,
 true,
 true,
 true,
 true,
 true]::boolean[]),
('public',
 'perception_suppressions',
 ARRAY['uuid',
 'text',
 'uuid',
 'uuid',
 'jsonb',
 'jsonb',
 'text',
 'text',
 'timestamp with time zone',
 'timestamp with time zone',
 'bigint',
 'jsonb',
 'jsonb',
 'text',
 'timestamp with time zone',
 'uuid']::text[],
 ARRAY[true,
 true,
 false,
 false,
 true,
 true,
 true,
 true,
 true,
 true,
 true,
 true,
 true,
 true,
 true,
 true]::boolean[]),
('public',
 'situation_slots',
 ARRAY['uuid',
 'text',
 'uuid',
 'text',
 'jsonb',
 'text',
 'timestamp with time zone',
 'timestamp with time zone',
 'jsonb',
 'jsonb',
 'bigint',
 'name',
 'timestamp with time zone']::text[],
 ARRAY[true,
 true,
 false,
 true,
 false,
 true,
 true,
 true,
 true,
 true,
 true,
 true,
 true]::boolean[]),
('public',
 'timeline',
 ARRAY['uuid',
 'text',
 'text',
 'text',
 'text',
 'uuid',
 'text',
 'text',
 'uuid',
 'timestamp with time zone',
 'timestamp with time zone',
 'boolean',
 'jsonb',
 'timestamp with time zone',
 'text',
 'date',
 'jsonb',
 'jsonb',
 'text',
 'text',
 'jsonb',
 'jsonb',
 'jsonb',
 'jsonb',
 'bigint',
 'name',
 'timestamp with time zone']::text[],
 ARRAY[true,
 true,
 true,
 true,
 true,
 false,
 true,
 true,
 false,
 true,
 true,
 true,
 true,
 true,
 false,
 false,
 true,
 false,
 false,
 false,
 false,
 false,
 true,
 true,
 true,
 true,
 true]::boolean[])
) expected(ns,objname,types,required)
 JOIN pg_namespace n ON n.nspname=expected.ns
 JOIN pg_class c ON c.relnamespace=n.oid AND c.relname=expected.objname
 WHERE (SELECT array_agg(format_type(a.atttypid,a.atttypmod) ORDER BY a.attnum)
          FROM pg_attribute a WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped)
            IS DISTINCT FROM expected.types
    OR (SELECT array_agg(a.attnotnull ORDER BY a.attnum)
          FROM pg_attribute a WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped)
            IS DISTINCT FROM expected.required
    OR EXISTS(SELECT 1 FROM pg_attribute a WHERE a.attrelid=c.oid AND a.attnum>0
        AND (a.attisdropped OR a.attidentity<>'' OR a.attgenerated<>'' OR a.attacl IS NOT NULL))
    OR (SELECT count(*) FROM pg_policy WHERE polrelid=c.oid)<>1
    OR NOT EXISTS(SELECT 1 FROM pg_policy p WHERE p.polrelid=c.oid
        AND p.polname='perception_owner_only' AND p.polcmd='*' AND p.polpermissive
        AND p.polroles=ARRAY[0::oid]
        AND pg_get_expr(p.polqual,p.polrelid)='(CURRENT_USER = ''perception_owner''::name)'
        AND pg_get_expr(p.polwithcheck,p.polrelid)='(CURRENT_USER = ''perception_owner''::name)'))
AND (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
     WHERE n.nspname='perception_private' AND c.relkind IN ('r',
 'p',
 'v',
 'm',
 'f'))=5
AND NOT EXISTS(SELECT 1 FROM (VALUES
('perception_private','backup_catalog',true,'s','sql',3802,
 '[["perception_owner","EXECUTE",false]]'::jsonb),
('perception_private','canonical_json',false,'i','plpgsql',25,
 '[["perception_owner","EXECUTE",false]]'::jsonb),
('perception_private','immutable_event',false,'v','plpgsql',2279,
 '[["perception_owner","EXECUTE",false]]'::jsonb),
('perception_private','instant',false,'i','plpgsql',1184,
 '[["perception_owner","EXECUTE",false]]'::jsonb),
('perception_private','valid_predicate',false,'s','plpgsql',16,
 '[["perception_owner","EXECUTE",false]]'::jsonb),
('perception_private','valid_value',false,'s','plpgsql',16,
 '[["perception_owner","EXECUTE",false]]'::jsonb),
('public','perception_bind',true,'v','plpgsql',3802,
 '[["perception_attention_writer","EXECUTE",false],["perception_context_writer","EXECUTE",false],["perception_owner","EXECUTE",false]]'::jsonb),
('public','perception_read',true,'s','plpgsql',3802,
 '[["perception_availability_reader","EXECUTE",false],["perception_owner","EXECUTE",false],["perception_owner_reader","EXECUTE",false]]'::jsonb),
('public','perception_registry',true,'s','plpgsql',3802,
 '[["perception_owner","EXECUTE",false],["perception_owner_reader","EXECUTE",false]]'::jsonb),
('public','perception_timeline',true,'s','plpgsql',3802,
 '[["perception_owner","EXECUTE",false],["perception_owner_reader","EXECUTE",false]]'::jsonb),
('public','perception_write',true,'v','plpgsql',3802,
 '[["perception_attention_writer","EXECUTE",false],["perception_context_writer","EXECUTE",false],["perception_dots_writer","EXECUTE",false],["perception_expect_writer","EXECUTE",false],["perception_health_writer","EXECUTE",false],["perception_owner","EXECUTE",false],["perception_sensing_writer","EXECUTE",false]]'::jsonb)
) expected(ns,objname,definer,volatility,language,result,acl)
 JOIN pg_namespace n ON n.nspname=expected.ns
 JOIN pg_proc p ON p.pronamespace=n.oid AND p.proname=expected.objname
 JOIN pg_language l ON l.oid=p.prolang
 WHERE p.prosecdef<>expected.definer OR p.provolatile<>expected.volatility
    OR l.lanname<>expected.language OR p.prorettype<>expected.result OR p.proretset
    OR (SELECT jsonb_agg(jsonb_build_array(
        CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END,
        a.privilege_type,a.is_grantable) ORDER BY
        CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END,a.privilege_type)
        FROM aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a)
          IS DISTINCT FROM expected.acl)

"""


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '30s'")
    op.execute("SELECT perception_admin.install_interface()")
    # Bootstrap's fixed superuser-owned interface is the only install path.
    if not op.get_bind().exec_driver_sql(_TRUSTED_FINALIZED_INTERFACE_SQL).scalar():
        raise RuntimeError("perception finalized catalog refused")


def downgrade() -> None:
    # The fixed helper takes the same finite table lock as its history check;
    # an ordinary migration cannot inspect or truncate private history itself.
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '30s'")
    op.execute("SELECT perception_admin.rollback_interface()")
