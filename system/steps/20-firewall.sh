#!/usr/bin/env bash
# 20 - open the ports the IVR needs (SIP, RTP, ARI/HTTP, builder).
#      Skips cleanly when step 05 found no active firewall.
. "$(dirname "${BASH_SOURCE[0]}")/../lib/common.sh"

step "Firewall ports"

SIP_PORT="${SIP_PORT:-5060}"
RTP_START="${RTP_START:-10000}"
RTP_END="${RTP_END:-20000}"
# ARI REST + the pjsip transport-ws listener both live on http.conf bindport.
ARI_PORT="${ARI_PORT:-8088}"
PJSIP_WS_PORT="${PJSIP_WS_PORT:-${ARI_PORT}}"
BUILDER_PORT="${BUILDER_PORT:-8090}"

FIREWALL_ACTIVE="$(state_get preflight firewall_active true)"

log "SIP ${SIP_PORT}/udp, RTP ${RTP_START}-${RTP_END}/udp, ARI ${ARI_PORT}/tcp (pjsip ws ${PJSIP_WS_PORT}/tcp), builder ${BUILDER_PORT}/tcp"

if [ "${FIREWALL_ACTIVE}" != "true" ]; then
  info "step 05 reported no active firewall - skipping port rules"
  info "if you add one later, open: ${SIP_PORT}/udp, ${RTP_START}-${RTP_END}/udp, ${ARI_PORT}/tcp, ${BUILDER_PORT}/tcp"
  write_step "firewall" "$(printf '{"skipped": true, "sip_port": %s, "rtp_start": %s, "rtp_end": %s, "ari_port": %s, "pjsip_ws_port": %s, "builder_port": %s, "fw_managed": false}' \
    "${SIP_PORT}" "${RTP_START}" "${RTP_END}" "${ARI_PORT}" "${PJSIP_WS_PORT}" "${BUILDER_PORT}")"
  return 0 2>/dev/null || exit 0
fi

if have ufw; then
  log "configuring ufw"
  ufw allow "${SIP_PORT}/udp" >/dev/null 2>&1 || warn "ufw: SIP rule failed"
  ufw allow "${RTP_START}:${RTP_END}/udp" >/dev/null 2>&1 || warn "ufw: RTP rule failed"
  ufw allow "${ARI_PORT}/tcp" >/dev/null 2>&1 || warn "ufw: ARI rule failed"
  [ "${PJSIP_WS_PORT}" = "${ARI_PORT}" ] || ufw allow "${PJSIP_WS_PORT}/tcp" >/dev/null 2>&1 || true
  ufw allow "${BUILDER_PORT}/tcp" >/dev/null 2>&1 || warn "ufw: builder rule failed"
  ufw status || true
  ok "ufw rules applied"
  FW_MANAGED=true
elif have firewall-cmd; then
  log "configuring firewalld"
  firewall-cmd --permanent --add-port="${SIP_PORT}/udp" >/dev/null || warn "firewalld SIP"
  firewall-cmd --permanent --add-port="${RTP_START}-${RTP_END}/udp" >/dev/null || warn "firewalld RTP"
  firewall-cmd --permanent --add-port="${ARI_PORT}/tcp" >/dev/null || warn "firewalld ARI"
  [ "${PJSIP_WS_PORT}" = "${ARI_PORT}" ] || firewall-cmd --permanent --add-port="${PJSIP_WS_PORT}/tcp" >/dev/null || true
  firewall-cmd --permanent --add-port="${BUILDER_PORT}/tcp" >/dev/null || warn "firewalld builder"
  firewall-cmd --reload >/dev/null || warn "firewalld reload failed"
  ok "firewalld rules applied"
  FW_MANAGED=true
else
  warn "no ufw/firewall-cmd found - open SIP/RTP/HTTP ports yourself if you are behind a firewall"
  FW_MANAGED=false
fi

info "NAT note: if Asterisk sits behind NAT, forward ${SIP_PORT}/udp and ${RTP_START}-${RTP_END}/udp"
info "to the machine running Asterisk, and set an external_media_address in rtp.conf"

write_step "firewall" "$(printf '{"skipped": false, "sip_port": %s, "rtp_start": %s, "rtp_end": %s, "ari_port": %s, "pjsip_ws_port": %s, "builder_port": %s, "fw_managed": %s}' \
  "${SIP_PORT}" "${RTP_START}" "${RTP_END}" "${ARI_PORT}" "${PJSIP_WS_PORT}" "${BUILDER_PORT}" \
  "$(bool_json "${FW_MANAGED}")")"