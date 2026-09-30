#!/usr/bin/env bash
# 30 - ARI + HTTP + RTP configuration (the part the IVR talks to)
. "$(dirname "${BASH_SOURCE[0]}")/../lib/common.sh"

step "ARI configuration"

ARI_USER="${ARI_USER:-openivr}"
ARI_PORT="${ARI_PORT:-8088}"
ARI_BIND="${ARI_BIND:-127.0.0.1}"
RTP_START="${RTP_START:-10000}"
RTP_END="${RTP_END:-20000}"
MEDIA_ADDR="${MEDIA_ADDR:-}"
ARI_PASSWORD="${ARI_PASSWORD:-$(head -c 24 /dev/urandom | base64 | tr -d '/+=' | head -c 24)}"

install -d -m 0755 "${ASTERISK_ETC}"

# Back up before overwriting: backup_conf() after the write would only ever
# capture the file we just wrote.
backup_conf "${ASTERISK_ETC}/ari.conf"
backup_conf "${ASTERISK_ETC}/http.conf"
backup_conf "${ASTERISK_ETC}/rtp.conf"

cat > "${ASTERISK_ETC}/ari.conf" <<EOF
; managed by openivr system/steps/30-ari.sh
[general]
enabled = yes
pretty = yes
allowed_origins = *

[user]
user = ${ARI_USER}
password = ${ARI_PASSWORD}
format = wav,ulaw,alaw
read_only = no
EOF
ok "wrote ${ASTERISK_ETC}/ari.conf (user ${ARI_USER})"

cat > "${ASTERISK_ETC}/http.conf" <<EOF
; managed by openivr system/steps/30-ari.sh
[general]
enabled = yes
bindaddr = ${ARI_BIND}
bindport = ${ARI_PORT}
prefix = /ari
enablestatic = no
sessionlimit = 100
EOF
ok "wrote ${ASTERISK_ETC}/http.conf (${ARI_BIND}:${ARI_PORT})"

{
  printf ';\n; managed by openivr system/steps/30-ari.sh\n'
  printf '[general]\nrtpstart = %s\nrtpend = %s\n' "${RTP_START}" "${RTP_END}"
  [ -n "${MEDIA_ADDR}" ] && printf 'external_media_address = %s\n' "${MEDIA_ADDR}"
  [ -n "${MEDIA_ADDR}" ] && printf 'external_signaling_address = %s\n' "${MEDIA_ADDR}"
  printf 'icesupport = no\n'
} > "${ASTERISK_ETC}/rtp.conf"
ok "wrote ${ASTERISK_ETC}/rtp.conf (RTP ${RTP_START}-${RTP_END})"

# ARI needs the websocket + http modules loaded before Stasis works.
mkdir -p "${ASTERISK_ETC}/modules.conf.d"
cat > "${ASTERISK_ETC}/modules.conf.d/openivr.conf" <<'EOF'
; managed by openivr
autoload = no
load = res_pjsip.so
load = res_http_websocket.so
load = res_ari.so
load = app_stasis.so
load = func_curl.so
EOF
ok "enabled modules (pjsip, http_websocket, ari, stasis)"

asterisk_reload
asterisk_running && ok "asterisk reloaded" || info "start Asterisk to activate these settings"

info "the IVR connects to: http://${ARI_BIND}:${ARI_PORT} as ${ARI_USER}"
info "password was stored in system.json (mode 600) and is copied into config at runtime"

chmod 0600 "${STATE_DIR}/ari_password" 2>/dev/null || true
printf '%s\n' "${ARI_PASSWORD}" > "${STATE_DIR}/ari_password"
chmod 0600 "${STATE_DIR}/ari_password"

write_step "ari" "$(printf '{"ari": {"base_url": "http://%s:%s", "username": "%s", "password": "%s"}, "ari_port": %s, "rtp": {"start": %s, "end": %s}}' \
  "${ARI_BIND}" "${ARI_PORT}" "${ARI_USER}" "${ARI_PASSWORD}" "${ARI_PORT}" "${RTP_START}" "${RTP_END}")"