#!/usr/bin/env bash
# 40 - SIP trunk to the outside world
. "$(dirname "${BASH_SOURCE[0]}")/../lib/common.sh"

step "SIP trunk"

log "skipping this step is fine for local testing with softphones"
if [ "$(ask_yes_no "Do you have a SIP trunk / SIP account from a provider?" "n")" != "y" ]; then
  info "no trunk configured - you can still test with local extensions (step 50)"
  write_step "trunk" '{"trunk": {"configured": false}}'
  return 0 2>/dev/null || exit 0
fi

TRUNK_NAME="$(ask "Trunk endpoint name (e.g. myprovider)" "trunk1")"
TRUNK_TYPE="$(ask "Type (sip / iax)" "sip")"
TRUNK_HOST="$(ask "Provider host or IP" "")"
TRUNK_USER="$(ask "Username / account" "")"
TRUNK_CONTEXT="$(ask "Incoming context (where inbound calls land)" "from-trunk")"

if [ -z "${TRUNK_HOST}" ] || [ -z "${TRUNK_USER}" ]; then
  warn "host and username are required - skipping trunk"
  write_step "trunk" '{"trunk": {"configured": false}}'
  return 0 2>/dev/null || exit 0
fi

if [ "$(ask_yes_no "Do you have an auth password?" "y")" = "y" ]; then
  TRUNK_PASS="$(ask "Password" "")"
  TRUNK_AUTH=1
else
  TRUNK_PASS=""
  TRUNK_AUTH=0
fi

mkdir -p "${ASTERISK_ETC}"
backup_conf "${ASTERISK_ETC}/pjsip-trunk.conf"
cat > "${ASTERISK_ETC}/pjsip-trunk.conf" <<EOF
; managed by openivr system/steps/40-trunk.sh
[${TRUNK_TYPE}:${TRUNK_NAME}]
type = ${TRUNK_TYPE}
endpoint = ${TRUNK_NAME}
host = ${TRUNK_HOST}
default_from_user = ${TRUNK_USER}
$( [ "${TRUNK_AUTH}" = "1" ] && printf 'auth_type = userpass\nusername = %s\npassword = %s\n' "${TRUNK_USER}" "${TRUNK_PASS}" )
context = ${TRUNK_CONTEXT}
direct_media = no
insecure = yes
rewrite_contact = yes
dtmf_mode = rfc4733
allow = !all,ulaw,alaw
$( [ "${TRUNK_TYPE}" = "sip" ] && printf 'outbound_auth = %s\n' "${TRUNK_NAME}" )

[${TRUNK_TYPE}:${TRUNK_NAME}/outbound]
type = ${TRUNK_TYPE}
endpoint = ${TRUNK_NAME}
caller_id = ${TRUNK_USER}
dtmf_mode = rfc4733
EOF
ok "wrote ${ASTERISK_ETC}/pjsip-trunk.conf (${TRUNK_TYPE}:${TRUNK_NAME})"

write_step "trunk" "$(printf '{"trunk": {"configured": true, "type": "%s", "name": "%s", "host": "%s", "username": "%s", "context": "%s", "auth": %s}}' \
  "${TRUNK_TYPE}" "${TRUNK_NAME}" "${TRUNK_HOST}" "${TRUNK_USER}" "${TRUNK_CONTEXT}" \
  "$( [ "${TRUNK_AUTH}" = "1" ] && echo true || echo false)")"

info "inbound: dialplan context ${TRUNK_CONTEXT} -> Stasis(openivr)"
info "outbound: dial PJSIP/${TRUNK_NAME} from a flow 'dial' action"