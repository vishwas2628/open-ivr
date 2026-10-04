#!/usr/bin/env bash
# 90 - build the Asterisk config from the builder JSON and deploy it.
#      render -> stage (data/asterisk-build) -> copy (/etc/asterisk) -> reload
. "$(dirname "${BASH_SOURCE[0]}")/../lib/common.sh"
. "$(dirname "${BASH_SOURCE[0]}")/../lib/permissions.sh"

step "Build and deploy the Asterisk configuration"

DEST="${ASTERISK_ETC}"

if [ ! -f "${OPENIVR_ROOT}/data/trunk.json" ] && [ ! -f "${OPENIVR_ROOT}/data/endpoints.json" ]; then
  warn "no builder configuration found in ${OPENIVR_ROOT}/data"
  info "run the wizard first:  make builder   (http://localhost:8090)"
  info "the builder's 'Build & Deploy' button does exactly what this step does"
  write_step "build_deploy" '{"ran": false, "reason": "no builder configuration"}'
  return 0 2>/dev/null || exit 0
fi

# /etc/asterisk must be group-writable for the operator, otherwise the copy
# below fails even though the builder wrote everything correctly.
if [ "$(id -u)" -eq 0 ]; then
  grant_asterisk_dirs
  ensure_operator_in_group "${SUDO_USER:-root}"
else
  can_write_etc || warn "$(deploy_requires_root_message)"
fi

if ! have "${OPENIVR_PYTHON}"; then
  die "no python interpreter found (${OPENIVR_PYTHON})"
fi

log "rendering from the builder JSON ..."
( cd "${OPENIVR_ROOT}" && PYTHONPATH="${OPENIVR_ROOT}" OPENIVR_ASTERISK_ETC="${DEST}" \
    "${OPENIVR_PYTHON}" -m openivr publish --dest "${DEST}" ) \
  && ok "configuration deployed to ${DEST}" \
  || warn "publish reported problems (see above); staged files are in ${OPENIVR_ROOT}/data/asterisk-build"

write_step "build_deploy" "$(printf '{"ran": true, "dest": "%s", "staging": "%s"}' \
  "${DEST}" "${OPENIVR_ROOT}/data/asterisk-build")"