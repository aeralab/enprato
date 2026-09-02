#!/usr/bin/env bash
set -euo pipefail

BACKUP_DB="${1:-}"
DB_PATH="${ENPRATO_DATABASE_PATH:-/home/ubuntu/enprato-data/enprato.sqlite3}"
if [[ -z "$BACKUP_DB" ]]; then echo "Usage: $0 /path/to/verified-backup.sqlite3 --confirm"; exit 2; fi
if [[ "${2:-}" != "--confirm" ]]; then echo "DRY-RUN only. Add --confirm only after stopping the service and verifying the backup."; sqlite3 "$BACKUP_DB" 'PRAGMA integrity_check;' 2>/dev/null || true; exit 0; fi
[[ -f "$BACKUP_DB" && -f "$DB_PATH" ]] || { echo "Backup or target database missing"; exit 1; }
[[ "${ENPRATO_ROLLBACK_APPROVED:-}" == "YES" ]] || { echo "Set ENPRATO_ROLLBACK_APPROVED=YES after a human review."; exit 2; }
cp --preserve=mode,timestamps "$DB_PATH" "$DB_PATH.before-rollback"
cp --preserve=mode,timestamps "$BACKUP_DB" "$DB_PATH"
echo "Rollback completed; verify service and course data before accepting traffic."
