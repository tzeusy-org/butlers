"""The public MCP entrypoint ``butlers-public-mcp`` (RFC 0039 D1, D10, D14).

A thin transport over :mod:`butlers.capability_host`: it registers exactly the pinned D10
catalogue, declares exactly the ``tools`` capability, and routes every request through
:meth:`CapabilityHost.handle`. It may import only the host, ``butlers.knowledge_read``,
the MCP SDK and the ASGI server (``tests/contracts/test_public_mcp_import_ban.py``).
"""
