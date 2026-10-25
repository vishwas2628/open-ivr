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
  # The group may have been granted after this shell started (or the installer
  # ran under sudo). Re-exec through sg so /etc/group membership applies
  # without requiring a logout/login first.
  group="${ASTERISK_GROUP:-$(stat -c %G "${ctl}" 2>/dev/null || true)}"
  if [ -n "${group}" ] && command -v sg >/dev/null 2>&1 \
     && sg "${group}" -c "test -w '${ctl}'" 2>/dev/null; then
    exec sg "${group}" -c "$(printf '%q ' "$0" "$@")"
  fi
  echo "reload: ${ctl} is not writable by $(id -un)" >&2
  echo "reload: run 'sudo ./system/install.sh --grant-permissions' once," >&2
  echo "reload: or make sure you are in the asterisk group: id -nG | tr ' ' '\n' | grep asterisk" >&2
  echo "reload: then run 'newgrp asterisk' or log out and back in" >&2
  exit 1
fi

asterisk -rx "core reload"
asterisk -rx "pjsip reload" || true
asterisk -rx "dialplan reload" || true
echo "asterisk reloaded"