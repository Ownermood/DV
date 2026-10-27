#!/bin/bash
# Daily MongoDB backup of this bot only. Keeps the last 7 days in ~/dv-backups.
# Install once:  bash ~/dv/scripts/backup.sh --install   (adds a 3 AM cron job)
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$HOME/dv-backups"
KEEP_DAYS=7

if [ "${1:-}" = "--install" ]; then
    command -v crontab >/dev/null || { echo "crontab missing: run  apt install -y cron  then try again"; exit 1; }
    line="0 3 * * * bash $DIR/scripts/backup.sh >> $OUT/backup.log 2>&1"
    mkdir -p "$OUT"
    ( crontab -l 2>/dev/null | grep -v "dv/scripts/backup.sh" || true; echo "$line" ) | crontab -
    echo "Installed: $line"
    exit 0
fi

mkdir -p "$OUT"
DB="$(grep -E '^MONGO_DB=' "$DIR/.env" | cut -d= -f2- | tr -d '"' || true)"
DB="${DB:-downloader}"
FILE="$OUT/dv-$(date +%F_%H%M).archive.gz"
cd "$DIR"
docker compose exec -T mongo mongodump --quiet --archive --gzip --db "$DB" > "$FILE"
find "$OUT" -name 'dv-*.archive.gz' -mtime +$KEEP_DAYS -delete
echo "$(date '+%F %T') backup ok: $FILE ($(du -h "$FILE" | cut -f1))"
