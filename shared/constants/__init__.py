"""Project-wide constants and paths."""
import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
# Everything a deployment keeps — films, versions, the database — lives here.
# DATA_DIR moves it (a container volume, a data disk, a throwaway copy).
DATA_DIR = Path(os.getenv("DATA_DIR") or ROOT_DIR / "data").resolve()
OUTPUTS_DIR = DATA_DIR / "outputs"
TEMP_DIR = DATA_DIR / "temp"
STATE_DIR = DATA_DIR / "state_versions"
DB_PATH = DATA_DIR / "state.db"

DEFAULT_FPS = 24
DEFAULT_WIDTH = 1280
DEFAULT_HEIGHT = 720
DEFAULT_SAMPLE_RATE = 22050

# Phase names
PHASE_STORY = "story"
PHASE_AUDIO = "audio"
PHASE_VIDEO = "video"
PHASE_EDIT = "edit"

# Edit targets
TARGET_AUDIO = "audio"
TARGET_VIDEO_FRAME = "video_frame"
TARGET_VIDEO = "video"
TARGET_SCRIPT = "script"

for d in (DATA_DIR, OUTPUTS_DIR, TEMP_DIR, STATE_DIR):
    d.mkdir(parents=True, exist_ok=True)
