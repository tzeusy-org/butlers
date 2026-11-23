"""Inert trust representation for the private runtime-probe control plane.

This package carries the trust representation --- strict deployment-key
parsing, immutable startup snapshots, durable replay receipts, and the narrow
verification-persistence surface --- plus the control plane built on it: the
capability, the coordinator, Switchboard's private route, and the dedicated
Dashboard/Scheduler client.

Since bu-0uqgo.11 it also carries the production mount and the activation
gate: the signer is mounted to the Dashboard alone, the verifier keyring to
every process that must check a capability, and Model Test, verify-all, and
the hourly sweep reach a runtime only through this plane.  Every signing path
goes through :func:`activated_signer_snapshot`, so an image that still holds a
dashboard-local probe signs nothing even with the mount present.

See ``docs/operations/runtime-probe-control-keys.md`` for the operator contract.

The package root re-exports nothing: import the submodule you need (``capability``,
``keys``, ``client``, ...). Importing the assertion codec must not load the probe client
or the coordinator, whose function-local imports reach the spawner and the runtimes; the
public MCP server verifies assertions through ``capability`` and ``keys`` alone
(``tests/contracts/test_public_mcp_import_ban.py``).
"""
