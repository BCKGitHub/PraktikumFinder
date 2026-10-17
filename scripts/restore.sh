#!/bin/sh
# Restore an encrypted backup. Stop the app first!
# Usage: BACKUP_PASSPHRASE=... scripts/restore.sh backup.db.gz.enc [target_db]
set -eu
SRC="$1"
DB="${2:-data/praktikumfinder.db}"
: "${BACKUP_PASSPHRASE:?Set BACKUP_PASSPHRASE}"
TMP="$DB.restore-tmp"
openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -in "$SRC" -pass env:BACKUP_PASSPHRASE | gunzip > "$TMP"
python3 -c "import sqlite3,sys; r=sqlite3.connect(sys.argv[1]).execute('PRAGMA integrity_check').fetchone()[0]; sys.exit(0 if r=='ok' else 'integrity check failed: '+r)" "$TMP"
[ -f "$DB" ] && cp "$DB" "$DB.before-restore-$(date -u +%Y%m%dT%H%M%SZ)"
rm -f "$DB-wal" "$DB-shm"
mv "$TMP" "$DB"
echo "Restored $SRC -> $DB. Start the app and check /health and the admin dashboard."
