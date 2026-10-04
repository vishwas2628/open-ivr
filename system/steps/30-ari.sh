#!/usr/bin/env bash
# 30 - ARI + HTTP + RTP configuration.
#      Renders the skeleton templates (so ari.conf keeps its two Stasis apps)
#      and stores the generated password in config.yaml *and* system.json.
. "$(dirname "${BASH_SOURCE[0]}")/../lib/common.sh"

step "ARI configuration"

ARI_USER="${ARI_USER:-openivr}"
# Static: must equal config.yaml app.stasis_app and extensions.conf [globals].
STASIS_APP="${STASIS_APP:-openivr}"
ARI_ADMIN_APP="${ARI_ADMIN_APP:-openivr-admin}"
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

# render <skeleton> <dest>  - fills {{token}} pairs from KEY=VALUE arguments.
# Options that were left empty are dropped: Asterisk rejects "key = " lines.
render() {
  local skeleton="$1" dest="$2"; shift 2
  [ -f "${skeleton}" ] || die "skeleton missing: ${skeleton}"
  python3 - "${skeleton}" "${dest}" "$@" <<'PY'
import pathlib, re, sys

skeleton, dest = sys.argv[1], sys.argv[2]
lines = []
for line in pathlib.Path(skeleton).read_text(encoding="utf-8").splitlines(keepends=True):
    if not line.lstrip().startswith((";", "#")):
        for pair in sys.argv[3:]:
            key, _, value = pair.partition("=")
            line = line.replace("{{" + key + "}}", value)
    lines.append(line)
kept = [
    line
    for line in lines
    if not re.match(r"^[A-Za-z0-9_]+\s*=\s*$", line.rstrip("\n"))
]
pathlib.Path(dest).write_text("".join(kept), encoding="utf-8")
PY
  chown "${OPENIVR_USER}:${OPENIVR_GROUP}" "${dest}" 2>/dev/null || true
  chmod 0640 "${dest}" 2>/dev/null || true
}

# ari.conf - matches the skeleton: [general] + [{{ari_user}}] type=user with
# stasis_app = <ivr app>,<admin app>
render "${OPENIVR_ROOT}/system/asterisk/ari.conf" "${ASTERISK_ETC}/ari.conf" \
  "ari_user=${ARI_USER}" "ari_pass=${ARI_PASSWORD}" \
  "stasis_app=${STASIS_APP},${ARI_ADMIN_APP}"
ok "wrote ${ASTERISK_ETC}/ari.conf (user ${ARI_USER}, stasis apps ${STASIS_APP},${ARI_ADMIN_APP})"

# http.conf - bindaddr, bindport, prefix=/ari, enablestatic=no
render "${OPENIVR_ROOT}/system/asterisk/http.conf" "${ASTERISK_ETC}/http.conf" \
  "ari_bind=${ARI_BIND}" "http_port=${ARI_PORT}"
ok "wrote ${ASTERISK_ETC}/http.conf (${ARI_BIND}:${ARI_PORT})"

# rtp.conf - icesupport=yes, stunaddr token, external_media_address
render "${OPENIVR_ROOT}/system/asterisk/rtp.conf" "${ASTERISK_ETC}/rtp.conf" \
  "rtpstart=${RTP_START}" "rtpend=${RTP_END}" "stunaddr=${STUN_ADDR}" \
  "external_media_address=${MEDIA_ADDR}" "ice_acl=rtp-ice"
ok "wrote ${ASTERISK_ETC}/rtp.conf (RTP ${RTP_START}-${RTP_END}, STUN ${STUN_ADDR})"

# NOTE: modules.conf is managed by the skeleton (autoload=yes + explicit loads).
# Do NOT write modules.conf.d/openivr.conf - it conflicts (autoload=no).

# --- secrets: config.yaml (0600) is the single source of truth --------------
config_set "ari.username" "${ARI_USER}"
config_set "ari.password" "${ARI_PASSWORD}"
config_set "ari.base_url" "http://${ARI_BIND}:${ARI_PORT}/ari"
config_set "app.stasis_app" "${STASIS_APP}"

umask 077
printf '%s\n' "${ARI_PASSWORD}" > "${STATE_DIR}/ari_password"
chmod 0600 "${STATE_DIR}/ari_password"

asterisk_reload
asterisk_running && ok "asterisk reloaded" || info "start Asterisk to activate these settings"

info "the IVR connects to: http://${ARI_BIND}:${ARI_PORT}/ari as ${ARI_USER}"
info "password stored in config.yaml (mode 600) and merged into system.json"

write_step "ari" "$(printf '{"ari": {"base_url": "http://%s:%s/ari", "username": "%s", "password": "%s"}, "ari_port": %s, "stasis_app": "%s", "stasis_admin_app": "%s", "rtp": {"start": %s, "end": %s}}' \
  "${ARI_BIND}" "${ARI_PORT}" "${ARI_USER}" "${ARI_PASSWORD}" "${ARI_PORT}" "${STASIS_APP}" \
  "${ARI_ADMIN_APP}" "${RTP_START}" "${RTP_END}")"