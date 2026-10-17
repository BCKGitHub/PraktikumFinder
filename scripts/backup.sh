#!/bin/sh
# Consistent, encrypted SQLite backup.
# Usage: BACKUP_PASSPHRASE=... scripts/backup.sh [db_path] [backup_dir]
# In Docker: docker compose exec -e BACKUP_PASSPHRASE=... app sh scripts/backup.sh
set -eu
DB="${1:-data/praktikumfinder.db}"
OUT_DIR="${2:-data/backups}"
KEEP_DAYS="${KEEP_DAYS:-30}"
: "${BACKUP_PASSPHRASE:?Set BACKUP_PASSPHRASE to encrypt the backup}"
mkdir -p "$OUT_DIR"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
TMP="$OUT_DIR/.pf-$STAMP.db"
python3 -c "import sqlite3,sys; s=sqlite3.connect(sys.argv[1]); d=sqlite3.connect(sys.argv[2]); s.backup(d); d.close(); s.close()" "$DB" "$TMP"
python3 -c "import sqlite3,sys; r=sqlite3.connect(sys.argv[1]).execute('PRAGMA integrity_check').fetchone()[0]; sys.exit(0 if r=='ok' else 'integrity check failed: '+r)" "$TMP"
gzip -9 "$TMP"
openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -in "$TMP.gz" -out "$OUT_DIR/praktikumfinder-$STAMP.db.gz.enc" -pass env:BACKUP_PASSPHRASE
rm -f "$TMP.gz"
find "$OUT_DIR" -name 'praktikumfinder-*.db.gz.enc' -mtime +"$KEEP_DAYS" -delete
echo "Backup written: $OUT_DIR/praktikumfinder-$STAMP.db.gz.enc"
echo "Copy it off the server (e.g. to EU object storage) - a backup on the same disk is not a backup."
