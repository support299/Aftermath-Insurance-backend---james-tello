#!/usr/bin/env bash
# Clone live DB (clover_deal_insight) into staging (clover_deal_insight_staging).
# Reads connection details from backend/.env. Requires PostgreSQL 18+ client tools.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="$BACKEND_DIR/.env"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing $ENV_FILE" >&2
  exit 1
fi

# shellcheck disable=SC1090
source <(grep -E '^(DB_HOST|DB_PORT|DB_USER|DB_PASSWORD)=' "$ENV_FILE" | sed 's/^/export /')

LIVE_DB="clover_deal_insight"
STAGING_DB="clover_deal_insight_staging"
DUMP_FILE="$BACKEND_DIR/.staging_dump.sql"

PG_DUMP="${PG_DUMP:-$(command -v pg_dump || true)}"
PSQL="${PSQL:-$(command -v psql || true)}"

# Prefer PostgreSQL 18 client if installed via Homebrew (matches RDS 18.x).
if [[ -x /opt/homebrew/opt/postgresql@18/bin/pg_dump ]]; then
  PG_DUMP="/opt/homebrew/opt/postgresql@18/bin/pg_dump"
  PSQL="/opt/homebrew/opt/postgresql@18/bin/psql"
fi

if [[ -z "$PG_DUMP" || -z "$PSQL" ]]; then
  echo "pg_dump and psql are required (PostgreSQL 18+ client recommended)." >&2
  exit 1
fi

export PGPASSWORD="$DB_PASSWORD"
export PGHOST="$DB_HOST"
export PGUSER="$DB_USER"
export PGPORT="${DB_PORT:-5432}"

echo "Terminating connections to $STAGING_DB..."
"$PSQL" -d postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$STAGING_DB' AND pid <> pg_backend_pid();"

echo "Recreating $STAGING_DB..."
"$PSQL" -d postgres -c "DROP DATABASE IF EXISTS $STAGING_DB;"
"$PSQL" -d postgres -c "CREATE DATABASE $STAGING_DB OWNER $DB_USER;"

echo "Dumping $LIVE_DB..."
"$PG_DUMP" -d "$LIVE_DB" --no-owner --no-acl -F p -f "$DUMP_FILE"

echo "Restoring into $STAGING_DB..."
"$PSQL" -d "$STAGING_DB" -v ON_ERROR_STOP=1 -f "$DUMP_FILE"

echo "Cleaning up dump file..."
rm -f "$DUMP_FILE"

echo "Done. Staging DB refreshed from live."
