#!/usr/bin/env bash
set -euo pipefail

APP_DATA_DIR="${ENPRATO_DATA_DIR:-/home/ubuntu/enprato-data}"
DB_PATH="${ENPRATO_DATABASE_PATH:-$APP_DATA_DIR/enprato.sqlite3}"
MODE="${1:---dry-run}"

[[ "$MODE" == "--dry-run" ]] || { echo "Refusing to run: only --dry-run is supported by this preparation script."; exit 2; }
[[ -f "$DB_PATH" ]] || { echo "Database not found: $DB_PATH"; exit 1; }
[[ -d "$APP_DATA_DIR/sessions" ]] || { echo "Sessions directory not found: $APP_DATA_DIR/sessions"; exit 1; }
command -v sqlite3 >/dev/null || { echo "sqlite3 is required"; exit 1; }

echo "DRY-RUN: checking SQLite integrity and migration inventory"
sqlite3 "$DB_PATH" 'PRAGMA integrity_check;'
echo "Applied migrations:"
sqlite3 "$DB_PATH" 'SELECT version, applied_at FROM schema_migrations ORDER BY version;'
echo "Session folders: $(find "$APP_DATA_DIR/sessions" -mindepth 1 -maxdepth 1 -type d ! -name '_*' | wc -l)"
echo "Pending migrations in checkout:"
for file in /home/ubuntu/enprato/backend/migrations/*.sql; do
  version="$(basename "$file")"
  sqlite3 "$DB_PATH" "SELECT 1 FROM schema_migrations WHERE version='$version';" | grep -q 1 || echo "  $version"
done
echo "DRY-RUN complete. No migration or data mutation was performed."
