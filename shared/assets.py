"""Where a finished asset lives, and the one place that turns it into a URL.

**Local disk is the default, deliberately.** The API serves `data/outputs` at
`/assets`, which needs no configuration, no network round trip and no expiring
links. It only stops working when the worker and the API are on different
hosts — which the job queue makes possible but which a laptop is not.

Nothing but `STORAGE_URL` changes that. Bucket credentials sitting in the
environment do not: otherwise adding a key to try something out would quietly
reroute every local render through object storage.

Set `STORAGE_URL=s3://bucket` (Cloudflare R2's free tier is S3-compatible) and
finished assets are uploaded instead, with `asset_url` returning the public URL.
Everything else in the codebase keeps writing ordinary files to
`data/outputs`; publishing happens once, after a job succeeds.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path, PureWindowsPath
from typing import Dict, Iterable, List, Optional

from shared import constants
from shared.utils.logging import get_logger

log = get_logger("assets")


def key_for(path: str | Path) -> Optional[str]:
    """The storage key for a file under data/outputs, e.g. "pid/subtitles/urdu.vtt".

    Paths recorded on Windows arrive with backslashes and may be read back on
    Linux, so they are split on both separators rather than by os.path.
    """
    if not path:
        return None
    parts = list(PureWindowsPath(str(path)).parts)
    root_name = PureWindowsPath(str(constants.OUTPUTS_DIR)).name
    for i in range(len(parts) - 1, -1, -1):
        if parts[i] == root_name:
            tail = parts[i + 1:]
            return "/".join(tail) if tail else None
    return None


# ---- backends ---------------------------------------------------------------

class LocalAssets:
    """The API serves data/outputs itself."""

    name = "local"

    def url(self, key: str) -> str:
        return f"/assets/{key}"

    def publish(self, key: str, path: Path) -> str:
        return self.url(key)


class S3Assets:
    """Any S3-compatible bucket: AWS, MinIO, or Cloudflare R2's free 10 GB."""

    name = "s3"

    def __init__(self, bucket: str):
        self.bucket = bucket
        self.public_base = os.getenv("S3_PUBLIC_BASE", "").rstrip("/")
        self._client = None

    def client(self):
        if self._client is None:
            import boto3  # imported here so the default install needs no AWS SDK
            from botocore.config import Config
            endpoint = os.getenv("S3_ENDPOINT_URL") or None
            # Virtual-host addressing (bucket.host) is boto3's default and needs
            # DNS per bucket; R2, MinIO and the like are addressed by path. Only
            # real AWS is left on "auto".
            style = os.getenv("S3_ADDRESSING_STYLE") or ("path" if endpoint else "auto")
            self._client = boto3.client(
                "s3",
                endpoint_url=endpoint,
                region_name=os.getenv("S3_REGION", "auto"),
                config=Config(s3={"addressing_style": style},
                              retries={"max_attempts": 3, "mode": "standard"}),
            )
        return self._client

    def url(self, key: str) -> str:
        if self.public_base:
            return f"{self.public_base}/{key}"
        # No public domain on the bucket: hand out a time-limited link.
        return self.client().generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=3600)

    def publish(self, key: str, path: Path) -> str:
        self.client().upload_file(str(path), self.bucket, key)
        return self.url(key)


@lru_cache(maxsize=1)
def store():
    """The configured backend. STORAGE_URL=s3://bucket, or local by default."""
    url = os.getenv("STORAGE_URL", "").strip()
    if url.startswith("s3://"):
        return S3Assets(url[len("s3://"):].strip("/"))
    if url and url not in ("local", "file://"):
        log.warning("unrecognised STORAGE_URL %r - using local disk", url)
    return LocalAssets()


def reset() -> None:
    """Forget the cached backend (tests, and anything that re-reads the env)."""
    store.cache_clear()


# ---- the API the rest of the code uses --------------------------------------

def asset_url(path: str | Path | None) -> Optional[str]:
    """A URL the browser can fetch for a file under data/outputs."""
    if not path:
        return None
    key = key_for(path)
    return store().url(key) if key else None


def publish(paths: Iterable[str | Path]) -> Dict[str, str]:
    """Upload finished assets, if the backend needs it. Returns {path: url}.

    With local storage this only computes URLs — nothing is copied.
    """
    backend = store()
    out: Dict[str, str] = {}
    failed: List[str] = []
    for path in paths:
        key = key_for(path)
        if not key:
            continue
        file = Path(str(path))
        if not file.exists():
            continue
        try:
            out[str(path)] = backend.publish(key, file)
        except Exception as e:  # noqa: BLE001
            # A render that finished is still worth keeping; say what didn't
            # reach the bucket rather than failing the whole job.
            log.error("could not publish %s: %s: %s", key, type(e).__name__, e)
            failed.append(key)
    if failed:
        log.warning("%d asset(s) stayed local only", len(failed))
    return out


def backend_name() -> str:
    return store().name
