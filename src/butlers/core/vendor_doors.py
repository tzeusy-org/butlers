"""Vendor doors: the dark switch for the owner-confirmed vendor-door registry (bu-jv1moa.40).

``public.vendor_doors`` (core_336) and its authority functions exist, but no sender, receiver or
renewal consumer resolves a door while this is ``False`` (openspec ``specify-vendor-doors``,
REQ-vendor-doors-007). There is no activation under RFC 0038 cutover rules; flipping it is an
owner act, not part of the schema and authority layer.
"""

from __future__ import annotations

VENDOR_DOORS_LIVE = False
"""Door references resolve and the confirmation routes answer only when this is ``True``."""
