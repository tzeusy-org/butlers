# Source me: exports the site-specific (non-secret) hosts that tracked files no longer carry.
#
# Looks, in order, at $BUTLERS_SITE_ENV (default /secrets/.dev.env) and <repo>/.env.local, and
# exports ONLY the allowlisted keys below, so sourcing never leaks the rest of a secrets file
# into the environment. Unreadable or missing files are skipped silently; already-set vars win.
_butlers_site_keys="BEADS_DOLT_SERVER_HOST BUTLERS_IMAGE_REGISTRY BUTLERS_PUBLIC_HOST
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
unset _butlers_site_keys _butlers_site_root _butlers_site_file _butlers_site_key _butlers_cur _butlers_val
