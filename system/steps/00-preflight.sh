#!/usr/bin/env bash
# 00 - preflight: OS, tools, directories, permissions
. "$(dirname "${BASH_SOURCE[0]}")/../lib/common.sh"

step "Preflight checks"

OS_ID="$(. /etc/os-release 2>/dev/null && printf '%s' "${ID:-unknown}")"
OS_VER="$(. /etc/os-release 2>/dev/null && printf '%s' "${VERSION_ID:-unknown}")"
ARCH="$(uname -m)"
info "OS ${OS_ID} ${OS_VER} (${ARCH}), kernel $(uname -r)"

case "${OS_ID}" in
  ubuntu|debian) PKG=apt ;;
  rhel|centos|rocky|almalinux|ol|fedora) PKG=dnf ;;
  fedora) PKG=dnf ;;
  *) PKG=apt; warn "unknown distro ${OS_ID}; assuming apt" ;;
esac
info "package manager: ${PKG}"

for tool in python3 curl tar; do
  have "${tool}" || warn "${tool} is missing - install it before continuing"
done
have python3 && ok "python3 $(python3 --version 2>&1 | awk '{print $2}')"

if [ "${ASTERISK_ETC}" = "/etc/asterisk" ]; then
  [ -d "${ASTERISK_ETC}" ] && ok "asterisk config dir ${ASTERISK_ETC}" \
                           || info "asterisk not installed yet (step 10 installs it)"
fi

mkdir -p "${ASTERISK_SPOOL}/recording" "${ASTERISK_LIB}/sounds/custom"
chown -R "${OPENIVR_USER}:${OPENIVR_GROUP}" "${ASTERISK_SPOOL}/recording" 2>/dev/null || true
chown -R "${OPENIVR_USER}:${OPENIVR_GROUP}" "${ASTERISK_LIB}/sounds" 2>/dev/null || true
ok "asterisk spool + custom sounds dir ready"

mkdir -p "${OPENIVR_ROOT}/data"/{sounds,recordings,logs,flows}
ok "openivr data dirs ready under ${OPENIVR_ROOT}/data"

write_step "preflight" "$(printf '{"os": %s, "os_version": %s, "arch": %s, "pkg_manager": %s, "python": %s, "checked_at": %s}' \
  "$(json_escape "${OS_ID}")" "$(json_escape "${OS_VER}")" "$(json_escape "${ARCH}")" \
  "$(json_escape "${PKG}")" "$(json_escape "$(python3 --version 2>&1)")" "$(json_escape "$(date -Is)")")"