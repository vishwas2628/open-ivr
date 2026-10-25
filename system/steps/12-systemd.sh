#!/usr/bin/env bash
# 12 - install, enable and start the openivr systemd service
. "$(dirname "${BASH_SOURCE[0]}")/../lib/common.sh"

step "systemd service installation"

require_root

SERVICE_SRC="${OPENIVR_ROOT}/systemd/openivr.service"
SERVICE_DEST="/etc/systemd/system/openivr.service"

[ -f "${SERVICE_SRC}" ] || die "service template not found: ${SERVICE_SRC}"

have systemctl || { warn "systemctl not available - start OpenIVR manually: ${OPENIVR_PYTHON} -m openivr.core"; return 0 2>/dev/null || exit 0; }

# Render unit file template
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
    raise SystemExit(f"unfilled tokens in unit file: {leftover}")
pathlib.Path(dest).write_text(text, encoding="utf-8")
os.chmod(dest, 0o644)
print(f"  ok rendered {dest}")
PY
ok "service file installed to ${SERVICE_DEST}"

systemctl daemon-reload
ok "systemd daemon reloaded"

systemctl enable openivr >/dev/null 2>&1 || warn "could not enable openivr service"
ok "openivr service enabled"

systemctl restart openivr >/dev/null 2>&1 || systemctl start openivr >/dev/null 2>&1 || warn "could not start openivr service"
ok "openivr service started"

sleep 2

step "System status"
if systemctl is-active --quiet openivr 2>/dev/null; then
  ok "OpenIVR service: RUNNING"
else
  warn "OpenIVR service: NOT RUNNING (run 'journalctl -u openivr -n 30' to inspect)"
fi
if systemctl is-active --quiet asterisk 2>/dev/null || asterisk_running; then
  ok "Asterisk PBX: RUNNING"
else
  warn "Asterisk PBX: NOT RUNNING"
fi

BUILDER_PORT="${BUILDER_PORT:-$(state_get firewall builder_port 8090)}"
ARI_PORT="${ARI_PORT:-$(state_get ari ari_port 8088)}"

echo ""
echo "=========================================================="
echo "  Setup Complete! Next steps:"
echo "  1. Web Builder UI : http://localhost:${BUILDER_PORT}"
echo "  2. Asterisk ARI   : http://localhost:${ARI_PORT}/ari"
echo "  3. OpenIVR Logs   : journalctl -u openivr -f"
echo "=========================================================="
echo ""

write_step "systemd" '{"service": "openivr", "enabled": true, "started": true}'
