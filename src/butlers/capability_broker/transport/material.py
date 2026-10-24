"""Dedicated TLS material without importing daemon or broker assembly."""


class ProductionTransportUnavailable(RuntimeError):
    """Closed refusal before private material, factories or listeners."""


def material_context(material, *, server=False):
    """Read only explicit dedicated TLS files after production prerequisites."""
    import ssl
    from pathlib import Path

    for name in ("ca_path", "certificate_path", "private_key_path"):
        if not Path(getattr(material, name)).is_absolute():
            raise ProductionTransportUnavailable("absolute_private_material_required")
    context = ssl.create_default_context(
        ssl.Purpose.CLIENT_AUTH if server else ssl.Purpose.SERVER_AUTH, cafile=material.ca_path
    )
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.verify_mode = ssl.CERT_REQUIRED
    context.set_alpn_protocols(["http/1.1"])
    context.load_cert_chain(material.certificate_path, material.private_key_path)
    return context
