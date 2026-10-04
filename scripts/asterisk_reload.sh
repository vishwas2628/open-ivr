#!/usr/bin/env bash
# Reload Asterisk after the builder publishes conf files.
# Safe to run repeatedly. Does nothing useful if Asterisk is not running.
#
# No sudo: reloading needs write access to the Asterisk CLI socket
# (/run/asterisk/asterisk.ctl). The installer makes it group-writable once
# (`sudo ./system/install.sh --grant-permissions`).
set -euo pipefail

if ! command -v asterisk >/dev/null 2>&1; then
  echo "asterisk not installed; skipped reload" >&2
  exit 1
fi

ctl="${ASTCTL_SOCKET:-/run/asterisk/asterisk.ctl}"
if [ -S "${ctl}" ] && [ ! -w "${ctl}" ]; then
  echo "reload: ${ctl} is not writable by $(id -un)" >&2
  echo "reload: run 'sudo ./system/install.sh --grant-permissions' once," >&2
  echo "reload: or make sure you are in the asterisk group: id -nG | tr ' ' '\n' | grep asterisk" >&2
  exit 1
fi

asterisk -rx "core reload"
asterisk -rx "pjsip reload" || true
asterisk -rx "dialplan reload" || true
echo "asterisk reloaded"