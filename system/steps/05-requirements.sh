#!/usr/bin/env bash
# 05 - system requirements preflight: OS, tools, python packages, disk, and the
#      two answers the later steps need (firewall active? ffmpeg wanted?).
. "$(dirname "${BASH_SOURCE[0]}")/../lib/common.sh"

step "System requirements"

# ------------------------------------------------------------------- the OS
OS_ID="$(. /etc/os-release 2>/dev/null && printf '%s' "${ID:-unknown}")"
OS_VER="$(. /etc/os-release 2>/dev/null && printf '%s' "${VERSION_ID:-unknown}")"
ARCH="$(uname -m)"
info "OS ${OS_ID} ${OS_VER} (${ARCH}), kernel $(uname -r)"

case "${OS_ID}" in
  ubuntu|debian) PKG=apt ;;
  rhel|centos|rocky|almalinux|ol|fedora) PKG=dnf ;;
  *) PKG=apt; warn "unknown distro ${OS_ID}; assuming apt" ;;
esac
info "package manager: ${PKG}"

# ---------------------------------------------------------------- the tools
MISSING=""
for tool in python3 curl tar; do
  if have "${tool}"; then
    ok "${tool} present"
  else
    warn "${tool} is missing - install it before continuing"
    MISSING="${MISSING} ${tool}"
  fi
done
have git || warn "git is missing (only needed to update openivr itself)"

# --------------------------------------------------------------- python 3.10+
if have python3; then
  PY_VER="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo 0.0)"
  if "${OPENIVR_PYTHON}" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    ok "python ${PY_VER} (>= 3.10 required) at ${OPENIVR_PYTHON}"
  else
    warn "python ${PY_VER} is older than 3.10 - openivr needs >= 3.10"
    MISSING="${MISSING} python>=3.10"
  fi
fi

# --------------------------------------------------- python packages (.venv)
PKG_MISSING=""
if have "${OPENIVR_PYTHON}"; then
  for mod in yaml fastapi uvicorn jinja2 multipart; do
    if "${OPENIVR_PYTHON}" -c "import ${mod}" >/dev/null 2>&1; then
      ok "python package ${mod}"
    else
      PKG_MISSING="${PKG_MISSING} ${mod}"
    fi
  done
  if [ -n "${PKG_MISSING}" ]; then
    warn "missing python packages:${PKG_MISSING}"
    info "create the venv and install them:"
    info "  python3 -m venv ${OPENIVR_ROOT}/.venv"
    info "  ${OPENIVR_ROOT}/.venv/bin/pip install -r ${OPENIVR_ROOT}/requirements.txt"
  fi
fi

# -------------------------------------------------------------- disk + ffmpeg
FREE_GB="$(df -Pk "${OPENIVR_ROOT}" 2>/dev/null | awk 'NR==2 {printf "%d", $4/1048576}')"
if [ -n "${FREE_GB}" ] && [ "${FREE_GB}" -ge 2 ]; then
  ok "${FREE_GB} GB free in ${OPENIVR_ROOT} (2 GB required)"
else
  warn "only ${FREE_GB:-?} GB free in ${OPENIVR_ROOT} - Asterisk needs >= 2 GB"
fi

if have ffmpeg; then
  FFMPEG_PRESENT=true
  FFMPEG_REQUESTED="$(ask_yes_no "ffmpeg is installed - use it to convert uploaded audio?" "y")"
else
  FFMPEG_PRESENT=false
  FFMPEG_REQUESTED="$(ask_yes_no "Install ffmpeg so the builder can convert uploaded audio?" "y")"
fi
if [ "${FFMPEG_REQUESTED}" = "y" ] && [ "${FFMPEG_PRESENT}" = "false" ]; then
  case "${PKG}" in
    apt) apt-get update -qq && apt-get install -y ffmpeg || warn "ffmpeg install failed" ;;
    dnf) dnf install -y ffmpeg || warn "ffmpeg install failed" ;;
  esac
  have ffmpeg && ok "ffmpeg installed" || warn "ffmpeg is still missing - only .wav/.gsm/.ulaw uploads will be accepted"
fi
have ffmpeg && FFMPEG_PRESENT=true || FFMPEG_PRESENT=false

# ---------------------------------------------------------------- firewall?
if have ufw || have firewall-cmd; then
  DEFAULT_FW="y"
else
  DEFAULT_FW="n"
fi
FIREWALL_ACTIVE="$(ask_yes_no "Is a firewall (ufw/firewalld) active on this system?" "${DEFAULT_FW}")"
ok "firewall active: ${FIREWALL_ACTIVE} (step 20 opens the ports)"

# -------------------------------------------------------------- directories
if [ "${ASTERISK_ETC}" = "/etc/asterisk" ]; then
  if [ -d "${ASTERISK_ETC}" ]; then
    ok "asterisk config dir ${ASTERISK_ETC}"
  else
    info "asterisk not installed yet (step 10 installs it)"
  fi
fi

mkdir -p "${ASTERISK_SPOOL}/recording" "${ASTERISK_LIB}/sounds/custom"
chown -R "${OPENIVR_USER}:${OPENIVR_GROUP}" "${ASTERISK_SPOOL}/recording" 2>/dev/null || true
chown -R "${OPENIVR_USER}:${OPENIVR_GROUP}" "${ASTERISK_LIB}/sounds" 2>/dev/null || true
ok "asterisk spool + custom sounds dir ready"

mkdir -p "${OPENIVR_ROOT}/data"/{sounds,recordings,logs,flows}
ok "openivr data dirs ready under ${OPENIVR_ROOT}/data"

# Keys stay `preflight` so an existing system/.state/preflight.json keeps
# merging into system.json exactly as before the renumbering.
write_step "preflight" "$(printf '{"os": %s, "os_version": %s, "arch": %s, "pkg_manager": %s, "python": %s, "python_bin": %s, "ffmpeg_present": %s, "ffmpeg_requested": %s, "firewall_active": %s, "disk_free_gb": %s, "missing_tools": %s, "checked_at": %s}' \
  "$(json_escape "${OS_ID}")" "$(json_escape "${OS_VER}")" "$(json_escape "${ARCH}")" \
  "$(json_escape "${PKG}")" "$(json_escape "$(python3 --version 2>&1)")" "$(json_escape "${OPENIVR_PYTHON}")" \
  "$(bool_json "${FFMPEG_PRESENT}")" "$(bool_json "${FFMPEG_REQUESTED}")" "$(bool_json "${FIREWALL_ACTIVE}")" \
  "${FREE_GB:-0}" "$(json_escape "${MISSING# }")" "$(json_escape "$(date -Is)")")"