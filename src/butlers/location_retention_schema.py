"""Trusted migration ordering for the own-domain native tool input dependency.

Standalone Chronicle schemas can install ordinary storage before runtime-core
exists. Only the actual trusted migrations install the FK; runtime producers
never provision it and refuse an absent or differing installed dependency.
"""

from __future__ import annotations


def tool_input_dependency_sql(schema: str) -> str:
    quoted = '"' + schema.replace('"', '""') + '"'
    child = quoted + ".location_native_memory_mutation_inputs"
    parent = quoted + ".location_runtime_tool_intents"
    child_literal, parent_literal = child.replace("'", "''"), parent.replace("'", "''")
    return f"""
        DO $location_tool_dependency$
        DECLARE
          child_oid OID := pg_catalog.to_regclass('{child_literal}');
          parent_oid OID := pg_catalog.to_regclass('{parent_literal}');
        BEGIN
          -- The standalone ordinary Chronicle storage phase has no runtime
          -- parent. Its native producer cannot admit an input in this phase.
          IF child_oid IS NULL OR parent_oid IS NULL THEN RETURN; END IF;
          IF EXISTS(SELECT 1 FROM pg_catalog.pg_constraint c
              WHERE c.conrelid=child_oid
                AND c.conname='location_native_memory_mutation_inputs_tool_generation_fkey') THEN
            IF NOT EXISTS(SELECT 1 FROM pg_catalog.pg_constraint c
                WHERE c.conrelid=child_oid AND c.confrelid=parent_oid AND c.contype='f'
                  AND c.conname='location_native_memory_mutation_inputs_tool_generation_fkey'
                  AND c.conkey=ARRAY[2]::SMALLINT[] AND c.confkey=ARRAY[1]::SMALLINT[]
                  AND c.convalidated AND NOT c.condeferrable AND NOT c.condeferred
                  AND c.confupdtype='a' AND c.confdeltype='a' AND c.confmatchtype='s') THEN
              RAISE EXCEPTION 'Native mutation tool dependency differs';
            END IF;
          ELSE
            IF EXISTS(SELECT 1 FROM pg_catalog.pg_constraint c
                WHERE c.conrelid=child_oid AND c.contype='f' AND 2=ANY(c.conkey)) THEN
              RAISE EXCEPTION 'Native mutation tool dependency differs';
            END IF;
            ALTER TABLE {child}
              ADD CONSTRAINT location_native_memory_mutation_inputs_tool_generation_fkey
              FOREIGN KEY(tool_generation) REFERENCES {parent}(tool_generation);
          END IF;
        END $location_tool_dependency$;
    """
