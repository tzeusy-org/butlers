"""General butler tools — freeform collection item management.

Re-exports all public symbols so that ``from butlers.tools.general import X``
continues to work as before.
"""

from butlers.tools.general._helpers import _deep_merge
from butlers.tools.general.collections import (
    collection_create,
    collection_delete,
    collection_export,
    collection_list,
)
from butlers.tools.general.items import (
    item_create,
    item_delete,
    item_get,
    item_search,
    item_update,
)

__all__ = [
    "_deep_merge",
    "collection_create",
    "collection_delete",
    "collection_export",
    "collection_list",
    "item_create",
    "item_delete",
    "item_get",
    "item_search",
    "item_update",
]
