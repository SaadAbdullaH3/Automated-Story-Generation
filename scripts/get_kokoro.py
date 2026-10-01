"""Download the Kokoro voice model (open-source TTS, runs offline on CPU).

    pip install -r requirements-voices.txt
    python scripts/get_kokoro.py

Puts ~340 MB in data/models/kokoro/ and prints the two .env lines to add.
Kokoro is Apache-2.0 and needs no key, no GPU and no network once downloaded.
"""
from __future__ import annotations
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "data" / "models" / "kokoro"
RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
FILES = {
    "kokoro-v1.0.onnx": f"{RELEASE}/kokoro-v1.0.onnx",
    "voices-v1.0.bin": f"{RELEASE}/voices-v1.0.bin",
}


def download(url: str, dest: Path) -> None:
    print(f"  {dest.name} <- {url}")

    def progress(block: int, size: int, total: int) -> None:
        if total > 0:
            done = min(100, block * size * 100 // total)
            print(f"\r    {done:3d}%  ({total / 1e6:.0f} MB)", end="", flush=True)

    urllib.request.urlretrieve(url, dest, reporthook=progress)
    print()


def main() -> int:
    TARGET.mkdir(parents=True, exist_ok=True)
    print(f"Downloading Kokoro into {TARGET}")
    for name, url in FILES.items():
        dest = TARGET / name
        if dest.exists() and dest.stat().st_size > 1_000_000:
            print(f"  {name} already here ({dest.stat().st_size / 1e6:.0f} MB)")
            continue
        try:
            download(url, dest)
        except Exception as e:  # noqa: BLE001
            print(f"\n  failed: {e}\n  Download it manually from {url}")
            return 1
    print("\nAdd these to .env:")
    print(f"KOKORO_MODEL={TARGET / 'kokoro-v1.0.onnx'}")
    print(f"KOKORO_VOICES={TARGET / 'voices-v1.0.bin'}")
    print("\nThen `python main.py providers` will list kokoro ahead of edge-tts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
