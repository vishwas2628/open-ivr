#!/usr/bin/env bash
# 20 - open the ports the IVR needs (SIP, RTP, ARI/HTTP, builder)
. "$(dirname "${BASH_SOURCE[0]}")/../lib/common.sh"

step "Firewall ports"

SIP_PORT="${SIP_PORT:-5060}"
RTP_START="${RTP_START:-10000}"
RTP_END="${RTP_END:-20000}"
ARI_PORT="${ARI_PORT:-8088}"
BUILDER_PORT="${BUILDER_PORT:-8090}"

log "SIP ${SIP_PORT}/udp, RTP ${RTP_START}-${RTP_END}/udp, ARI ${ARI_PORT}/tcp, builder ${BUILDER_PORT}/tcp"

if have ufw; then
  log "configuring ufw"
  ufw allow "${SIP_PORT}/udp" >/dev/null 2>&1 || warn "ufw: SIP rule failed"
  ufw allow "${RTP_START}:${RTP_END}/udp" >/dev/null 2>&1 || warn "ufw: RTP rule failed"
  ufw allow "${ARI_PORT}/tcp" >/dev/null 2>&1 || warn "ufw: ARI rule failed"
  ufw allow "${BUILDER_PORT}/tcp" >/dev/null 2>&1 || warn "ufw: builder rule failed"
  ufw status || true
  ok "ufw rules applied"
elif have firewall-cmd; then
  log "configuring firewalld"
  firewall-cmd --permanent --add-port="${SIP_PORT}/udp" >/dev/null || warn "firewalld SIP"
  firewall-cmd --permanent --add-port="${RTP_START}-${RTP_END}/udp" >/dev/null || warn "firewalld RTP"
  firewall-cmd --permanent --add-port="${ARI_PORT}/tcp" >/dev/null || warn "firewalld ARI"
  firewall-cmd --permanent --add-port="${BUILDER_PORT}/tcp" >/dev/null || warn "firewalld builder"
  firewall-cmd --reload >/dev/null || warn "firewalld reload failed"
  ok "firewalld rules applied"
else
  warn "no ufw/firewalld found - open SIP/RTP/HTTP ports yourself if you are behind a firewall"
fi

info "NAT note: if Asterisk sits behind NAT, forward ${SIP_PORT}/udp and ${RTP_START}-${RTP_END}/udp"
info "to the machine running Asterisk, and set an external_media_address in rtp.conf"

write_step "firewall" "$(printf '{"sip_port": %s, "rtp_start": %s, "rtp_end": %s, "ari_port": %s, "builder_port": %s, "fw_managed": %s}' \
  "${SIP_PORT}" "${RTP_START}" "${RTP_END}" "${ARI_PORT}" "${BUILDER_PORT}" \
  "$(command -v ufw >/dev/null && echo true || echo false)")"