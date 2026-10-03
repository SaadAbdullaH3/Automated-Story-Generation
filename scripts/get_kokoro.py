"""Download the Kokoro voice model (open-source TTS, runs offline on CPU).

    pip install -r requirements-voices.txt
    python scripts/get_kokoro.py

Puts ~340 MB in <DATA_DIR>/models/kokoro/ and prints the two .env lines to add.
Kokoro is Apache-2.0 and needs no key, no GPU and no network once downloaded.

Safe to run unattended (the container runs it on every start): a file is only
kept once its SHA-256 matches, so an interrupted download or a swapped release
file is replaced or refused rather than loaded as a broken model.
"""
from __future__ import annotations

import hashlib
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = Path(os.getenv("DATA_DIR") or ROOT / "data").resolve() / "models" / "kokoro"
RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
# name -> (url, sha256). Hashes of the copies verified in use on 2026-10-01.
FILES = {
    "kokoro-v1.0.onnx": (f"{RELEASE}/kokoro-v1.0.onnx",
                         "7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5"),
    "voices-v1.0.bin": (f"{RELEASE}/voices-v1.0.bin",
                        "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d"),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, dest: Path) -> None:
    print(f"  {dest.name} <- {url}", flush=True)
    shown = [-10]

    def progress(block: int, size: int, total: int) -> None:
        if total > 0:
            done = min(100, block * size * 100 // total)
            if done >= shown[0] + 10:          # every 10%: readable in a log
                shown[0] = done
                print(f"    {done:3d}%  of {total / 1e6:.0f} MB", flush=True)

    urllib.request.urlretrieve(url, dest, reporthook=progress)


def fetch(name: str, url: str, expected: str) -> bool:
    dest = TARGET / name
    if dest.exists():
        if sha256(dest) == expected:
            print(f"  {name} already here and verified ({dest.stat().st_size / 1e6:.0f} MB)")
            return True
        print(f"  {name} is here but doesn't match its checksum — downloading again")
    # Written beside the real name and only renamed once verified, so a
    # download cut short never looks like a finished model.
    part = dest.with_name(dest.name + ".part")
    try:
        download(url, part)
    except Exception as e:  # noqa: BLE001
        part.unlink(missing_ok=True)
        print(f"  failed: {e}\n  Download it manually from {url}")
        return False
    actual = sha256(part)
    if actual != expected:
        part.unlink(missing_ok=True)
        print(f"  {name}: checksum mismatch (got {actual[:16]}…, expected {expected[:16]}…) "
              "— refusing it")
        return False
    part.replace(dest)
    return True


def main() -> int:
    TARGET.mkdir(parents=True, exist_ok=True)
    print(f"Kokoro voice model in {TARGET}", flush=True)
    if not all(fetch(name, url, digest) for name, (url, digest) in FILES.items()):
        return 1
    if "--quiet" not in sys.argv:
        print("\nAdd these to .env:")
        print(f"KOKORO_MODEL={TARGET / 'kokoro-v1.0.onnx'}")
        print(f"KOKORO_VOICES={TARGET / 'voices-v1.0.bin'}")
        print("\nThen `python main.py providers` will list kokoro ahead of edge-tts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
