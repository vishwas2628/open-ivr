#!/usr/bin/env bash
# 95 - install, enable and start the openivr systemd service.
#      Installs and starts the unit in one go.
. "$(dirname "${BASH_SOURCE[0]}")/../lib/common.sh"

step "systemd service installation"

require_root

SERVICE_SRC="${OPENIVR_ROOT}/systemd/openivr.service"
SERVICE_DEST="/etc/systemd/system/openivr.service"

[ -f "${SERVICE_SRC}" ] || die "service file not found: ${SERVICE_SRC}"

have systemctl || { warn "systemctl not available - start the IVR manually: ${OPENIVR_PYTHON} -m openivr run"; return 0 2>/dev/null || exit 0; }

# The unit is a template: fill the paths of this machine before installing it.
python3 - "${SERVICE_SRC}" "${SERVICE_DEST}" \
  "${OPENIVR_ROOT}" "${OPENIVR_USER}" "${OPENIVR_GROUP}" \
  "${OPENIVR_PYTHON}" "${OPENIVR_LOG_LEVEL:-INFO}" <<'PY'
import os, pathlib, sys

src, dest = sys.argv[1], sys.argv[2]
text = pathlib.Path(src).read_text(encoding="utf-8")
for token, value in zip(
    ("OPENIVR_ROOT", "OPENIVR_USER", "OPENIVR_GROUP", "OPENIVR_PYTHON", "OPENIVR_LOG_LEVEL"),
    sys.argv[3:],
):
    text = text.replace("{{" + token + "}}", value)
leftover = [l for l in text.splitlines() if "{{" in l]
if leftover:
    raise SystemExit(f"unfilled tokens in the unit file: {leftover}")
pathlib.Path(dest).write_text(text, encoding="utf-8")
os.chmod(dest, 0o644)
print(f"  ok rendered {dest}")
PY
ok "service file installed to ${SERVICE_DEST}"

systemctl daemon-reload
ok "systemd daemon reloaded"

systemctl enable openivr
ok "openivr service enabled"

systemctl restart openivr
ok "openivr service started"

sleep 2
systemctl status openivr --no-pager -l || warn "service may not be running - check logs"

journalctl -u openivr -n 30 --no-pager || true
ok "service logs shown above"

step "System status"
if systemctl is-active --quiet openivr; then
  ok "IVR service: RUNNING"
else
  warn "IVR service: NOT RUNNING"
fi
if systemctl is-active --quiet asterisk; then
  ok "Asterisk: RUNNING"
else
  warn "Asterisk: NOT RUNNING"
fi
BUILDER_PORT="${BUILDER_PORT:-$(state_get firewall builder_port 8090)}"
ARI_PORT="${ARI_PORT:-$(state_get firewall ari_port 8088)}"
echo ""
echo "  Builder  : http://localhost:${BUILDER_PORT}"
echo "  ARI      : http://localhost:${ARI_PORT}/ari"
echo "  Logs     : journalctl -u openivr -f"
echo ""

write_step "systemd" '{"service": "openivr", "enabled": true, "started": true}'