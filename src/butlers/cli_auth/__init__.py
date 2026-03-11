"""CLI auth bridge — device-code OAuth for CLI tools.

Spawns CLI login commands as subprocesses, parses device codes from stdout,
and tracks session state so the dashboard can present a one-click auth UX.
"""
