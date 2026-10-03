#!/usr/bin/env bash
# Reload Asterisk after the builder publishes conf files.
# Safe to run repeatedly. Does nothing useful if Asterisk is not running.
set -euo pipefail

if command -v asterisk >/dev/null 2>&1; then
  asterisk -rx "core reload"
  asterisk -rx "pjsip reload" || true
  asterisk -rx "dialplan reload" || true
  echo "asterisk reloaded"
else
  echo "asterisk not installed; skipped reload" >&2
  exit 1
fi
