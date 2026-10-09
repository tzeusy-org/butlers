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
    "location_ingress_input_ends": [
        ("copy_generation", "uuid", True),
        ("ended_at", "timestamp with time zone", True),
    ],
}
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
    "location_ingress_input_ends": {
        "PRIMARY KEY (copy_generation)",
        "FOREIGN KEY (copy_generation) REFERENCES location_ingress_input_claims(copy_generation)",
    },
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
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp());
        CREATE INDEX IF NOT EXISTS ix_location_ingress_source
          ON location_ingress_input_births(dedupe_digest,copy_generation);
        CREATE TABLE IF NOT EXISTS location_ingress_input_claims (
          copy_generation UUID PRIMARY KEY REFERENCES location_ingress_input_births,
          handler_generation UUID NOT NULL UNIQUE,incarnation UUID NOT NULL,
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp());
        CREATE TABLE IF NOT EXISTS location_ingress_accepted_inputs (
          copy_generation UUID PRIMARY KEY REFERENCES location_ingress_input_births,
          request_id UUID NOT NULL,
          stored_digest BYTEA NOT NULL CHECK(octet_length(stored_digest)=32),
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp());
        CREATE TABLE IF NOT EXISTS location_ingress_input_ends (
          copy_generation UUID PRIMARY KEY REFERENCES location_ingress_input_claims,
          ended_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp());
    """
