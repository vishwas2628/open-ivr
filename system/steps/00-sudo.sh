#!/usr/bin/env bash
# 00 - capture the sudo credential once, so every later step can use `sudo -n`
. "$(dirname "${BASH_SOURCE[0]}")/../lib/common.sh"

step "Capturing sudo credential"

SUDO_DURATION="${SUDO_DURATION:-15}"

if [ "$(id -u)" -eq 0 ]; then
  ok "already running as root"
else
  # Ask once. `sudo -v` refreshes the timestamp so the rest of the install can
  # run non-interactive with `sudo -n`; no password is ever written to disk.
  if [ -n "${NONINTERACTIVE:-}" ]; then
    sudo -n -v 2>/dev/null || die "sudo access is required for installation (no TTY to prompt)"
  else
    sudo -v || die "sudo access is required for installation"
  fi
  ok "sudo credential cached (valid for ${SUDO_DURATION} minutes)"
  ok "no password is stored on disk"
fi

info "installing as: ${OPENIVR_OWNER:-${SUDO_USER:-${USER:-root}}}"

write_step "sudo" "$(printf '{"sudo_captured": true, "as_user": %s}' \
  "$(json_escape "${SUDO_USER:-${USER:-root}}")")"