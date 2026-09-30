#!/usr/bin/env bash
# openivr - system installer orchestrator
#
# Runs every step in order, collects the answers into ./system.json (the single
# JSON file plan.md asks for) and leaves the machine ready for the builder.
#
#   sudo ./system/install.sh              # interactive
#   NONINTERACTIVE=1 sudo ./system/install.sh
#   STEPS="30-ari.sh" sudo ./system/install.sh   # just one step
. "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

MODE="${1:-install}"

usage() {
  cat <<EOF
usage: sudo ./system/install.sh [options]

  --steps "10-asterisk.sh 30-ari.sh"   run only these steps
  --from 20-firewall.sh               run from this step to the end
  --no-interact                       never prompt (use defaults)
  --reset                             wipe ./system/.state first
  --help

Steps, in order:
  00-preflight  OS, tools, directories
  10-asterisk   install Asterisk (packages, or SOURCE_BUILD=1 from source)
  20-firewall   open SIP/RTP/HTTP ports
  30-ari        ari.conf, http.conf, rtp.conf, modules
  40-trunk      SIP trunk (optional)
  50-extensions local extensions + IVR dialplan
  60-database   PostgreSQL CDR backend (optional)
  70-recording  recording folders + cron
  80-smtp       SMTP for voicemail and alerts
EOF
}

STEPS_OVERRIDE=""
FROM=""
while [ $# -gt 0 ]; do
  case "$1" in
    --steps) STEPS_OVERRIDE="$2"; shift 2 ;;
    --from) FROM="$2"; shift 2 ;;
    --no-interact) export NONINTERACTIVE=1; shift ;;
    --reset) rm -rf "${STATE_DIR}"; shift ;;
    --help|-h) usage; exit 0 ;;
    *) usage; exit 1 ;;
  esac
done

[ "${MODE}" = "--help" ] && { usage; exit 0; }

require_root

printf '\n%s\n' "${C_BOLD}openivr system installer${C_RESET}"
info "root    : ${OPENIVR_ROOT}"
info "state   : ${STATE_DIR}"
info "asterisk: ${ASTERISK_ETC}"

if [ -n "${STEPS_OVERRIDE}" ]; then
  read -r -a STEP_LIST <<< "${STEPS_OVERRIDE}"
elif [ -n "${FROM}" ]; then
  read -r -a STEP_LIST <<< "$(cd "${SYSTEM_DIR}/steps" && ls *.sh | sort | awk -v f="${FROM}" '$0 >= f')"
else
  read -r -a STEP_LIST <<< "$(cd "${SYSTEM_DIR}/steps" && ls *.sh | sort)"
fi

STARTED_AT="$(date -Is)"
FAILED=()

for step_file in "${STEP_LIST[@]}"; do
  step_path="${SYSTEM_DIR}/steps/${step_file}"
  [ -f "${step_path}" ] || { warn "missing step ${step_file}"; continue; }
  if ! OPENIVR_COMMON_SOURCED="" bash "${step_path}"; then
    err "step ${step_file} failed"
    FAILED+=("${step_file}")
  fi
done

step "Writing system.json"

mkdir -p "${OPENIVR_ROOT}"
merge_steps "${OPENIVR_ROOT}/system.json"

python3 - "${OPENIVR_ROOT}/system.json" "${STARTED_AT}" <<'PY'
import json, pathlib, sys, time
path = pathlib.Path(sys.argv[1])
data = json.loads(path.read_text())
data["_meta"] = {
    "generated_at": sys.argv[2],
    "generated_ts": int(time.time()),
    "generator": "openivr system/install.sh",
}
path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
PY

# system.json holds the ARI, database and SMTP passwords, so it must not be
# world readable - but it also has to stay usable by the person who installed
# openivr (the builder and the CLI run as them) and by the service account.
OPENIVR_OWNER="${SUDO_USER:-${USER:-root}}"
if getent group "${OPENIVR_GROUP}" >/dev/null 2>&1; then
  chown "${OPENIVR_OWNER}:${OPENIVR_GROUP}" "${OPENIVR_ROOT}/system.json"
  chmod 0640 "${OPENIVR_ROOT}/system.json"
  info "system.json is ${OPENIVR_OWNER}:${OPENIVR_GROUP} 0640 (owner + ${OPENIVR_GROUP} group)"
else
  chown "${OPENIVR_OWNER}" "${OPENIVR_ROOT}/system.json"
  chmod 0600 "${OPENIVR_ROOT}/system.json"
fi
chown -R "${OPENIVR_OWNER}" "${STATE_DIR}" 2>/dev/null || true

ok "system.json written to ${OPENIVR_ROOT}/system.json"

step "Summary"
python3 - "${OPENIVR_ROOT}/system.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
ari = d.get("ari") or {}
ext = (d.get("extensions") or {})
trunk = d.get("trunk") or {}
cdr = d.get("cdr") or {}
pg = cdr.get("postgres") or {}
smtp = d.get("smtp") or {}
rec = d.get("record") or {}
print(f"  ARI        : {ari.get('base_url', '?')} (user {ari.get('username', '?')})")
print(f"  extensions : {ext.get('count', 0)} ({ext.get('list', 'none')})")
print(f"  trunk      : {trunk.get('name', 'not configured')}")
print(f"  CDR        : {cdr.get('backend', 'csv')}"
      + (f" ({pg.get('user', '?')}@{pg.get('host', '?')}:{pg.get('port', '?')}/{pg.get('dbname', '?')})"
         if cdr.get("backend") in ("postgres", "both") else ""))
print(f"  recording  : MixMonitor={rec.get('mixmonitor_dir', 'n/a')}")
print(f"  SMTP       : {'configured' if smtp.get('enabled') else 'disabled'}")
print(f"  dialplan   : Stasis({(d.get('dialplan') or {}).get('stasis_app', 'openivr')})")
PY

if [ ${#FAILED[@]} -gt 0 ]; then
  warn "failed steps: ${FAILED[*]}"
  exit 1
fi

printf '\n%s\n\n' "${C_GREEN}System module done.${C_RESET} Next: ./run_ivr.sh (builder -> verify -> systemd)"