#!/usr/bin/env bash
# Deterministically fingerprint the exact local files copied into butlers-base.
#
# This helper deliberately consumes only its positional file arguments.  It
# does not source dotenv files or inspect ambient variables, so a base-image
# freshness decision cannot accidentally incorporate deployment authority.

butlers_base_image_input_fingerprint() {
  if [ "$#" -eq 0 ]; then
    echo "ERROR: base image fingerprint requires at least one input" >&2
    return 1
  fi

  local input digest
  for input in "$@"; do
    if [ ! -f "$input" ]; then
      echo "ERROR: base image fingerprint input is unavailable: $input" >&2
      return 1
    fi
  done

  _butlers_base_image_digest() {
    if command -v sha256sum >/dev/null 2>&1; then
      sha256sum | awk '{print $1}'
    else
      shasum -a 256 | awk '{print $1}'
    fi
  }

  {
    for input in "$@"; do
      digest=$(_butlers_base_image_digest < "$input")
      printf '%s\0%s\0' "$input" "$digest"
    done
  } | _butlers_base_image_digest
}
