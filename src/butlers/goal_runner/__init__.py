"""The RFC 0038 goal runner: the only thing that starts goal sessions (D8, D14).

Dark: nothing here is wired to ingress, schedules or the dashboard until the M6
switch. ``GoalRunner`` launches leads on admission, recovery and escalation and
children on a lead's ``goal.delegate``; ``GoalTools`` serves the goal tools
through the broker's goal endpoint. ``GoalRuntime`` (``control_plane``) joins
admission, periodic supervision and owner Stop to the runner behind
``GoalRuntimeSwitch``; ``ControlSessionHost`` (``staging``) stages each launch's
model credential over the session-host control channel.
"""
