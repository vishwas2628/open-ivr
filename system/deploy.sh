#!/usr/bin/env bash
# openivr - deploy the builder's Asterisk configuration (no sudo).
#
#   ./system/deploy.sh              # render, copy to /etc/asterisk, reload
#   ./system/deploy.sh --no-reload  # skip the Asterisk reload
#   ./system/deploy.sh --staging    # only render into data/asterisk-build
#
# This is what `make deploy` runs. It never calls sudo: the installer granted
# the asterisk group write access on /etc/asterisk once (see
# `sudo ./system/install.sh --grant-permissions`).
. "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
. "$(dirname "${BASH_SOURCE[0]}")/lib/permissions.sh"

RELOAD=1
DEST="${ASTERISK_ETC}"
EXTRA=()

while [ $# -gt 0 ]; do
  case "$1" in
    --no-reload) RELOAD=0; shift ;;
    --staging) DEST=""; shift ;;
    --dest) DEST="$2"; shift 2 ;;
    --help|-h)
      sed -n '2,12p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) usage_error() { err "unknown option $1"; exit 2; }; usage_error ;;
  esac
done

step "Deploying the Asterisk configuration"

if ! have "${OPENIVR_PYTHON}"; then
  die "no python interpreter (${OPENIVR_PYTHON}) - run: python3 -m venv ${OPENIVR_ROOT}/.venv"
fi

if [ -n "${DEST}" ] && ! can_write_etc && [ "$(id -u)" -ne 0 ]; then
  warn "$(deploy_requires_root_message)"
  exit 1
fi

if [ -z "${DEST}" ]; then
  log "staging only: ${OPENIVR_ROOT}/data/asterisk-build"
  EXTRA+=(--no-reload)
elif ! can_write_etc; then
  die "cannot write to ${DEST}"
fi

( cd "${OPENIVR_ROOT}" && PYTHONPATH="${OPENIVR_ROOT}" \
  ${DEST:+OPENIVR_ASTERISK_ETC="${DEST}"} \
  "${OPENIVR_PYTHON}" -m openivr publish ${EXTRA[@]+"${EXTRA[@]}"} ${DEST:+--dest "${DEST}"} )

# Prompt files uploaded in the builder live in data/sounds; Asterisk only plays
# what is in its own sounds folder, so push them across (group-writable, no sudo).
if [ -d "${OPENIVR_ROOT}/data/sounds" ]; then
  log "syncing prompts into ${ASTERISK_LIB}/sounds/custom ..."
  OPENIVR_SKIP_RELOAD=1 bash "${OPENIVR_ROOT}/system/sync-sounds.sh" copy >/dev/null 2>&1 \
    && ok "prompts synced" \
    || warn "could not sync prompts (run: bash system/sync-sounds.sh)"
fi

if [ "${RELOAD}" -eq 1 ]; then
  bash "${OPENIVR_ROOT}/scripts/asterisk_reload.sh" || warn "reload did not succeed"
fi

ok "deploy finished"