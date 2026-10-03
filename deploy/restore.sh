#!/usr/bin/env bash
# Put a backup from deploy/backup.sh back, replacing the current database and
# films:
#
#   bash deploy/restore.sh latest
#   bash deploy/restore.sh 20261003T031500Z
#   bash deploy/restore.sh bucket      # the machine is new: fetch from R2 first
set -euo pipefail
cd "$(dirname "$0")/.."

DEST=${BACKUP_DIR:-$HOME/backups}
DEST_FOR_DOCKER=${BACKUP_DIR_HOST:-$DEST}    # see backup.sh
COMPOSE=${COMPOSE:-docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml}
SUDO=${SUDO-sudo}
stamp=${1:?which backup? a name from $DEST/db (without .dump), "latest", or "bucket"}
if [ "$stamp" = bucket ]; then
  mkdir -p "$DEST"
  stamp=$($COMPOSE run --rm --no-deps -T -u "$(id -u):$(id -g)" -v "$DEST_FOR_DOCKER:/backups" api \
            python scripts/offsite_backup.py pull --backups /backups \
          | awk '/^pulled backup/ {print $3}')
  [ -n "$stamp" ] || { echo "nothing came back from the bucket"; exit 1; }
fi
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
