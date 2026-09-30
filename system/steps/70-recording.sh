#!/usr/bin/env bash
# 70 - recording folders and housekeeping
. "$(dirname "${BASH_SOURCE[0]}")/../lib/common.sh"

step "Recording setup"

RECORD_DIR="${RECORD_DIR:-${ASTERISK_SPOOL}/monitor}"
KEEP_DAYS="${KEEP_DAYS:-30}"

mkdir -p "${RECORD_DIR}" "${ASTERISK_SPOOL}/recording" "${OPENIVR_ROOT}/data/recordings/voicemail"
chown -R "${OPENIVR_USER}:${OPENIVR_GROUP}" "${RECORD_DIR}" "${ASTERISK_SPOOL}/recording" 2>/dev/null || true
chmod 750 "${RECORD_DIR}"
ok "recording directories ready"

# Asterisk cleans its own monitor spool; the IVR's own folder needs a job.
cat > /etc/cron.d/openivr-recordings <<EOF
# managed by openivr - prune IVR recordings and logs older than ${KEEP_DAYS} days
17 4 * * * ${OPENIVR_USER} find ${OPENIVR_ROOT}/data/recordings -type f -mtime +${KEEP_DAYS} -delete 2>/dev/null
23 4 * * * ${OPENIVR_USER} find ${OPENIVR_ROOT}/data/logs -type f -name '*.log.*' -mtime +${KEEP_DAYS} -delete 2>/dev/null
EOF
chmod 0644 /etc/cron.d/openivr-recordings 2>/dev/null || warn "could not install the cron job"
ok "cron job installed (keeps ${KEEP_DAYS} days)"

if have asteriskctl; then
  asterisk_reload
  asterisk_running && ok "asterisk reloaded" || true
fi

write_step "recording" "$(printf '{"record": {"mixmonitor_dir": "%s", "keep_days": %s, "cron": "/etc/cron.d/openivr-recordings"}}' \
  "${RECORD_DIR}" "${KEEP_DAYS}")"