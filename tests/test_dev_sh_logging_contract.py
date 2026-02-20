from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def test_dev_sh_uses_wrapped_command_logging():
    script = Path("dev.sh").read_text(encoding="utf-8")

    # Guardrail: command logs should come from wrapped process output, not pane mirroring.
    assert "_wrap_cmd_for_log() {" in script
    assert "tmux pipe-pane -o -t" not in script
    assert "--zsh" not in script

    expected_wrapped_invocations = (
        '_wrap_cmd_for_log "GOOGLE_OAUTH_REDIRECT_URI=',
        '_wrap_cmd_for_log "npm install &&',
        '_wrap_cmd_for_log "${ENV_LOADER} && if [ -f \\"$TELEGRAM_BOT_CONNECTOR_ENV_FILE\\" ]',
        '_wrap_cmd_for_log "${ENV_LOADER} && if [ -f \\"$TELEGRAM_USER_CONNECTOR_ENV_FILE\\" ]',
        '_wrap_cmd_for_log "${ENV_LOADER} && uv sync --dev',
        '_wrap_cmd_for_log "${GMAIL_PANE_CMD}"',
    )
    for invocation in expected_wrapped_invocations:
        assert invocation in script
