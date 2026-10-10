"""Content-free owning ingress history schema; no runtime dependencies."""

LOCAL_COLUMNS = {
    "location_ingress_server_births": [
        ("server_generation", "uuid", True),
        ("incarnation", "uuid", True),
        ("committed_at", "timestamp with time zone", True),
    ],
    "location_ingress_server_ends": [
        ("server_generation", "uuid", True),
        ("ended_at", "timestamp with time zone", True),
    ],
    "location_ingress_input_births": [
        ("copy_generation", "uuid", True),
        ("server_generation", "uuid", True),
        ("dedupe_digest", "bytea", True),
        ("envelope_digest", "bytea", True),
        ("copy_kind", "smallint", True),
        ("committed_at", "timestamp with time zone", True),
    ],
    "location_ingress_input_parents": [
        ("copy_generation", "uuid", True),
        ("parent_generation", "uuid", True),
        ("committed_at", "timestamp with time zone", True),
    ],
    "location_ingress_input_claims": [
        ("copy_generation", "uuid", True),
        ("handler_generation", "uuid", True),
        ("incarnation", "uuid", True),
        ("committed_at", "timestamp with time zone", True),
    ],
    "location_ingress_accepted_inputs": [
        ("copy_generation", "uuid", True),
        ("request_id", "uuid", True),
        ("stored_digest", "bytea", True),
        ("committed_at", "timestamp with time zone", True),
    ],
    "location_ingress_inbox_sources": [
        ("request_id", "uuid", True),
        ("copy_generation", "uuid", True),
        ("dedupe_digest", "bytea", True),
        ("stored_digest", "bytea", True),
        ("committed_at", "timestamp with time zone", True),
    ],
    "location_ingress_input_ends": [
        ("copy_generation", "uuid", True),
        ("ended_at", "timestamp with time zone", True),
    ],
    "location_ingress_runtime_inputs": [
        ("input_generation", "uuid", True),
        ("copy_generation", "uuid", True),
        ("receiving_session", "uuid", True),
        ("request_id", "uuid", True),
        ("stored_digest", "bytea", True),
        ("envelope_digest", "bytea", True),
        ("prompt_digest", "bytea", True),
        ("committed_at", "timestamp with time zone", True),
    ],
}
LOCAL_COLUMNS["location_ingress_structured_inputs"] = [
    ("input_generation", "uuid", True),
    ("copy_generation", "uuid", True),
    ("request_id", "uuid", True),
    ("stored_digest", "bytea", True),
    ("envelope_digest", "bytea", True),
    ("prompt_digest", "bytea", True),
    ("system_digest", "bytea", True),
    ("tools_digest", "bytea", True),
    ("committed_at", "timestamp with time zone", True),
]
LOCAL_COLUMNS["location_ingress_structured_outputs"] = [
    ("input_generation", "uuid", True),
    ("output_digest", "bytea", True),
    ("committed_at", "timestamp with time zone", True),
]
LOCAL_COLUMNS["location_ingress_structured_sdk_births"] = [
    ("input_generation", "uuid", True),
    ("task_generation", "uuid", True),
    ("handler_generation", "uuid", True),
    ("incarnation", "uuid", True),
    ("committed_at", "timestamp with time zone", True),
]
LOCAL_COLUMNS["location_ingress_structured_sdk_ends"] = [
    ("input_generation", "uuid", True),
    ("task_generation", "uuid", True),
    ("receipt_id", "uuid", True),
    ("committed_at", "timestamp with time zone", True),
]
LOCAL_COLUMNS["location_ingress_structured_local_ends"] = [
    ("input_generation", "uuid", True),
    ("task_generation", "uuid", True),
    ("handler_generation", "uuid", True),
    ("incarnation", "uuid", True),
    ("output_digest", "bytea", True),
    ("receipt_id", "uuid", True),
    ("committed_at", "timestamp with time zone", True),
]
LOCAL_TABLES = tuple(LOCAL_COLUMNS)
LOCAL_CONSTRAINTS = {
    "location_ingress_server_births": {"PRIMARY KEY (server_generation)"},
    "location_ingress_server_ends": {
        "PRIMARY KEY (server_generation)",
        "FOREIGN KEY (server_generation) REFERENCES "
        "location_ingress_server_births(server_generation)",
    },
    "location_ingress_input_births": {
        "PRIMARY KEY (copy_generation)",
        "FOREIGN KEY (server_generation) REFERENCES "
        "location_ingress_server_births(server_generation)",
        "CHECK ((octet_length(dedupe_digest) = 32))",
        "CHECK ((octet_length(envelope_digest) = 32))",
        "CHECK ((copy_kind = ANY (ARRAY[1, 2, 3])))",
    },
    "location_ingress_input_parents": {
        "PRIMARY KEY (copy_generation)",
        "FOREIGN KEY (copy_generation) REFERENCES location_ingress_input_births(copy_generation)",
        "FOREIGN KEY (parent_generation) REFERENCES location_ingress_input_births(copy_generation)",
        "CHECK ((copy_generation <> parent_generation))",
    },
    "location_ingress_input_claims": {
        "PRIMARY KEY (copy_generation)",
        "UNIQUE (handler_generation)",
        "FOREIGN KEY (copy_generation) REFERENCES location_ingress_input_births(copy_generation)",
    },
    "location_ingress_accepted_inputs": {
        "PRIMARY KEY (copy_generation)",
        "FOREIGN KEY (copy_generation) REFERENCES location_ingress_input_births(copy_generation)",
        "CHECK ((octet_length(stored_digest) = 32))",
    },
    "location_ingress_inbox_sources": {
        "PRIMARY KEY (request_id)",
        "UNIQUE (copy_generation)",
        "FOREIGN KEY (copy_generation) REFERENCES location_ingress_input_births(copy_generation)",
        "CHECK ((octet_length(dedupe_digest) = 32))",
        "CHECK ((octet_length(stored_digest) = 32))",
    },
    "location_ingress_input_ends": {
        "PRIMARY KEY (copy_generation)",
        "FOREIGN KEY (copy_generation) REFERENCES location_ingress_input_claims(copy_generation)",
    },
    "location_ingress_runtime_inputs": {
        "PRIMARY KEY (input_generation)",
        "UNIQUE (receiving_session)",
        "FOREIGN KEY (copy_generation) REFERENCES location_ingress_input_claims(copy_generation)",
        "CHECK ((octet_length(stored_digest) = 32))",
        "CHECK ((octet_length(envelope_digest) = 32))",
        "CHECK ((octet_length(prompt_digest) = 32))",
    },
}


