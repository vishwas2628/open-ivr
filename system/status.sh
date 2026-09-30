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
  ok "open-ivr.service: $(systemctl is-active open-ivr)"
  systemctl --no-pager --lines=5 status open-ivr 2>/dev/null | sed 's/^/     /' || true
else
  info "open-ivr.service: not running"
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

sounds=$(find "${OPENIVR_ROOT}/data/sounds" -maxdepth 1 -type f -name '*.wav' 2>/dev/null | wc -l)
info "prompts in data/sounds: ${sounds}"

cdr="${OPENIVR_ROOT}/data/logs/cdr.csv"
if [ -f "${cdr}" ]; then
  info "CDR rows: $(( $(wc -l < "${cdr}") - 1 ))  (${cdr})"
else
  info "no CDR file yet (${cdr})"
fi

vms=$(find "${OPENIVR_ROOT}/data/recordings/voicemail" -type f 2>/dev/null | wc -l)
info "voicemails stored: ${vms}"
printf '\n'