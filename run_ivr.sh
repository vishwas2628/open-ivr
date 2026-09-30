#!/usr/bin/env bash
# openivr - the global orchestrator described in plan.md
#
#   1. system module  - install/configure Asterisk + ARI (needs sudo, writes system.json)
#   2. builder       - web IVR builder (FastAPI) at http://127.0.0.1:8090
#   3. verification  - config, flow, media, ARI, ports
#   4. systemd       - install and start the open-ivr service
#
# usage: ./run_ivr.sh [--skip-install] [--no-builder] [--no-start] [--port 8090]
set -euo pipefail

OPENIVR_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${OPENIVR_ROOT}"

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  C_RESET=$'\033[0m'; C_RED=$'\033[31m'; C_GREEN=$'\033[32m'
  C_YELLOW=$'\033[33m'; C_BLUE=$'\033[34m'; C_BOLD=$'\033[1m'
else
  C_RESET=""; C_RED=""; C_GREEN=""; C_YELLOW=""; C_BLUE=""; C_BOLD=""
fi

SKIP_INSTALL=0; RUN_BUILDER=1; START_SERVICE=1; BUILDER_PORT="${BUILDER_PORT:-8090}"
BUILDER_HOST="${BUILDER_HOST:-127.0.0.1}"
KEEP_OPEN=0

usage() {
  cat <<EOF
usage: ./run_ivr.sh [options]

  --skip-install   do not run the system module (Asterisk/ARI install)
  --no-builder     skip the web builder step
  --keep-open      keep the builder running after the flow is saved
  --no-start       do not (re)start the systemd service
  --port N         builder port (default 8090)
  --host ADDR      builder bind address (default 127.0.0.1)
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --skip-install) SKIP_INSTALL=1; shift ;;
    --no-builder) RUN_BUILDER=0; shift ;;
    --keep-open) KEEP_OPEN=1; shift ;;
    --no-start) START_SERVICE=0; shift ;;
    --port) BUILDER_PORT="$2"; shift 2 ;;
    --host) BUILDER_HOST="$2"; shift 2 ;;
    --help|-h) usage; exit 0 ;;
    *) printf 'unknown option: %s\n' "$1"; usage; exit 1 ;;
  esac
done

