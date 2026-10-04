#!/usr/bin/env bash
# openivr - one-time filesystem permissions for the unprivileged deploy path.
#
# install.sh runs this once with sudo. Afterwards the user who runs the builder
# only needs to be a member of the 'asterisk' group: render -> stage -> copy
# (/etc/asterisk) -> reload all work without sudo, and no Python code ever
# shells out to sudo.
# shellcheck shell=bash

[ -n "${OPENIVR_PERMISSIONS_SOURCED:-}" ] && return 0
OPENIVR_PERMISSIONS_SOURCED=1

# grant_asterisk_dirs
# Hands /etc/asterisk and the asterisk spool to the asterisk group with the
# setgid bit, so new files inherit the group and can be rewritten without sudo.
grant_asterisk_dirs() {
  local granted=false

  if getent group "${OPENIVR_GROUP}" >/dev/null 2>&1; then
    if [ -d "${ASTERISK_ETC}" ]; then
      chown -R "${OPENIVR_USER}:${OPENIVR_GROUP}" "${ASTERISK_ETC}" || true
      chmod 2775 "${ASTERISK_ETC}" || true
      ok "chown -R ${OPENIVR_USER}:${OPENIVR_GROUP} ${ASTERISK_ETC}; chmod 2775 ${ASTERISK_ETC}"
      granted=true
    fi
  else
    warn "group ${OPENIVR_GROUP} does not exist (Asterisk not installed yet) - run this again after step 10"
  fi

  for dir in "${ASTERISK_LIB}/sounds" "${ASTERISK_LIB}/sounds/custom" \
             "${ASTERISK_SPOOL}" "${ASTERISK_SPOOL}/recording" "${ASTERISK_SPOOL}/monitor"; do
    [ -d "${dir}" ] || continue
    chown -R "${OPENIVR_USER}:${OPENIVR_GROUP}" "${dir}" 2>/dev/null || true
    chmod 2775 "${dir}" 2>/dev/null || true
    granted=true
  done
  ok "asterisk sounds + spool owned by ${OPENIVR_USER}:${OPENIVR_GROUP} (2775)"

  grant_astctl

  return 0
}

# grant_astctl
# Asterisk's CLI socket (/run/asterisk/asterisk.ctl) is created 0755 when
# asterisk.conf leaves astctlpermissions commented out, so `asterisk -rx
# "core reload"` only works for root. That would drag sudo back into the reload
# path, so patch the three astctl lines to group-writable. asterisk.conf itself
# stays package-owned - only these lines are touched, and a .openivr.bak is
# kept next to it.
grant_astctl() {
  local conf="${ASTERISK_ETC}/asterisk.conf"
  local user="${OPENIVR_USER}" group="${OPENIVR_GROUP}"

  [ -f "${conf}" ] || return 0
  if grep -Eq '^[[:space:]]*astctlpermissions[[:space:]]*=[[:space:]]*0?660' "${conf}"; then
    ok "asterisk.conf already sets astctlpermissions 0660"
    return 0
  fi

  cp -p "${conf}" "${conf}.openivr.bak"
  sed -i \
    -e "s|^;[[:space:]]*astctlpermissions[[:space:]]*=.*|astctlpermissions = 0660|" \
    -e "s|^;[[:space:]]*astctlowner[[:space:]]*=.*|astctlowner = ${user}|" \
    -e "s|^;[[:space:]]*astctlgroup[[:space:]]*=.*|astctlgroup = ${group}|" \
    "${conf}"

  if grep -Eq '^astctlpermissions[[:space:]]*=[[:space:]]*0?660' "${conf}"; then
    ok "asterisk.conf: astctlpermissions 0660, ${user}:${group} (backup: ${conf}.openivr.bak)"
    if systemctl is-active --quiet asterisk; then
      systemctl restart asterisk >/dev/null 2>&1 \
        && ok "asterisk restarted so the CLI socket is group-writable" \
        || warn "restart asterisk to apply the group-writable CLI socket"
    fi
  else
    warn "could not patch ${conf}; add 'astctlpermissions = 0660' manually or reload needs sudo"
  fi
}

# ensure_operator_in_group <user>
# The operator (the human running the builder) must be in the asterisk group.
ensure_operator_in_group() {
  local user="${1:-${SUDO_USER:-${USER:-root}}}"
  getent group "${OPENIVR_GROUP}" >/dev/null 2>&1 || return 0
  if id -nG "${user}" 2>/dev/null | tr ' ' '\n' | grep -qx "${OPENIVR_GROUP}"; then
    ok "${user} is already a member of ${OPENIVR_GROUP}"
    return 0
  fi
  if have usermod; then
    usermod -a -G "${OPENIVR_GROUP}" "${user}" 2>/dev/null || {
      warn "could not add ${user} to ${OPENIVR_GROUP} automatically"
      warn "run: sudo usermod -a -G ${OPENIVR_GROUP} ${user}  (then log out and back in)"
      return 0
    }
    ok "added ${user} to group ${OPENIVR_GROUP}"
    warn "log out and back in (or run 'newgrp ${OPENIVR_GROUP}') so the group is picked up"
  fi
}

# can_write_etc
# True when the current user can drop files into ${ASTERISK_ETC} unprivileged.
can_write_etc() {
  [ -d "${ASTERISK_ETC}" ] || return 1
  [ -w "${ASTERISK_ETC}" ] && return 0
  probe="$(mktemp "${ASTERISK_ETC}/.openivr-write-test.XXXXXX" 2>/dev/null)" || return 1
  rm -f "${probe}"
  return 0
}

# deploy_requires_root_message
deploy_requires_root_message() {
  cat <<EOF
The unprivileged deploy path needs write access to ${ASTERISK_ETC}.

Fix it once with:
    sudo ./system/install.sh --steps 05-requirements.sh   # creates the dirs
    sudo ./system/install.sh --grant-permissions          # one-time chown/chmod

Then make sure you are in the asterisk group:
    sudo usermod -a -G ${OPENIVR_GROUP} $(id -un)   &&  newgrp ${OPENIVR_GROUP}
EOF
}