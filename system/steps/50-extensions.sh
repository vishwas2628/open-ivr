#!/usr/bin/env bash
# 50 - local extensions + the IVR dialplan (Stasis, dialplan handoff)
. "$(dirname "${BASH_SOURCE[0]}")/../lib/common.sh"

step "Extensions and dialplan"

EXT_COUNT="$(ask "How many local extensions (softphone/desk phones)?" "3")"
EXT_START="${EXT_START:-6001}"
CODEC="${CODEC:-ulaw}"
MIXMONITOR="$(ask_yes_no "Record human-to-human calls with MixMonitor?" "n")"
RECORD_DIR="${RECORD_DIR:-${ASTERISK_SPOOL}/monitor}"
STASIS_APP="${STASIS_APP:-openivr}"
DIAL_TIMEOUT="${DIAL_TIMEOUT:-45}"

mkdir -p "${ASTERISK_ETC}"

PJSIP_EXT_CONF="${ASTERISK_ETC}/pjsip-extensions.conf"
OPENIVR_CONF="${ASTERISK_ETC}/openivr.conf"
backup_conf "${PJSIP_EXT_CONF}"
backup_conf "${OPENIVR_CONF}"

# --- extensions -------------------------------------------------------------
# Section names must be unique per extension: pjsip loads every [type=X] section
# it finds, so a repeated [endpoint] would leave only the last one configured.
{
  echo "; managed by openivr system/steps/50-extensions.sh"
  for i in $(seq 1 "${EXT_COUNT}"); do
    num=$((EXT_START + i - 1))
    cat <<EOF
[endpoint-${num}]
type = endpoint
context = openivr-internal
allow = !all,${CODEC}-${num}
direct_media = no
rtp_symmetric = yes
dtmf_mode = rfc4733
callerid = ${num} <${num}>
aors = aor-${num}

[aor-${num}]
type = aor
max_contacts = 1
remove_existing = yes

[${CODEC}-${num}]
type = allow
EOF
    echo
  done
} > "${PJSIP_EXT_CONF}"
ok "wrote ${EXT_COUNT} extension(s) ${EXT_START}-$((EXT_START + EXT_COUNT - 1)) in pjsip-extensions.conf"

# --- dialplan ---------------------------------------------------------------
{
  cat <<EOF
;
; openivr IVR dialplan - managed by openivr system/steps/50-extensions.sh
;
; Inbound (from trunk or extension):
;   Answer -> optional recording -> Stasis(${STASIS_APP})
; The IVR decides what happens next; a 'dial' action hands the channel back
; to [openivr-dial] below (this is why the CDR stays clean - see plan.md).
;

[general]
static = yes
writeprotect = no

[openivr-internal]
exten => _X.,1,NoOp(internal call to \${EXTEN})
  same => n,Dial(PJSIP/\${EXTEN},30)
  same => n,Hangup()

[from-trunk]
exten => _X.,1,NoOp(inbound call from \${CALLERID(num)})
$( [ "${MIXMONITOR}" = "y" ] && echo "  same => n,MixMonitor(\${UNIQUEID}.wav,b)" )
  same => n,Answer()
  same => n,Stasis(${STASIS_APP},\${EXTEN},\${CALLERID(num)},\${CALLERID(name)})
  same => n,Hangup()

[openivr-ivr]
; Entry point for the IVR: this is what your carrier sends calls to.
exten => s,1,NoOp(=== openivr ${STASIS_APP} ===)
$( [ "${MIXMONITOR}" = "y" ] && echo "  same => n,MixMonitor(\${UNIQUEID}.wav,b)" )
  same => n,Answer()
  same => n,Stasis(${STASIS_APP},s,\${CALLERID(num)},\${CALLERID(name)})
  same => n,Hangup()

[openivr-dial]
; Reached from an IVR 'dial' action via continueInDialplan().
; \${OPENIVR_DIAL_TARGET} was set by the IVR right before handing over.
exten => s,1,NoOp(openivr dialing \${OPENIVR_DIAL_TARGET} (call \${OPENIVR_CALL_ID}))
  same => n,GotoIf(\$["\${OPENIVR_DIAL_TARGET}" != ""]?dial)
  same => n,Playback(please-leave-a-message-after-the-tone)
  same => n,Hangup()
  same => n(dial),Set(CDRIVRDEST=\${OPENIVR_DIAL_TARGET})
$( [ "${MIXMONITOR}" = "y" ] && echo "  same => n,MixMonitor(\${UNIQUEID}.wav,b)" )
  same => n,Dial(\${OPENIVR_DIAL_TARGET},${DIAL_TIMEOUT})
  same => n,Goto(openivr-ivr,s,1)
  same => n,Hangup()
EOF
} > "${OPENIVR_CONF}"
ok "wrote ${OPENIVR_CONF} (MixMonitor: ${MIXMONITOR})"

mkdir -p "${RECORD_DIR}"
chown -R "${OPENIVR_USER}:${OPENIVR_GROUP}" "${RECORD_DIR}" 2>/dev/null || true

asterisk_reload

EXT_LIST="$(seq -s, "${EXT_START}" $((EXT_START + EXT_COUNT - 1)))"
write_step "extensions" "$(printf '{"extensions": {"count": %s, "start": %s, "codec": "%s", "list": "%s"}, "dialplan": {"context": "openivr-ivr", "dial_context": "openivr-dial", "stasis_app": "%s", "mixmonitor": %s, "record_dir": "%s", "dial_timeout": %s}}' \
  "${EXT_COUNT}" "${EXT_START}" "${CODEC}" "${EXT_LIST}" "${STASIS_APP}" \
  "$( [ "${MIXMONITOR}" = "y" ] && echo true || echo false)" "${RECORD_DIR}" "${DIAL_TIMEOUT}")"

info "test with: asterisk -rx 'channel originate Local/6001@test application Playback demo-congrats'"
info "and place calls to PJSIP/${EXT_START} from a softphone"