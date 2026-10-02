#!/usr/bin/env bash
# 30 - ARI + HTTP + RTP configuration (matches skeleton)
. "$(dirname "${BASH_SOURCE[0]}")/../lib/common.sh"

step "ARI configuration"

ARI_USER="${ARI_USER:-openivr}"
ARI_PORT="${ARI_PORT:-8088}"
ARI_BIND="${ARI_BIND:-127.0.0.1}"
RTP_START="${RTP_START:-10000}"
RTP_END="${RTP_END:-20000}"
MEDIA_ADDR="${MEDIA_ADDR:-}"
STUN_ADDR="${STUN_ADDR:-stun.l.google.com:19302}"
ARI_PASSWORD="${ARI_PASSWORD:-$(head -c 24 /dev/urandom | base64 | tr -d '/+=' | head -c 24)}"

install -d -m 0755 "${ASTERISK_ETC}"

# Back up before overwriting
backup_conf "${ASTERISK_ETC}/ari.conf"
backup_conf "${ASTERISK_ETC}/http.conf"
backup_conf "${ASTERISK_ETC}/rtp.conf"

# ari.conf - matches skeleton [general] + [{{ari_user}}] type=user
cat > "${ASTERISK_ETC}/ari.conf" <<EOF
; managed by openivr system/steps/30-ari.sh
[general]
enabled = yes
pretty = yes
allowed_origins = *
websocket_write_timeout = 100
auth_realm = Asterisk REST Interface

[${ARI_USER}]
type = user
read_only = no
password = ${ARI_PASSWORD}
format = wav,ulaw,alaw
EOF
ok "wrote ${ASTERISK_ETC}/ari.conf (user ${ARI_USER})"

# http.conf - matches skeleton: bindaddr, bindport, prefix=/ari, enablestatic=no
cat > "${ASTERISK_ETC}/http.conf" <<EOF
; managed by openivr system/steps/30-ari.sh
[general]
enabled = yes
pretty = yes
allowed_origins = *
bindaddr = ${ARI_BIND}
bindport = ${ARI_PORT}
prefix = /ari
enablestatic = no
sessionlimit = 100
EOF
ok "wrote ${ASTERISK_ETC}/http.conf (${ARI_BIND}:${ARI_PORT})"

# rtp.conf - matches skeleton: icesupport=yes, stunaddr token, external_media_address
{
  printf ';\n; managed by openivr system/steps/30-ari.sh\n'
  printf '[general]\nrtpstart = %s\nrtpend = %s\n' "${RTP_START}" "${RTP_END}"
  [ -n "${MEDIA_ADDR}" ] && printf 'external_media_address = %s\n' "${MEDIA_ADDR}"
  [ -n "${MEDIA_ADDR}" ] && printf 'external_signaling_address = %s\n' "${MEDIA_ADDR}"
  printf 'icesupport = yes\n'
  printf 'stunaddr = %s\n' "${STUN_ADDR}"
} > "${ASTERISK_ETC}/rtp.conf"
ok "wrote ${ASTERISK_ETC}/rtp.conf (RTP ${RTP_START}-${RTP_END}, STUN ${STUN_ADDR})"

# NOTE: modules.conf is managed by the skeleton (autoload=yes + explicit loads).
# Do NOT write modules.conf.d/openivr.conf - it conflicts (autoload=no).

asterisk_reload
asterisk_running && ok "asterisk reloaded" || info "start Asterisk to activate these settings"

info "the IVR connects to: http://${ARI_BIND}:${ARI_PORT}/ari as ${ARI_USER}"
info "password was stored in system.json (mode 600) and is copied into config at runtime"

chmod 0600 "${STATE_DIR}/ari_password" 2>/dev/null || true
printf '%s\n' "${ARI_PASSWORD}" > "${STATE_DIR}/ari_password"
chmod 0600 "${STATE_DIR}/ari_password"

write_step "ari" "$(printf '{"ari": {"base_url": "http://%s:%s/ari", "username": "%s", "password": "%s"}, "ari_port": %s, "rtp": {"start": %s, "end": %s}}' \
  "${ARI_BIND}" "${ARI_PORT}" "${ARI_USER}" "${ARI_PASSWORD}" "${ARI_PORT}" "${RTP_START}" "${RTP_END}")"