"""Copy the nightly backups off the machine, and bring them back.

    python scripts/offsite_backup.py push --backups /backups   # backup.sh runs this
    python scripts/offsite_backup.py pull --backups /backups   # on a new machine, before restore.sh

Any S3-compatible bucket works (Cloudflare R2's free 10 GB is the intended
one), with the same settings the app uses for assets: S3_ENDPOINT_URL,
AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, plus BACKUP_BUCKET.

Under <BACKUP_PREFIX>/ (default "backups") the bucket holds:
    db/<stamp>.dump    every database dump, the newest --keep of them
    data/...           a mirror of the newest film snapshot
    LATEST             the stamp that mirror and its matching dump belong to

Only what changed is uploaded: a file whose size and MD5 match the bucket's
copy is left alone (uploads stay single-part below 64 MB so that the ETag *is*
the MD5), and a file gone from the snapshot is deleted from the mirror.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path
from typing import Dict, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SINGLE_PART_LIMIT = 64 * 1024 * 1024


def _client():
    from shared.assets import S3Assets
    return S3Assets(os.environ["BACKUP_BUCKET"]).client()


def _transfer_config():
    from boto3.s3.transfer import TransferConfig
    return TransferConfig(multipart_threshold=SINGLE_PART_LIMIT)


def _md5(path: Path) -> str:
    digest = hashlib.md5()  # noqa: S324 — compared with S3's ETag, not used for security
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _remote(s3, bucket: str, prefix: str) -> Dict[str, Tuple[int, str]]:
    """key -> (size, etag) for everything under prefix."""
    out: Dict[str, Tuple[int, str]] = {}
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            out[obj["Key"]] = (obj["Size"], obj["ETag"].strip('"'))
    return out


def _same(path: Path, remote: Tuple[int, str] | None) -> bool:
    if remote is None or path.stat().st_size != remote[0]:
        return False
    # Past the single-part limit the ETag isn't an MD5; size has to do.
    return path.stat().st_size >= SINGLE_PART_LIMIT or _md5(path) == remote[1]


def _snapshots(backups: Path):
    return sorted(p for p in (backups / "data").iterdir()
                  if p.is_dir() and not p.name.endswith(".part"))


def push(backups: Path, bucket: str, prefix: str, keep: int) -> Dict[str, int]:
    s3, config = _client(), _transfer_config()
    stats = {"uploaded": 0, "uploaded_bytes": 0, "unchanged": 0, "deleted": 0}

    # Every dump not up there yet, then the oldest pruned past `keep`.
    dumps = _remote(s3, bucket, f"{prefix}/db/")
    for dump in sorted((backups / "db").glob("*.dump")):
        key = f"{prefix}/db/{dump.name}"
        if not _same(dump, dumps.get(key)):
            s3.upload_file(str(dump), bucket, key, Config=config)
            stats["uploaded"] += 1
            stats["uploaded_bytes"] += dump.stat().st_size
            dumps[key] = (dump.stat().st_size, "")
    for key in sorted(dumps)[:-keep] if keep > 0 else []:
        s3.delete_object(Bucket=bucket, Key=key)
        stats["deleted"] += 1

    # The newest film snapshot, mirrored: changed files up, vanished ones gone.
    snapshots = _snapshots(backups)
    if not snapshots:
        raise SystemExit(f"no film snapshot under {backups / 'data'}")
    newest = snapshots[-1]
    remote = _remote(s3, bucket, f"{prefix}/data/")
    wanted = set()
    for path in sorted(p for p in newest.rglob("*") if p.is_file()):
        key = f"{prefix}/data/{path.relative_to(newest).as_posix()}"
        wanted.add(key)
        if _same(path, remote.get(key)):
            stats["unchanged"] += 1
            continue
        s3.upload_file(str(path), bucket, key, Config=config)
        stats["uploaded"] += 1
        stats["uploaded_bytes"] += path.stat().st_size
    stale = sorted(set(remote) - wanted)
    for start in range(0, len(stale), 1000):        # DeleteObjects takes 1000 at most
        s3.delete_objects(Bucket=bucket, Delete={
            "Objects": [{"Key": k} for k in stale[start:start + 1000]], "Quiet": True})
    stats["deleted"] += len(stale)

    # Written last: a push cut short leaves LATEST naming the previous, whole one.
    s3.put_object(Bucket=bucket, Key=f"{prefix}/LATEST", Body=newest.name.encode())
    return stats


def pull(backups: Path, bucket: str, prefix: str) -> str:
    """Download the newest backup into the local layout restore.sh reads; returns its stamp."""
    s3 = _client()
    stamp = s3.get_object(Bucket=bucket, Key=f"{prefix}/LATEST")["Body"].read().decode().strip()
    (backups / "db").mkdir(parents=True, exist_ok=True)
    s3.download_file(bucket, f"{prefix}/db/{stamp}.dump", str(backups / "db" / f"{stamp}.dump"))
    target = backups / "data" / stamp
    for key, size_etag in _remote(s3, bucket, f"{prefix}/data/").items():
        dest = target / key[len(f"{prefix}/data/"):]
        if dest.exists() and _same(dest, size_etag):
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        s3.download_file(bucket, key, str(dest))
    return stamp


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=["push", "pull"])
    parser.add_argument("--backups", type=Path, required=True)
    parser.add_argument("--keep", type=int, default=int(os.getenv("BACKUP_KEEP_REMOTE", "14")))
    args = parser.parse_args(argv)
    bucket = os.getenv("BACKUP_BUCKET", "").strip()
    if not bucket:
        print("BACKUP_BUCKET is not set — nothing to do")
        return 1
    prefix = os.getenv("BACKUP_PREFIX", "backups").strip("/")
    if args.action == "push":
        s = push(args.backups, bucket, prefix, args.keep)
        print(f"off-machine: {s['uploaded']} file(s) up ({s['uploaded_bytes'] / 1e6:.1f} MB), "
              f"{s['unchanged']} unchanged, {s['deleted']} removed — s3://{bucket}/{prefix}/")
    else:
        stamp = pull(args.backups, bucket, prefix)
        print(f"pulled backup {stamp} from s3://{bucket}/{prefix}/ into {args.backups}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
