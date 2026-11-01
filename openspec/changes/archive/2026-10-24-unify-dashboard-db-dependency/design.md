# Design

Use the existing manager getter as the public dependency callable, preserving get_db_manager identity and callers. Db and OptionalDb annotate required and existing optional reads respectively; the optional adapter catches only the existing uninitialized RuntimeError boundary. QA credential status wiring remains a distinct startup action. An explicit ordered core export list followed by the unchanged sorted roster loader supplies registration; health routes and SPA remain explicit.

This amendment replaces the old factory `wire_db_dependencies()`/`_get_db_manager` implementation clause and the matching roster-wiring scenario only. Every other scenario, singleton/pool lifetime, wire shape and authority remains governed by its existing text. Full executing route/OpenAPI/middleware equality and actual required/optional HTTP controls provide preservation evidence.