LOCAL_CONSTRAINTS["location_ingress_structured_inputs"] = {
    "PRIMARY KEY (input_generation)",
    "FOREIGN KEY (copy_generation) REFERENCES location_ingress_input_claims(copy_generation)",
    *(
        f"CHECK ((octet_length({column}) = 32))"
        for column in (
            "stored_digest",
            "envelope_digest",
            "prompt_digest",
            "system_digest",
            "tools_digest",
        )
    ),
}

LOCAL_CONSTRAINTS["location_ingress_structured_outputs"] = {
    "PRIMARY KEY (input_generation)",
    "FOREIGN KEY (input_generation) REFERENCES "
    "location_ingress_structured_inputs(input_generation)",
    "CHECK ((octet_length(output_digest) = 32))",
}

LOCAL_CONSTRAINTS["location_ingress_structured_sdk_births"] = {
    "PRIMARY KEY (input_generation)",
    "UNIQUE (task_generation)",
    "UNIQUE (input_generation, task_generation)",
    "FOREIGN KEY (input_generation) REFERENCES "
    "location_ingress_structured_inputs(input_generation)",
    "FOREIGN KEY (handler_generation) REFERENCES location_ingress_input_claims(handler_generation)",
}
LOCAL_CONSTRAINTS["location_ingress_structured_sdk_ends"] = {
    "PRIMARY KEY (input_generation)",
    "UNIQUE (receipt_id)",
    "FOREIGN KEY (input_generation, task_generation) REFERENCES "
    "location_ingress_structured_sdk_births(input_generation, task_generation)",
}

LOCAL_CONSTRAINTS["location_ingress_structured_local_ends"] = {
    "PRIMARY KEY (input_generation)",
    "UNIQUE (receipt_id)",
    "FOREIGN KEY (input_generation) REFERENCES "
    "location_ingress_structured_outputs(input_generation)",
    "FOREIGN KEY (input_generation, task_generation) REFERENCES "
    "location_ingress_structured_sdk_births(input_generation, task_generation)",
    "FOREIGN KEY (handler_generation) REFERENCES location_ingress_input_claims(handler_generation)",
    "CHECK ((octet_length(output_digest) = 32))",
}


