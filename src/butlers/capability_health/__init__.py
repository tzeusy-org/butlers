"""Capability health (RFC 0038 S6): one reducer, one publisher, one fail-safe reader.

Health can only refuse or annotate. It never authorizes a call, and an unreadable
projection is ``unknown``: normal authorization and the underlying call checks apply.
"""