banner() {
  # Gradient: each row gets its own 24-bit colour, indigo (top) -> cyan (bottom).
  local rows=(
     '██████╗ ██████╗ ███████╗███╗   ██╗      ██╗██╗   ██╗██████╗ '
    '██╔═══██╗██╔══██╗██╔════╝████╗  ██║      ██║██║   ██║██╔══██╗'
    '██║   ██║██████╔╝█████╗  ██╔██╗ ██║█████╗██║██║   ██║██████╔╝'
    '██║   ██║██╔═══╝ ██╔══╝  ██║╚██╗██║╚════╝██║╚██╗ ██╔╝██╔══██╗'
    '╚██████╔╝██║     ███████╗██║ ╚████║      ██║ ╚████╔╝ ██║  ██║'
    ' ╚═════╝ ╚═╝     ╚══════╝╚═╝  ╚═══╝      ╚═╝  ╚═══╝  ╚═╝  ╚═╝'
  )
  local n=${#rows[@]} i r g b
  if [ -z "${C_BOLD}" ]; then
    for ((i = 0; i < n; i++)); do printf '%s\n' "${rows[i]}"; done
    printf '\n  openivr - self hosted IVR\n\n'
    return
  fi
  for ((i = 0; i < n; i++)); do
    r=$((99 + (88 - 99) * i / (n - 1)))
    g=$((68 + (215 - 68) * i / (n - 1)))
    b=$((255 - (255 - 255) * i / (n - 1)))
    printf '\033[38;2;%d;%d;%dm%s\033[0m\n' "$r" "$g" "$b" "${C_BOLD}${rows[i]}${C_RESET}"
  done
  printf '\n%s\n\n' "${C_BOLD}  openivr - self hosted IVR${C_RESET}"
}

step_header() { printf '\n%s\n' "${C_BOLD}${C_BLUE}=== Step $1: $2 ===${C_RESET}"; }
info()  { printf '%s\n' "     $*"; }
ok()    { printf '%s\n' "${C_GREEN}  ok${C_RESET} $*"; }
warn()  { printf '%s\n' "${C_YELLOW}  !!${C_RESET} $*" >&2; }
fail()  { printf '%s\n' "${C_RED}  xx${C_RESET} $*" >&2; }

printf '\n\n '
banner

PYTHON="${OPENIVR_ROOT}/.venv/bin/python"
if [ ! -x "${PYTHON}" ]; then
  fail "no virtualenv at ${OPENIVR_ROOT}/.venv"
  info "create it with: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi

# ---------------------------------------------------------------------------
step_header 1 "System module (Asterisk + ARI)"

if [ "${SKIP_INSTALL}" = "1" ]; then
  warn "skipped (--skip-install)"
elif [ -f "${OPENIVR_ROOT}/system.json" ] && [ "${NONINTERACTIVE:-}" = "1" ]; then
  info "system.json exists and NONINTERACTIVE=1 - skipping the installer"
else
  SUDO=""
  if [ "$(id -u)" -ne 0 ]; then
    if command -v sudo >/dev/null 2>&1; then
      SUDO="sudo"
      info "sudo is needed for the system module; you may be prompted for a password"
    else
      fail "this step needs root privileges; re-run as root or use --skip-install"
      exit 1
    fi
  fi
  ${SUDO} "${OPENIVR_ROOT}/system/install.sh"
  ok "system module finished - system.json written"
fi

# ---------------------------------------------------------------------------
step_header 2 "IVR builder (web)"

if [ "${RUN_BUILDER}" = "0" ]; then
  warn "skipped (--no-builder)"
elif [ "${NONINTERACTIVE:-}" = "1" ]; then
  info "NONINTERACTIVE=1 - not starting the builder"
else
  info "open http://${BUILDER_HOST}:${BUILDER_PORT} and build your IVR"
  info "(remote box? use an SSH tunnel: ssh -L ${BUILDER_PORT}:127.0.0.1:${BUILDER_PORT} user@host)"
  args=(builder --host "${BUILDER_HOST}" --port "${BUILDER_PORT}")
  [ "${KEEP_OPEN}" = "1" ] && args+=(--keep-open)
  "${PYTHON}" -m openivr "${args[@]}"
  ok "builder finished"
fi

# ---------------------------------------------------------------------------
step_header 3 "Verification"

SUDO=""
[ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1 && SUDO="sudo"

info "syncing prompts into Asterisk's sounds folder"
${SUDO} "${OPENIVR_ROOT}/system/sync-sounds.sh" >/dev/null 2>&1 \
  && ok "prompts synced" || warn "prompt sync failed - check permissions on /var/lib/asterisk"

if "${PYTHON}" -m openivr verify; then
  ok "verification passed"
else
  warn "verification reported problems (see above); the service may still start"
fi

# ---------------------------------------------------------------------------
step_header 4 "Service"

if [ "${START_SERVICE}" = "0" ]; then
  warn "skipped (--no-start)"
elif ! command -v systemctl >/dev/null 2>&1; then
  warn "systemd not available - start manually: ${PYTHON} -m openivr run"
else
  UNIT_SRC="${OPENIVR_ROOT}/systemd/open-ivr.service"
  UNIT_DST="/etc/systemd/system/open-ivr.service"
  INSTALL_ROOT="/opt/openivr"

  ${SUDO} mkdir -p "${INSTALL_ROOT}"
  if [ "${OPENIVR_ROOT}" != "${INSTALL_ROOT}" ]; then
    if ! command -v rsync >/dev/null 2>&1; then
      fail "rsync is required to copy the project to ${INSTALL_ROOT} (install it, or re-run with --no-start)"
      exit 1
    fi
    ${SUDO} rsync -a --delete --exclude '.venv' --exclude '.git' --exclude 'data/logs' \
      --exclude 'data/recordings' --exclude '__pycache__' \
      "${OPENIVR_ROOT}/" "${INSTALL_ROOT}/"
    # The venv is excluded by the rsync above, so build it from the interpreter
    # we already know works - not from the (not yet existing) copy in /opt.
    if ! ${SUDO} "${PYTHON}" -m venv "${INSTALL_ROOT}/.venv"; then
      fail "could not create ${INSTALL_ROOT}/.venv"
      info "create it manually: sudo ${PYTHON} -m venv ${INSTALL_ROOT}/.venv"
    elif ! ${SUDO} "${INSTALL_ROOT}/.venv/bin/pip" install -q -r "${INSTALL_ROOT}/requirements.txt"; then
      fail "could not install requirements into ${INSTALL_ROOT}/.venv"
    else
      ok "prepared ${INSTALL_ROOT}/.venv"
    fi
    info "copied the project to ${INSTALL_ROOT} (the unit runs from there)"
  else
    if ! ${SUDO} "${PYTHON}" -m pip install -q -e .; then
      fail "could not install the project into the existing virtualenv"
    fi
  fi

  UNIT_TARGET="${INSTALL_ROOT}/systemd/open-ivr.service"
  ${SUDO} cp "${UNIT_SRC}" "${UNIT_DST}"
  ${SUDO} sed -i "s#/opt/openivr#${INSTALL_ROOT}#g" "${UNIT_DST}"
  ${SUDO} systemctl daemon-reload
  ${SUDO} systemctl enable open-ivr >/dev/null 2>&1 || true
  ${SUDO} systemctl restart open-ivr
  sleep 2
  if systemctl is-active --quiet open-ivr; then
    ok "open-ivr.service is running"
    systemctl --no-pager --lines=8 status open-ivr | sed 's/^/     /' || true
  else
    fail "open-ivr.service did not start"
    info "check: journalctl -u open-ivr -n 50 --no-pager"
  fi
fi

# ---------------------------------------------------------------------------
step_header 5 "Report"

"${PYTHON}" - <<'PY' || true
import json, pathlib, sys

root = pathlib.Path(".").resolve()
sys.path.insert(0, str(root))

try:
    from openivr.config import load_config
    from openivr.flow import Flow
    from openivr.media import available_sounds

    cfg = load_config(root)
    flow = Flow.load(cfg.flow_path)
    prompts = sorted(available_sounds(cfg.sounds_dir))
except Exception as exc:  # noqa: BLE001 - the report must never crash the script
    print(f"  !! cannot read the configuration: {exc}")
    raise SystemExit(0)

print(f"  IVR app        : {cfg.app.stasis_app}")
print(f"  ARI            : {cfg.ari.base_url} as {cfg.ari.username}")
print(f"  dial mode      : {cfg.dial.mode}")
print(f"  menus          : {len(flow.menus)} (start: {flow.start_menu})")
print(f"  prompts        : {len(prompts)} -> {', '.join(prompts[:8]) or 'none'}"
      + (" ..." if len(prompts) > 8 else ""))
print(f"  CDR            : {cfg.cdr.backend} -> {cfg.cdr.csv_file}")
print(f"  voicemail      : {'on' if cfg.voicemail.enabled else 'off'}"
      f" ({cfg.voicemail_dir})")
print(f"  SMTP           : {'on' if cfg.smtp.enabled else 'off'}")
print()
print("  Mocked number registration check (plan.md step 5):")
extensions = []
if cfg.root.joinpath("system.json").exists():
    data = json.loads(cfg.root.joinpath("system.json").read_text())
    extensions = str((data.get("extensions") or {}).get("list") or "").split(",")
for ext in [e for e in extensions if e]:
    print(f"    PJSIP/{ext}      registered (mock)")
print(f"    PJSIP/9000      mock external number -> inbound context openivr-ivr")
print()
print("  Place a test call to an extension or to your inbound number and watch:")
print(f"    journalctl -u open-ivr -f        or    tail -f {cfg.path(cfg.logging.file)}")
PY

printf '\n%s\n' "${C_GREEN}open-ivr is ready.${C_RESET} Useful next commands:"
printf '  %s\n' "  ${PYTHON} -m openivr verify"
printf '  %s\n' "  ${PYTHON} -m openivr flow tree"
printf '  %s\n' "  ${PYTHON} -m openivr builder --port ${BUILDER_PORT}   # edit the flow again"
printf '  %s\n' "  sudo ${OPENIVR_ROOT}/system/status.sh"
printf '\n'