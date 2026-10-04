#!/usr/bin/env bash
# Quick status of everything openivr touches.
. "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

printf '\n%s\n' "${C_BOLD}openivr status${C_RESET}"

if asterisk_running; then
  ok "asterisk: $(asteriskctl version 2>/dev/null | head -1)"
else
  err "asterisk: not running"
fi

if [ -f "${OPENIVR_ROOT}/system.json" ]; then
  ok "system.json present"
  python3 - "${OPENIVR_ROOT}/system.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
ari = d.get("ari") or {}
print(f"     ARI     {ari.get('base_url','?')} as {ari.get('username','?')}")
ext = d.get("extensions") or {}
print(f"     ext     {ext.get('list','none')} ({ext.get('codec','?')})")
print(f"     trunk   {(d.get('trunk') or {}).get('name','none')}")
print(f"     smtp    {'on' if (d.get('smtp') or {}).get('enabled') else 'off'}")
PY
else
  warn "system.json missing - run: sudo ./system/install.sh"
fi

if systemctl is-active --quiet open-ivr 2>/dev/null; then
  ok "openivr.service: $(systemctl is-active openivr)"
  systemctl --no-pager --lines=5 status open-ivr 2>/dev/null | sed 's/^/     /' || true
else
  info "openivr.service: not running"
fi

if [ -f "${OPENIVR_ROOT}/config.yaml" ]; then
  cfg_mode="$(stat -c '%a %U:%G' "${OPENIVR_ROOT}/config.yaml")"
  ok "config.yaml present (${cfg_mode})"
else
  warn "config.yaml missing - the builder creates it on first run"
fi

permissions="${OPENIVR_ROOT}/data/permissions.json"
if [ -f "${permissions}" ]; then
  python3 - "${permissions}" <<'PY2'
import json, sys
d = json.load(open(sys.argv[1])).get("permissions", {})
print(f"     record  {'on ' + str(d.get('recording_dir')) if d.get('recording_enabled') else 'off'}")
print(f"     vm      {d.get('voicemail_dir') if d.get('voicemail_enabled') else 'off'}")
PY2
fi

flow="${OPENIVR_ROOT}/data/ivr_flow.json"
if [ -f "${flow}" ]; then
  ok "flow: ${flow}"
  python3 - "${flow}" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
menus = d.get("menus", {})
print(f"     start={d.get('start_menu')} menus={len(menus)} "
      f"options={sum(len(m.get('options', {})) for m in menus.values())}")
PY
else
  warn "no flow file - build one with the builder"
fi

sounds=$(find "${OPENIVR_ROOT}/data/sounds" -maxdepth 1 -type f \( -name '*.wav' -o -name '*.ulaw' -o -name '*.gsm' \) 2>/dev/null | wc -l)
info "prompts in data/sounds: ${sounds}"

staging="${OPENIVR_ROOT}/data/asterisk-build"
[ -d "${staging}" ] && info "staged configs: $(find "${staging}" -type f | wc -l) in ${staging}"

cdr="${OPENIVR_ROOT}/data/logs/cdr.csv"
if [ -f "${cdr}" ]; then
  info "CDR rows: $(( $(wc -l < "${cdr}") - 1 ))  (${cdr})"
else
  info "no CDR file yet (${cdr})"
fi

vms=$(find "${OPENIVR_ROOT}/data/recordings/voicemail" -type f 2>/dev/null | wc -l)
info "voicemails stored: ${vms}"
printf '\n'