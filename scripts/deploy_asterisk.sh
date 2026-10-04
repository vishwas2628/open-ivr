#!/usr/bin/env bash
# Copy staged Asterisk config files into the Asterisk conf directory.
# Called by openivr/builder/publish.py (and by system/deploy.sh) - it never
# calls sudo. The installer granted the asterisk group write access once, so a
# member of the asterisk group can deploy unprivileged.
#
#   deploy_asterisk.sh <stage_dir> [dest_dir]
set -euo pipefail

STAGE="${1:?Usage: deploy_asterisk.sh <stage_dir> [dest_dir]}"
DEST="${2:-/etc/asterisk}"
ASTERISK_USER="${ASTERISK_USER:-asterisk}"
ASTERISK_GROUP="${ASTERISK_GROUP:-asterisk}"

[ -d "${STAGE}" ] || { echo "deploy: staging dir not found: ${STAGE}" >&2; exit 1; }
[ -d "${DEST}" ] || { echo "deploy: dest dir not found: ${DEST}" >&2; exit 1; }

count=0
while IFS= read -r -d '' src; do
  rel="${src#"${STAGE}"/}"
  dst="${DEST}/${rel}"
  install -D -m 0640 "${src}" "${dst}"

  # Best effort: match the ownership of the directory we write into so the
  # running asterisk user can always read the config back.
  if getent group "${ASTERISK_GROUP}" >/dev/null 2>&1; then
    chgrp "${ASTERISK_GROUP}" "${dst}" 2>/dev/null || true
  fi
  if [ "$(id -u)" -eq 0 ] && id -u "${ASTERISK_USER}" >/dev/null 2>&1; then
    chown "${ASTERISK_USER}:${ASTERISK_GROUP}" "${dst}" 2>/dev/null || true
  fi
  count=$((count + 1))
done < <(find "${STAGE}" -type f -print0)

echo "deploy: copied ${count} file(s) to ${DEST}"