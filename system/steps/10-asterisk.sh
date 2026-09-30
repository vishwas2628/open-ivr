#!/usr/bin/env bash
# 10 - install Asterisk (distro package, or build from source)
. "$(dirname "${BASH_SOURCE[0]}")/../lib/common.sh"

step "Asterisk installation"

ASTERISK_VERSION="${ASTERISK_VERSION:-22.4.0}"
SOURCE_BUILD="${SOURCE_BUILD:-auto}"
SRC_DIR="${SRC_DIR:-/usr/src/asterisk}"

install_from_packages() {
  case "${PKG}" in
    apt)
      export DEBIAN_FRONTEND=noninteractive
      apt-get update -qq
      apt-get install -y asterisk asterisk-dahdi sox ffmpeg
      ;;
    dnf)
      dnf install -y epel-release || true
      dnf install -y asterisk sox || warn "asterisk package not in repos - use SOURCE_BUILD=1"
      ;;
  esac
}

install_from_source() {
  require_root
  local tarball="https://downloads.asterisk.org/asterisk/asterisk-${ASTERISK_VERSION}.tar.gz"
  local url="https://downloads.asterisk.org/asterisk/${ASTERISK_VERSION}.tar.gz"
  log "building Asterisk ${ASTERISK_VERSION} from source (this takes a while)"

  mkdir -p "${SRC_DIR}"
  if [ ! -d "${SRC_DIR}/asterisk-${ASTERISK_VERSION}" ]; then
    curl -fsSL -o "${SRC_DIR}/asterisk.tar.gz" "${url}" \
      || curl -fsSL -o "${SRC_DIR}/asterisk.tar.gz" "${tarball}" \
      || die "cannot download Asterisk ${ASTERISK_VERSION}"
    tar -xzf "${SRC_DIR}/asterisk.tar.gz" -C "${SRC_DIR}"
  fi

  cd "${SRC_DIR}/asterisk-${ASTERISK_VERSION}"
  if [ -x ./contrib/scripts/install_prereq ]; then
    ./contrib/scripts/install_prereq install || warn "prereq script failed - continuing"
  fi
  ./configure
  make menuselect || warn "menuselect skipped"
  make -j"$(nproc)"
  make install
  make basic-pbx >/dev/null 2>&1 || true
  ok "Asterisk ${ASTERISK_VERSION} built and installed"
}

if asteriskctl version >/dev/null 2>&1; then
  ok "Asterisk already installed: $(asteriskctl version | head -1)"
elif [ "${SOURCE_BUILD}" = "1" ]; then
  install_from_source
else
  log "installing Asterisk packages"
  install_from_packages
fi

asteriskctl version >/dev/null 2>&1 || die "Asterisk is not available after installation"

VERSION_LINE="$(asteriskctl version | head -1 | tr -d '\r')"
ok "${VERSION_LINE}"

if ! asterisk_running; then
  if have systemctl; then
    systemctl enable --now asterisk >/dev/null 2>&1 || warn "could not start asterisk via systemd"
  fi
  asterisk_running && ok "asterisk is running" || warn "asterisk is not running yet (start it manually)"
fi

# Modules the IVR needs: res_pjsip (SIP), res_http_websocket (ARI), app_stasis.
missing=""
for mod in pjsip.so http_websocket.so ari.so stasis.so; do
  asterisk -rx "module show ${mod%.so}" >/dev/null 2>&1 || missing="${missing} ${mod}"
done
if [ -n "${missing}" ]; then
  warn "modules not loaded yet:${missing}"
else
  ok "required modules available (pjsip, http_websocket, ari, stasis)"
fi

write_step "asterisk" "$(printf '{"installed": true, "version": %s, "modules_ok": %s, "source_build": %s}' \
  "$(json_escape "${VERSION_LINE}")" "$([ -z "${missing}" ] && echo true || echo false)" \
  "$(json_escape "${SOURCE_BUILD}")")"