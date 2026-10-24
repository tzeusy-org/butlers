"""API facade for the shared insights delivery-state read-model v1.

The dashboard and read-only MCP module use one query/DTO implementation in
``butlers.insight_delivery_state``. The version marker and public API imports
remain stable.
"""

from butlers.insight_delivery_state import (
    INSIGHT_DELIVERY_COLUMNS as INSIGHT_DELIVERY_COLUMNS,
)
from butlers.insight_delivery_state import (
    READ_MODEL_VERSION as READ_MODEL_VERSION,
)
from butlers.insight_delivery_state import (
    InsightDeliveryStateRow as InsightDeliveryStateRow,
)
from butlers.insight_delivery_state import (
    query_insight_delivery_state as query_insight_delivery_state,
)
from butlers.insight_delivery_state import (
    row_to_delivery_state as row_to_delivery_state,
)