def local_schema_sql() -> str:
    """Owning schema only; ordinary backup carries these content-free rows."""
    return """
        CREATE TABLE IF NOT EXISTS location_ingress_server_births (
          server_generation UUID PRIMARY KEY,incarnation UUID NOT NULL,
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp());
        CREATE TABLE IF NOT EXISTS location_ingress_server_ends (
          server_generation UUID PRIMARY KEY REFERENCES location_ingress_server_births,
          ended_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp());
        CREATE TABLE IF NOT EXISTS location_ingress_input_births (
          copy_generation UUID PRIMARY KEY,
          server_generation UUID NOT NULL REFERENCES location_ingress_server_births,
          dedupe_digest BYTEA NOT NULL CHECK(octet_length(dedupe_digest)=32),
          envelope_digest BYTEA NOT NULL CHECK(octet_length(envelope_digest)=32),
          copy_kind SMALLINT NOT NULL CHECK(copy_kind IN (1,2,3)),
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp());
        CREATE INDEX IF NOT EXISTS ix_location_ingress_source
          ON location_ingress_input_births(dedupe_digest,copy_generation);
        CREATE TABLE IF NOT EXISTS location_ingress_input_parents (
          copy_generation UUID PRIMARY KEY REFERENCES location_ingress_input_births,
          parent_generation UUID NOT NULL REFERENCES location_ingress_input_births(copy_generation),
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
          CHECK(copy_generation<>parent_generation));
        CREATE TABLE IF NOT EXISTS location_ingress_input_claims (
          copy_generation UUID PRIMARY KEY REFERENCES location_ingress_input_births,
          handler_generation UUID NOT NULL UNIQUE,incarnation UUID NOT NULL,
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp());
        CREATE TABLE IF NOT EXISTS location_ingress_accepted_inputs (
          copy_generation UUID PRIMARY KEY REFERENCES location_ingress_input_births,
          request_id UUID NOT NULL,
          stored_digest BYTEA NOT NULL CHECK(octet_length(stored_digest)=32),
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp());
        CREATE TABLE IF NOT EXISTS location_ingress_inbox_sources (
          request_id UUID PRIMARY KEY,
          copy_generation UUID NOT NULL UNIQUE REFERENCES location_ingress_input_births,
          dedupe_digest BYTEA NOT NULL CHECK(octet_length(dedupe_digest)=32),
          stored_digest BYTEA NOT NULL CHECK(octet_length(stored_digest)=32),
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp());
        CREATE TABLE IF NOT EXISTS location_ingress_input_ends (
          copy_generation UUID PRIMARY KEY REFERENCES location_ingress_input_claims,
          ended_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp());
        CREATE TABLE IF NOT EXISTS location_ingress_runtime_inputs (
          input_generation UUID PRIMARY KEY,
          copy_generation UUID NOT NULL REFERENCES location_ingress_input_claims,
          receiving_session UUID NOT NULL UNIQUE,request_id UUID NOT NULL,
          stored_digest BYTEA NOT NULL CHECK(octet_length(stored_digest)=32),
          envelope_digest BYTEA NOT NULL CHECK(octet_length(envelope_digest)=32),
          prompt_digest BYTEA NOT NULL CHECK(octet_length(prompt_digest)=32),
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp());
        CREATE TABLE IF NOT EXISTS location_ingress_structured_inputs (
          input_generation UUID PRIMARY KEY,
          copy_generation UUID NOT NULL REFERENCES location_ingress_input_claims,
          request_id UUID NOT NULL,
          stored_digest BYTEA NOT NULL CHECK(octet_length(stored_digest)=32),
          envelope_digest BYTEA NOT NULL CHECK(octet_length(envelope_digest)=32),
          prompt_digest BYTEA NOT NULL CHECK(octet_length(prompt_digest)=32),
          system_digest BYTEA NOT NULL CHECK(octet_length(system_digest)=32),
          tools_digest BYTEA NOT NULL CHECK(octet_length(tools_digest)=32),
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp());
        CREATE TABLE IF NOT EXISTS location_ingress_structured_outputs (
          input_generation UUID PRIMARY KEY REFERENCES location_ingress_structured_inputs,
          output_digest BYTEA NOT NULL CHECK(octet_length(output_digest)=32),
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp());
        CREATE TABLE IF NOT EXISTS location_ingress_structured_sdk_births (
          input_generation UUID PRIMARY KEY REFERENCES location_ingress_structured_inputs,
          task_generation UUID NOT NULL UNIQUE,
          handler_generation UUID NOT NULL
            REFERENCES location_ingress_input_claims(handler_generation),
          incarnation UUID NOT NULL,
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
          UNIQUE(input_generation,task_generation));
        CREATE TABLE IF NOT EXISTS location_ingress_structured_sdk_ends (
          input_generation UUID PRIMARY KEY, task_generation UUID NOT NULL,
          receipt_id UUID NOT NULL UNIQUE,
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
          FOREIGN KEY(input_generation,task_generation)
            REFERENCES location_ingress_structured_sdk_births(input_generation,task_generation));
        CREATE TABLE IF NOT EXISTS location_ingress_structured_local_ends (
          input_generation UUID PRIMARY KEY REFERENCES location_ingress_structured_outputs,
          task_generation UUID NOT NULL,
          handler_generation UUID NOT NULL
            REFERENCES location_ingress_input_claims(handler_generation),
          incarnation UUID NOT NULL,
          output_digest BYTEA NOT NULL CHECK(octet_length(output_digest)=32),
          receipt_id UUID NOT NULL UNIQUE,
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
          FOREIGN KEY(input_generation,task_generation)
            REFERENCES location_ingress_structured_sdk_births(input_generation,task_generation));
    """
