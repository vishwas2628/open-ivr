#!/usr/bin/env bash
# openivr - shared helpers for the system installer
# shellcheck shell=bash

[ -n "${OPENIVR_COMMON_SOURCED:-}" ] && return 0
OPENIVR_COMMON_SOURCED=1

set -euo pipefail

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  C_RESET=$'\033[0m'; C_RED=$'\033[31m'; C_GREEN=$'\033[32m'
  C_YELLOW=$'\033[33m'; C_BLUE=$'\033[34m'; C_BOLD=$'\033[1m'
else
  C_RESET=""; C_RED=""; C_GREEN=""; C_YELLOW=""; C_BLUE=""; C_BOLD=""
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SYSTEM_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
OPENIVR_ROOT="$(cd "${SYSTEM_DIR}/.." && pwd)"
STATE_DIR="${OPENIVR_STATE_DIR:-${OPENIVR_ROOT}/system/.state}"
ASTERISK_ETC="${ASTERISK_ETC:-/etc/asterisk}"
ASTERISK_LIB="${ASTERISK_LIB:-/var/lib/asterisk}"
ASTERISK_SPOOL="${ASTERISK_SPOOL:-/var/spool/asterisk}"
OPENIVR_USER="${OPENIVR_USER:-asterisk}"
OPENIVR_GROUP="${OPENIVR_GROUP:-asterisk}"

# Interpreter used for the openivr CLI (.venv when present, else system python3)
if [ -z "${OPENIVR_PYTHON:-}" ]; then
  if [ -x "${OPENIVR_ROOT}/.venv/bin/python" ]; then
    OPENIVR_PYTHON="${OPENIVR_ROOT}/.venv/bin/python"
  else
    OPENIVR_PYTHON="$(command -v python3 || true)"
  fi
fi
export OPENIVR_PYTHON

mkdir -p "${STATE_DIR}"

log()   { printf '%s\n' "${C_BLUE}==>${C_RESET} $*"; }
ok()    { printf '%s\n' "${C_GREEN}  ok${C_RESET} $*"; }
info()  { printf '%s\n' "     $*"; }
warn()  { printf '%s\n' "${C_YELLOW}  !!${C_RESET} $*" >&2; }
err()   { printf '%s\n' "${C_RED}  xx${C_RESET} $*" >&2; }
step()  { printf '\n%s\n' "${C_BOLD}${C_BLUE}###${C_RESET} $*"; }
die()   { err "$*"; exit 1; }

require_root() {
  if [ "$(id -u)" -ne 0 ]; then
    die "this step needs root; re-run with sudo"
  fi
}

have() { command -v "$1" >/dev/null 2>&1; }

ask() {
  local prompt="$1" default="${2:-}" answer
  if [ -n "${NONINTERACTIVE:-}" ]; then
    printf '%s\n' "${default}"
    return 0
  fi
  if [ -n "${default}" ]; then
    read -r -p "${prompt} [${default}]: " answer || true
  else
    read -r -p "${prompt}: " answer || true
  fi
  printf '%s\n' "${answer:-${default}}"
}

ask_yes_no() {
  local prompt="$1" default="${2:-y}" answer
  if [ -n "${NONINTERACTIVE:-}" ]; then
    printf '%s\n' "${default}"
    return 0
  fi
  read -r -p "${prompt} [${default}]: " answer || true
  answer="${answer:-${default}}"
  case "${answer,,}" in
    y|yes) printf 'y\n' ;;
    *)     printf 'n\n' ;;
  esac
}

# json_escape <string>
json_escape() {
  printf '%s' "$1" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))'
}

# bool_json <answer>  (y|n|Y|N|true|false) -> true|false
bool_json() {
  case "${1,,}" in
    y|yes|true|1) printf 'true' ;;
    *)            printf 'false' ;;
  esac
}

