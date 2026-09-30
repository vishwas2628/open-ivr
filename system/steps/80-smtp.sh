#!/usr/bin/env bash
# 80 - SMTP for voicemail delivery + service-down alerts
. "$(dirname "${BASH_SOURCE[0]}")/../lib/common.sh"

step "SMTP (voicemail mail + service alerts)"

log "the plan makes SMTP mandatory so a dead IVR raises an e-mail alarm"
info "you can also set it later in the builder (/smtp) or in data/smtp.json"

if [ "$(ask_yes_no "Configure an outgoing mail server now?" "y")" != "y" ]; then
  warn "without SMTP the IVR cannot alert you when ARI goes down"
  write_step "smtp" '{"smtp": {"enabled": false}}'
  return 0 2>/dev/null || exit 0
fi

SMTP_HOST="$(ask "SMTP host" "smtp.gmail.com")"
SMTP_PORT="$(ask "SMTP port" "587")"
SMTP_USER="$(ask "Username" "")"
SMTP_PASS="$(ask "Password / app password" "")"
SMTP_FROM="$(ask "From address" "${SMTP_USER}")"
SMTP_TO="$(ask "Recipient(s) for alerts and voicemails (comma separated)" "${SMTP_USER}")"
STARTTLS="$(ask_yes_no "Use STARTTLS?" "y")"

if [ -z "${SMTP_HOST}" ] || [ -z "${SMTP_TO}" ]; then
  warn "host and recipient are required - SMTP left disabled"
  write_step "smtp" '{"smtp": {"enabled": false}}'
  return 0 2>/dev/null || exit 0
fi

TO_JSON=$(printf '%s' "${SMTP_TO}" | python3 -c 'import json,sys; print(json.dumps([x.strip() for x in sys.stdin.read().split(",") if x.strip()]))')

write_step "smtp" "$(printf '{"smtp": {"enabled": true, "host": "%s", "port": %s, "starttls": %s, "username": "%s", "password": "%s", "from_addr": "%s", "alerts_to": %s}}' \
  "${SMTP_HOST}" "${SMTP_PORT}" "$( [ "${STARTTLS}" = "y" ] && echo true || echo false)" \
  "${SMTP_USER}" "${SMTP_PASS}" "${SMTP_FROM}" "${TO_JSON}")"

ok "SMTP settings stored in system.json (mode 600)"

if python3 - "${SMTP_HOST}" "${SMTP_PORT}" <<'PY'
import smtplib, sys
host, port = sys.argv[1], int(sys.argv[2])
try:
    with smtplib.SMTP(host, port, timeout=10):
        pass
except Exception as exc:
    print(f"  !! cannot reach {host}:{port}: {exc}")
    raise SystemExit(1)
PY
then ok "SMTP host reachable"
else warn "SMTP host unreachable - check the settings later with the builder"
fi