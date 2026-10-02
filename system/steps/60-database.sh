#!/usr/bin/env bash
# 60 - optional PostgreSQL (for Realtime config / schema reservation)
# CDR is handled by Asterisk's native cdr.conf [csv] backend.
# The cdr table below is RESERVED / UNUSED by openivr - Asterisk CSV is the
# authoritative call log. This table exists for future Realtime config use.
. "$(dirname "${BASH_SOURCE[0]}")/../lib/common.sh"

step "Database (optional PostgreSQL - reserved schema)"

if [ "$(ask_yes_no "Install PostgreSQL for Realtime schema reservation?" "n")" != "y" ]; then
  info "PostgreSQL skipped - CSV CDR backend (cdr.conf) is the default"
  write_step "database" '{"cdr": {"enabled": true, "backend": "csv"}}'
  return 0 2>/dev/null || exit 0
fi

DB_HOST="${DB_HOST:-127.0.0.1}"
DB_PORT="${DB_PORT:-5432}"
DB_NAME="${DB_NAME:-ivrdb}"
DB_USER="${DB_USER:-ivr}"
DB_PASS="${DB_PASS:-$(head -c 18 /dev/urandom | base64 | tr -d '/+=' | head -c 18)}"

case "${PKG}" in
  apt)   apt-get update -qq; apt-get install -y postgresql postgresql-contrib ;;
  dnf)   dnf install -y postgresql-server postgresql-contrib || true ;;
esac

if have systemctl; then
  systemctl enable --now postgresql >/dev/null 2>&1 || warn "could not start postgresql"
fi

PG_BIN="$(command -v psql || true)"
if [ -z "${PG_BIN}" ] && [ -d /usr/lib/postgresql ]; then
  PG_BIN="/usr/lib/postgresql/$(ls /usr/lib/postgresql | sort -V | tail -1)/bin/psql"
fi
[ -x "${PG_BIN}" ] || die "psql not found - is PostgreSQL installed?"

sudo -u postgres "${PG_BIN}" <<SQL >/dev/null 2>&1 || warn "role/database creation reported an error"
CREATE ROLE ${DB_USER} LOGIN PASSWORD '${DB_PASS}';
CREATE DATABASE ${DB_NAME} OWNER ${DB_USER};
SQL

SCHEMA=$(cat <<'SQL'
-- RESERVED / UNUSED by openivr. Asterisk cdr.conf [csv] is the call log.
-- This table exists only for potential future Realtime config storage.
CREATE TABLE IF NOT EXISTS cdr (
    id          bigserial PRIMARY KEY,
    call_id     text NOT NULL,
    channel_id  text NOT NULL DEFAULT '',
    caller      text NOT NULL DEFAULT 'unknown',
    started_at  timestamptz,
    ended_at    timestamptz,
    duration    numeric(10,3),
    outcome     text,
    cause       text,
    digits      text,
    variables   jsonb,
    events      jsonb
);
CREATE INDEX IF NOT EXISTS cdr_started_idx ON cdr (started_at);
CREATE INDEX IF NOT EXISTS cdr_call_idx ON cdr (call_id);
SQL
)
PGPASSWORD="${DB_PASS}" "${PG_BIN}" -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${DB_NAME}" \
  -c "${SCHEMA}" >/dev/null 2>&1 || warn "schema creation failed (check permissions)"

ok "PostgreSQL ${DB_NAME} ready on ${DB_HOST}:${DB_PORT} (user ${DB_USER})"
info "cdr table created (RESERVED - openivr uses Asterisk cdr.conf CSV backend)"

# The shape must match config.CdrCfg: cdr.postgres.{host,port,dbname,user,password}
# cdr.backend stays "csv" - Postgres is not used for CDR writing.
write_step "database" "$(printf '{"cdr": {"enabled": true, "backend": "csv", "postgres": {"host": "%s", "port": %s, "dbname": "%s", "user": "%s", "password": "%s"}}}' \
  "${DB_HOST}" "${DB_PORT}" "${DB_NAME}" "${DB_USER}" "${DB_PASS}")"