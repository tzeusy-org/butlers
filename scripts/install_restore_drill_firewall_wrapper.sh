#!/bin/bash
# Install the immutable runtime copy of restore-drill-firewall.sh.
#
# This is a deliberate one-time privileged installation step. The supported
# launchers never elevate a checkout path; they invoke only the fixed target
# below, which must remain in a root-owned, non-writable directory.

set -euo pipefail

PATH=/usr/sbin:/usr/bin:/sbin:/bin
readonly PATH

readonly TARGET="/usr/local/libexec/butlers-restore-drill-firewall"
readonly SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly SOURCE="${SOURCE_DIR}/restore-drill-firewall.sh"

usage() {
    printf 'Usage: %s [--print-install-plan]\n' "$0" >&2
}

print_install_plan() {
    printf 'Install reviewed source as root:root mode 0755: %s -> %s\n' "$SOURCE" "$TARGET"
}

case "${1:-}" in
    "")
        ;;
    --print-install-plan)
        (($# == 1)) || { usage; exit 2; }
        print_install_plan
        exit 0
        ;;
    *)
        usage
        exit 2
        ;;
esac

if ((EUID != 0)); then
    printf '%s\n' 'ERROR: installation requires a root-controlled deployment step.' >&2
    exit 1
fi
if [[ ! -f "$SOURCE" ]]; then
    printf 'ERROR: missing firewall installation source: %s\n' "$SOURCE" >&2
    exit 1
fi

install -d -o root -g root -m 0755 "$(dirname -- "$TARGET")"
install -o root -g root -m 0755 "$SOURCE" "$TARGET"

if [[ "$(stat -c '%U:%G:%a' "$TARGET")" != "root:root:755" ]]; then
    printf 'ERROR: immutable wrapper ownership/mode verification failed: %s\n' "$TARGET" >&2
    exit 1
fi

print_install_plan
