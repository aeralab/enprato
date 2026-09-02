#!/usr/bin/env bash
set -euo pipefail

APP_DATA_DIR="${ENPRATO_DATA_DIR:-/home/ubuntu/enprato-data}"
DB_PATH="${ENPRATO_DATABASE_PATH:-$APP_DATA_DIR/enprato.sqlite3}"
BACKUP_DIR="${ENPRATO_BACKUP_DIR:-/var/backups/enprato}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$BACKUP_DIR"

# SQLite online backup is consistent while the service is running.
sqlite3 "$DB_PATH" ".backup '$BACKUP_DIR/enprato-$STAMP.sqlite3'"
tar -czf "$BACKUP_DIR/sessions-$STAMP.tar.gz" -C "$APP_DATA_DIR" sessions
