#!/usr/bin/env bash
# Convert any audio file to formats Asterisk plays natively.
#
#   convert_sound.sh <input_file> <output_basename> [output_dir]
#
# Produces <output_dir>/<output_basename>.ulaw (8 kHz mono PCM µ-law, what
# Asterisk prefers for prompts) and the matching .wav. Called by
# openivr/media.py during a builder upload; safe to run by hand too.
set -euo pipefail

INPUT="${1:?Usage: convert_sound.sh <input_file> <output_basename> [output_dir]}"
BASENAME="${2:?Usage: convert_sound.sh <input_file> <output_basename> [output_dir]}"
OUTDIR="${3:-$(dirname "${INPUT}")}"

[ -f "${INPUT}" ] || { echo "convert_sound: no such file: ${INPUT}" >&2; exit 1; }
command -v ffmpeg >/dev/null 2>&1 || {
  echo "convert_sound: ffmpeg is not installed" >&2
  exit 127
}

mkdir -p "${OUTDIR}"
STEM="${OUTDIR}/${BASENAME}"

# The muxer is mulaw (ffmpeg's name for PCM mu-law); the file keeps the .ulaw suffix.
ffmpeg -y -nostdin -loglevel error -i "${INPUT}" \
  -ar 8000 -ac 1 -acodec pcm_mulaw -f mulaw "${STEM}.ulaw"

ffmpeg -y -nostdin -loglevel error -i "${INPUT}" \
  -ar 8000 -ac 1 -acodec pcm_s16le -f wav "${STEM}.wav"

echo "converted: ${STEM}.ulaw and ${STEM}.wav"