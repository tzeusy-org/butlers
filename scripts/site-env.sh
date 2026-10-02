# Source me: exports the site-specific (non-secret) hosts that tracked files no longer carry.
#
# Set TAILNET_NAME (e.g. <tailnet>.ts.net) once, plus optionally BUTLERS_NODE_NAME, and every
# host below is derived from it using the homelab's service-name convention. Any key set
# explicitly (env or file) overrides its derived value.
#
# Looks, in order, at $BUTLERS_SITE_ENV (default /secrets/.dev.env) and <repo>/.env.local, and
# exports ONLY the allowlisted keys below, so sourcing never leaks the rest of a secrets file
# into the environment. Unreadable or missing files are skipped silently; already-set vars win.
_butlers_site_keys="TAILNET_NAME BUTLERS_NODE_NAME BEADS_DOLT_SERVER_HOST BUTLERS_IMAGE_REGISTRY BUTLERS_PUBLIC_HOST
BUTLERS_OTLP_ENDPOINT LIVE_LISTENER_TRANSCRIPTION_URL OLLAMA_URL BUTLERS_ARCHIVE_DEAD_IDENTITIES"

_butlers_site_root="$(cd "$(dirname "${BASH_SOURCE[0]:-${(%):-%x}}")/.." 2>/dev/null && pwd)"

for _butlers_site_file in "${BUTLERS_SITE_ENV:-/secrets/.dev.env}" "${_butlers_site_root}/.env.local"; do
    [ -r "$_butlers_site_file" ] || continue
    for _butlers_site_key in $_butlers_site_keys; do
        eval "_butlers_cur=\${$_butlers_site_key:-}"
        [ -n "$_butlers_cur" ] && continue
        _butlers_val="$(sed -n "s/^\(export \)\{0,1\}${_butlers_site_key}=//p" "$_butlers_site_file" 2>/dev/null | tail -n 1)"
        _butlers_val="${_butlers_val%\"}"; _butlers_val="${_butlers_val#\"}"
        _butlers_val="${_butlers_val%\'}"; _butlers_val="${_butlers_val#\'}"
        [ -n "$_butlers_val" ] && export "$_butlers_site_key=$_butlers_val"
    done
done
if [ -n "${TAILNET_NAME:-}" ]; then
    export BEADS_DOLT_SERVER_HOST="${BEADS_DOLT_SERVER_HOST:-dolt.${TAILNET_NAME}}"
    export BUTLERS_IMAGE_REGISTRY="${BUTLERS_IMAGE_REGISTRY:-docker-registry.${TAILNET_NAME}}"
    export BUTLERS_OTLP_ENDPOINT="${BUTLERS_OTLP_ENDPOINT:-http://otel.${TAILNET_NAME}:4318}"
    export LIVE_LISTENER_TRANSCRIPTION_URL="${LIVE_LISTENER_TRANSCRIPTION_URL:-tcp://wyoming-faster-whisper.${TAILNET_NAME}:10300}"
    export BUTLERS_ARCHIVE_DEAD_IDENTITIES="${BUTLERS_ARCHIVE_DEAD_IDENTITIES:-home_assistant:homeassistant.${TAILNET_NAME}:443}"
    export OLLAMA_URL="${OLLAMA_URL:-https://ollama.${TAILNET_NAME}}"
    [ -z "${BUTLERS_NODE_NAME:-}" ] || export BUTLERS_PUBLIC_HOST="${BUTLERS_PUBLIC_HOST:-${BUTLERS_NODE_NAME}.${TAILNET_NAME}}"
fi
unset _butlers_site_keys _butlers_site_root _butlers_site_file _butlers_site_key _butlers_cur _butlers_val
