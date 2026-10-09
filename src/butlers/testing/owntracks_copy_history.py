"""Plant nonempty migrated OwnTracks metadata for backup row-parity contracts.

These are synthetic stored history rows, not accepted ingress, native source
admission or terminal runtime evidence. The fixture uses the actual schema and
existing connector role; it never creates tables or weakens RLS/immutability.
"""

from uuid import uuid4

COPY_HISTORY_TABLES = tuple(
    "connectors.owntracks_filtered_copy_" + suffix
    for suffix in ("births", "floors", "batches", "members")
)


def plant_copy_history(connection) -> None:
    """Insert one full FK-linked stored cohort on the caller's transaction."""
    generation, raw_id, filtered_id, decision, receipt = (uuid4() for _ in range(5))
    connection.exec_driver_sql("SET ROLE connector_writer")
    try:
        connection.exec_driver_sql(
            "INSERT INTO connectors.owntracks_filtered_copy_births "
            "(copy_generation,filtered_id,filtered_received_at,logical_source_digest,"
            "raw_digest,row_digest,producer_contract) "
            "VALUES(%s,%s,clock_timestamp(),decode(repeat('ab',32),'hex'),"
            "decode(repeat('cd',32),'hex'),decode(repeat('ef',32),'hex'),1)",
            (generation, filtered_id),
        )
        connection.exec_driver_sql(
            "INSERT INTO connectors.owntracks_filtered_copy_floors "
            "(logical_source_digest,decision_id,raw_id,source_revision,raw_digest,manifest_digest) "
            "VALUES(decode(repeat('ab',32),'hex'),%s,%s,1,decode(repeat('cd',32),'hex'),"
            "decode(repeat('12',32),'hex'))",
            (decision, raw_id),
        )
        connection.exec_driver_sql(
            "INSERT INTO connectors.owntracks_filtered_copy_batches "
            "(decision_id,manifest_digest,receipt_id,policy_version,cutoff,expected_count) "
            "VALUES(%s,decode(repeat('12',32),'hex'),%s,1,clock_timestamp(),1)",
            (decision, receipt),
        )
        connection.exec_driver_sql(
            "INSERT INTO connectors.owntracks_filtered_copy_members "
            "(receipt_id,copy_generation,original_digest,reduced_digest) "
            "VALUES(%s,%s,decode(repeat('ef',32),'hex'),decode(repeat('34',32),'hex'))",
            (receipt, generation),
        )
    finally:
        connection.exec_driver_sql("RESET ROLE")
