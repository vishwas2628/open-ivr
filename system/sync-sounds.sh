#!/usr/bin/env bash
# Sync data/sounds into Asterisk's sounds folder so sound:custom/<name> resolves.
# Called by run_ivr.sh (step 3) - safe to run repeatedly.
. "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

ASTERISK_SOUNDS="${ASTERISK_SOUNDS:-${ASTERISK_LIB}/sounds/custom}"
SOURCE="${OPENIVR_ROOT}/data/sounds"
MODE="${1:-copy}"

step "Syncing prompts to ${ASTERISK_SOUNDS}"

if [ ! -d "${SOURCE}" ]; then
  die "no sounds directory at ${SOURCE}"
fi

count=$(find "${SOURCE}" -maxdepth 1 -type f \( -name '*.wav' -o -name '*.sln' -o -name '*.gsm' -o -name '*.ulaw' -o -name '*.alaw' -o -name '*.mp3' -o -name '*.ogg' -o -name '*.oga' \) | wc -l)
if [ "${count}" -eq 0 ]; then
  warn "no audio files in ${SOURCE}"
  info "generate test prompts: python3 scripts/make_sounds.py --out data/sounds"
else
  ok "${count} prompt file(s) to sync"
fi

if [ "${MODE}" = "symlink" ]; then
  mkdir -p "${ASTERISK_SOUNDS}"
  ln -sfn "${SOURCE}" "${ASTERISK_SOUNDS}/openivr-link" 2>/dev/null || true
  ok "symlinked ${ASTERISK_SOUNDS}/openivr-link -> ${SOURCE}"
  echo "${ASTERISK_SOUNDS}/openivr-link"
  return 0 2>/dev/null || exit 0
fi

mkdir -p "${ASTERISK_SOUNDS}"
copied=0
while IFS= read -r file; do
  dest="${ASTERISK_SOUNDS}/$(basename "${file}")"
  if [ -f "${dest}" ] && cmp -s "${file}" "${dest}"; then
    continue
  fi
  if install -m 0644 -o "${OPENIVR_USER}" -g "${OPENIVR_GROUP}" "${file}" "${dest}" 2>/dev/null; then
    copied=$((copied + 1))
  else
    install -m 0644 "${file}" "${dest}" 2>/dev/null && copied=$((copied + 1)) \
      || warn "could not install $(basename "${file}")"
  fi
done < <(find "${SOURCE}" -maxdepth 1 -type f \( -name '*.wav' -o -name '*.sln' -o -name '*.gsm' -o -name '*.ulaw' -o -name '*.alaw' -o -name '*.mp3' -o -name '*.ogg' -o -name '*.oga' \) -print)

ok "${copied} file(s) updated, $((count - copied)) already current"
asterisk_reload
echo "${ASTERISK_SOUNDS}"