"""Disk-level snapshot/restore of pipeline assets."""
from __future__ import annotations
import shutil
from pathlib import Path
from typing import Any, Dict, Iterable, List

from shared import constants
from shared.utils.files import ensure_dir, project_dir


def referenced_files(state: Any, extra: Iterable[str] = ()) -> List[str]:
    """Every existing file inside the project directory that `state` points to.

    Walks the whole serialized state (script, audio, video, phase artifact
    lists), so any file an agent or edit records is captured by snapshots
    without maintaining separate asset lists.
    """
    proj_root = project_dir(state.project_id).resolve()
    found: Dict[str, None] = {}

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for v in value.values():
                visit(v)
        elif isinstance(value, (list, tuple)):
            for v in value:
                visit(v)
        elif isinstance(value, str) and ("/" in value or "\\" in value) and len(value) < 1024:
            p = Path(value)
            try:
                if p.is_file() and p.resolve().is_relative_to(proj_root):
                    found.setdefault(str(p), None)
            except (OSError, ValueError):
                pass

    visit(state.model_dump(mode="json"))
    for path in extra:
        if path and Path(path).is_file():
            found.setdefault(str(path), None)
    return list(found)


def snapshot_assets(project_id: str, version: int, asset_paths: List[str]) -> List[str]:
    """Copy each asset into the snapshot directory; returns the snapshot copies."""
    snap_root = ensure_dir(constants.STATE_DIR / project_id / f"v{version}")
    saved: List[str] = []
    proj_root = project_dir(project_id)
    for src in asset_paths:
        src_p = Path(src)
        if not src_p.exists():
            continue
        try:
            rel = src_p.relative_to(proj_root)
        except ValueError:
            rel = Path(src_p.name)
        dst = snap_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src_p.is_dir():
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src_p, dst)
        else:
            shutil.copy2(src_p, dst)
        saved.append(str(dst))
    return saved


def restore_assets(project_id: str, version: int) -> List[str]:
    """Copy a snapshot back into the live project directory."""
    snap_root = constants.STATE_DIR / project_id / f"v{version}"
    if not snap_root.exists():
        raise FileNotFoundError(f"snapshot v{version} not found for {project_id}")
    proj_root = ensure_dir(project_dir(project_id))
    restored: List[str] = []
    for src in snap_root.rglob("*"):
        if src.is_dir():
            continue
        rel = src.relative_to(snap_root)
        dst = proj_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        restored.append(str(dst))
    return restored
