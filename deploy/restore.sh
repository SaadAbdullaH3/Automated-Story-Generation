#!/usr/bin/env bash
# Put a backup from deploy/backup.sh back, replacing the current database and
# films:
#
#   bash deploy/restore.sh latest
#   bash deploy/restore.sh 20261003T031500Z
set -euo pipefail
cd "$(dirname "$0")/.."

DEST=${BACKUP_DIR:-$HOME/backups}
COMPOSE=${COMPOSE:-docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml}
SUDO=${SUDO-sudo}
stamp=${1:?which backup? a name from $DEST/db (without .dump), or "latest"}
if [ "$stamp" = latest ]; then
  stamp=$(find "$DEST/db" -maxdepth 1 -name '*.dump' | sort | tail -n 1 | xargs -r basename | sed 's/\.dump$//')
fi
db="$DEST/db/$stamp.dump"
films="$DEST/data/$stamp"
[ -f "$db" ] || { echo "no database backup $db"; exit 1; }
[ -d "$films" ] || { echo "no film backup $films"; exit 1; }

echo "Restoring $stamp over the current database and films."
# Nothing may write while the old state is replaced underneath it.
$COMPOSE stop api worker
$COMPOSE exec -T db pg_restore -U storygen -d storygen --clean --if-exists --no-owner < "$db"
# Back to the containers' user (uid 10001): the backup was taken as you.
$SUDO rsync -a --delete --exclude 'models/' --chown=10001:10001 "$films/" data/
$COMPOSE start api worker
echo "Restored $stamp."
