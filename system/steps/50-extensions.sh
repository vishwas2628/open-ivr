#!/usr/bin/env bash
# 50 - local extensions + IVR dialplan (uses skeleton includes)
. "$(dirname "${BASH_SOURCE[0]}")/../lib/common.sh"

step "Extensions and dialplan (skeleton-backed)"

EXT_COUNT="$(ask "How many local extensions (softphone/desk phones)?" "3")"
EXT_START="${EXT_START:-6001}"
CODEC="${CODEC:-ulaw}"
MIXMONITOR="$(ask_yes_no "Record human-to-human calls with MixMonitor?" "n")"
RECORD_DIR="${RECORD_DIR:-${ASTERISK_SPOOL}/monitor}"
STASIS_APP="${STASIS_APP:-openivr}"
DIAL_TIMEOUT="${DIAL_TIMEOUT:-45}"

mkdir -p "${ASTERISK_ETC}/endpoints"
mkdir -p "${ASTERISK_ETC}/extensions"

# --- PJSIP user endpoints: one file per user in endpoints/ -------------------
# Uses the .skel_endpoint.conf template. Section naming:
#   <num>           type=endpoint
#   <num>-aor       type=aor
#   <num>-auth      type=auth

for i in $(seq 1 "${EXT_COUNT}"); do
  num=$((EXT_START + i - 1))
  cat > "${ASTERISK_ETC}/endpoints/${num}.conf" <<EOF
; managed by openivr system/steps/50-extensions.sh
[${num}]
type = endpoint
transport = transport-udp
context = extensions
callerid = "${num}" <${num}>
disallow = all
allow = ${CODEC},ulaw,alaw,opus
direct_media = no
rtp_symmetric = yes
rewrite_contact = yes
force_rport = yes
auth = ${num}-auth
aors = ${num}-aor


[${num}-aor]
type = aor
max_contacts = 5
remove_existing = yes
qualify_frequency = 30


[${num}-auth]
type = auth
auth_type = userpass
username = ${num}
password = SECRET_PASSWORD_CHANGE_ME
EOF
  # Fix permissions
  chown "${OPENIVR_USER}:${OPENIVR_GROUP}" "${ASTERISK_ETC}/endpoints/${num}.conf" 2>/dev/null || true
done
ok "wrote ${EXT_COUNT} endpoint file(s) to endpoints/ (${EXT_START}-$((EXT_START + EXT_COUNT - 1)))"

# --- Dialplan: one extensions/<N>.conf per extension -------------------------
# Each file contributes `exten => <N>` to the shared [extensions] context.
# The skeleton's extensions.conf already has [from-trunk], [openivr-ivr],
# [openivr-dial], [extensions] (catch-all), and #tryinclude extensions/*.conf.
# We just add the per-extension ring entries here.

for i in seq 1 "${EXT_COUNT}"; do
  num=$((EXT_START + i - 1))
  cat > "${ASTERISK_ETC}/extensions/${num}.conf" <<EOF
; managed by openivr system/steps/50-extensions.sh
[extensions]
exten => ${num},1,NoOp(ringing extension ${num})
   same => n,Dial(PJSIP/${num},${DIAL_TIMEOUT})
   same => n,Hangup()
EOF
  chown "${OPENIVR_USER}:${OPENIVR_GROUP}" "${ASTERISK_ETC}/extensions/${num}.conf" 2>/dev/null || true
done
ok "wrote ${EXT_COUNT} dialplan extension file(s) to extensions/"

# Note: [openivr-dial], [from-trunk], [openivr-ivr], [extensions] are in the
# skeleton's extensions.conf. The step no longer writes openivr.conf.

mkdir -p "${RECORD_DIR}"
chown -R "${OPENIVR_USER}:${OPENIVR_GROUP}" "${RECORD_DIR}" 2>/dev/null || true

asterisk_reload

EXT_LIST="$(seq -s, "${EXT_START}" $((EXT_START + EXT_COUNT - 1)))"
write_step "extensions" "$(printf '{"extensions": {"count": %s, "start": %s, "codec": "%s", "list": "%s"}, "dialplan": {"context": "extensions", "dial_context": "openivr-dial", "stasis_app": "%s", "mixmonitor": %s, "record_dir": "%s", "dial_timeout": %s}}' \
  "${EXT_COUNT}" "${EXT_START}" "${CODEC}" "${EXT_LIST}" "${STASIS_APP}" \
  "$( [ "${MIXMONITOR}" = "y" ] && echo true || echo false)" "${RECORD_DIR}" "${DIAL_TIMEOUT}")"

info "Test internal: asterisk -rx 'channel originate Local/${EXT_START}@extensions application Playback demo-congrats'"
info "Place calls to PJSIP/${EXT_START} from a softphone (password: SECRET_PASSWORD_CHANGE_ME)"