# state_get <fragment> <key> [default]
# Reads one key out of a write_step fragment (falls back to $3).
state_get() {
  local fragment="$1" key="$2" default="${3:-}"
  local file="${STATE_DIR}/${fragment}.json"
  [ -f "${file}" ] || { printf '%s\n' "${default}"; return 0; }
  python3 - "${file}" "${key}" "${default}" <<'PY'
import json, sys
path, key, default = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    data = json.load(open(path)).get("data") or {}
except Exception:
    data = {}
value = data.get(key, default)
if isinstance(value, bool):
    value = "true" if value else "false"
print(value)
PY
}

# config_set <key> <value>
# Writes a single value into config.yaml (mode 0600) - the YAML-aware way for
# installer steps to store secrets (30-ari.sh, 60-database.sh, 80-smtp.sh).
config_set() {
  local key="$1" value="$2"
  if ! have "${OPENIVR_PYTHON}"; then
    warn "no python interpreter (${OPENIVR_PYTHON}); ${key} only stored in system.json"
    return 0
  fi
  ( cd "${OPENIVR_ROOT}" && PYTHONPATH="${OPENIVR_ROOT}" "${OPENIVR_PYTHON}" -m openivr \
      config set "${key}=${value}" >/dev/null ) \
    || die "could not write ${key} into ${OPENIVR_ROOT}/config.yaml"
  ok "config.yaml: ${key} stored (mode 0600)"
}

# write_step <name> <json-body>
# Persists a JSON fragment for the orchestrator to merge into system.json.
write_step() {
  local name="$1" body="$2"
  printf '{ "step": "%s", "data": %s }\n' "${name}" "${body}" > "${STATE_DIR}/${name}.json"
  ok "recorded ${name}"
}

# step_exists <name>
step_exists() { [ -f "${STATE_DIR}/$1.json" ]; }

# merge_steps <output.json>
# Merges every state fragment into one flat object (later steps win).
merge_steps() {
  local output="$1"
  python3 - "${STATE_DIR}" "${output}" <<'PY'
import json, pathlib, sys

state = pathlib.Path(sys.argv[1])
out = pathlib.Path(sys.argv[2])
merged = {}
for fragment in sorted(state.glob("*.json")):
    try:
        data = json.loads(fragment.read_text())
    except Exception as exc:
        print(f"  !! unreadable {fragment.name}: {exc}", file=sys.stderr)
        continue
    merged.update(data.get("data") or {})
out.write_text(json.dumps(merged, indent=2, sort_keys=True) + "\n")
print(f"  ok merged {len(merged)} keys into {out}")
PY
}

# find_latest_asterisk_conf <basename>
# Prints the file in $ASTERISK_ETC that already defines [basename], if any.
find_conf() {
  local base="$1" file
  for file in "${ASTERISK_ETC}"/*.conf; do
    [ -f "${file}" ] || continue
    if grep -qE "^\[${base}\]" "${file}" 2>/dev/null; then
      printf '%s\n' "${file}"
      return 0
    fi
  done
  return 1
}

# backup_conf <file>
backup_conf() {
  local file="$1"
  [ -f "${file}" ] || return 0
  cp -a "${file}" "${file}.openivr.bak"
}

# replace_conf_section <file> <section> <heredoc-reader>
# Rewrites [section] .. next [section] in a conf file.
replace_conf_section() {
  local file="$1" section="$2"
  python3 - "${file}" "${section}" <<'PY'
import re, sys, pathlib

path = pathlib.Path(sys.argv[1])
section = sys.argv[2]
body = sys.stdin.read()

if not path.exists():
    path.write_text(body)
    raise SystemExit(0)

text = path.read_text()
pattern = re.compile(rf"^\[{re.escape(section)}\][^\[]*", re.MULTILINE | re.DOTALL)
if pattern.search(text):
    text = pattern.sub(lambda _: body, text, count=1)
else:
    text = text.rstrip("\n") + "\n\n" + body
path.write_text(text)
PY
}

asterisk_reload() {
  if have asteriskctl; then
    asteriskctl reload >/dev/null 2>&1 || true
  fi
}

asterisk_running() {
  pgrep -x asterisk >/dev/null 2>&1
}