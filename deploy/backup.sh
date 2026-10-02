#!/usr/bin/env bash
# Back up what can't be rebuilt: the database (accounts, versions, the queue)
# and the films with their versions. Run nightly from cron:
#
#   15 3 * * * cd ~/storygen && bash deploy/backup.sh >> ~/backups/backup.log 2>&1
#
# Films are kept as rsync snapshots hard-linked to the previous night, so a
# night costs only what changed — a full copy every night would fill the disk,
# since every version already holds its own copies of its files.
# Restore with deploy/restore.sh.
set -euo pipefail
cd "$(dirname "$0")/.."

DEST=${BACKUP_DIR:-$HOME/backups}
# Where Docker sees that folder. The same path, unless this script itself runs
# in a container talking to the host's Docker (a backup sidecar).
DEST_FOR_DOCKER=${BACKUP_DIR_HOST:-$DEST}
KEEP=${BACKUP_KEEP:-7}
COMPOSE=${COMPOSE:-docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml}
stamp=$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p "$DEST/db" "$DEST/data"
# A run that died half way leaves .part behind; it must not count as a backup.
rm -rf "$DEST"/db/*.part "$DEST"/data/*.part

# The database, consistent at one instant, in pg_restore's own format.
$COMPOSE exec -T db pg_dump -U storygen --format=custom storygen > "$DEST/db/$stamp.dump.part"
mv "$DEST/db/$stamp.dump.part" "$DEST/db/$stamp.dump"

# The films. The voice model is left out: it is re-downloaded and verified.
previous=$(find "$DEST/data" -mindepth 1 -maxdepth 1 -type d | sort | tail -n 1)
rsync -a --delete --exclude 'models/' ${previous:+--link-dest="$previous"} \
      data/ "$DEST/data/$stamp.part/"
mv "$DEST/data/$stamp.part" "$DEST/data/$stamp"

# Keep the newest $KEEP of each.
find "$DEST/db" -maxdepth 1 -name '*.dump' | sort | head -n -"$KEEP" | xargs -r rm -f
find "$DEST/data" -mindepth 1 -maxdepth 1 -type d | sort | head -n -"$KEEP" | xargs -r rm -rf

# Off the machine too, when .env names a bucket (BACKUP_BUCKET, with the same
# S3/R2 keys the app uses): every dump and a mirror of tonight's films, only
# the files that changed. Run in the app's image, which has the S3 client.
if grep -qs '^BACKUP_BUCKET=.' .env; then
  $COMPOSE run --rm --no-deps -T -u "$(id -u):$(id -g)" -v "$DEST_FOR_DOCKER:/backups" api \
    python scripts/offsite_backup.py push --backups /backups
fi

# du counts a hard-linked file once per call, so the last figure is what all
# the snapshots really take on disk together.
echo "$(date -u +%FT%TZ) backup $stamp: database $(du -h "$DEST/db/$stamp.dump" | cut -f1)," \
     "films $(du -sh "$DEST/data/$stamp" | cut -f1)," \
     "all $(find "$DEST/data" -mindepth 1 -maxdepth 1 -type d | wc -l) snapshots on disk $(du -sh "$DEST/data" | cut -f1)"
