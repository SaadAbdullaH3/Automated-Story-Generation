"""High-level StateManager — snapshot, revert, history."""
from __future__ import annotations
from datetime import datetime
from typing import List, Optional, Dict, Any

from shared import constants
from shared.schemas.pipeline import PipelineState, PipelineVersion
from shared.utils.files import ensure_dir, write_json, read_json
from shared.utils.logging import get_logger

from .snapshot import snapshot_assets, restore_assets
from .storage import VersionStore


log = get_logger("state_manager")


class StateManager:
    """Versioned, append-only state store across all pipeline phases."""

    def __init__(self, storage: Optional[VersionStore] = None):
        self.storage = storage or VersionStore()

    # ---- snapshot --------------------------------------------------------

    def snapshot(
        self,
        state: PipelineState,
        asset_paths: List[str],
        description: str = "",
        edit_intent: Optional[Dict[str, Any]] = None,
    ) -> PipelineVersion:
        """Bump the version counter, persist state JSON + assets."""
        version = self.storage.latest_version(state.project_id) + 1
        parent = version - 1 if version > 1 else None

        state.version = version
        state.touch()

        snap_dir = ensure_dir(constants.STATE_DIR / state.project_id / f"v{version}")
        state_path = snap_dir / "state.json"
        write_json(state_path, state.model_dump(mode="json"))

        saved_assets = snapshot_assets(state.project_id, version, asset_paths)

        self.storage.append_version(
            project_id=state.project_id,
            version=version,
            state_path=str(state_path),
            asset_paths=saved_assets,
            description=description,
            parent_version=parent,
            edit_intent=edit_intent,
            created_at=datetime.utcnow().isoformat(),
        )

        log.info("snapshot v%d (%s) created for %s", version, description, state.project_id)
        return PipelineVersion(
            version=version,
            project_id=state.project_id,
            created_at=datetime.utcnow().isoformat(),
            description=description,
            state_path=str(state_path),
            asset_paths=saved_assets,
            parent_version=parent,
            edit_intent=edit_intent,
        )

    # ---- revert ----------------------------------------------------------

    def revert(self, project_id: str, version: int) -> PipelineState:
        """Restore both state JSON and assets to the requested snapshot."""
        record = self.storage.get_version(project_id, version)
        if not record:
            raise ValueError(f"no version v{version} for project {project_id}")

        data = read_json(record["state_path"])
        state = PipelineState.model_validate(data)

        restored = restore_assets(project_id, version)
        log.info("reverted %s to v%d (%d assets restored)", project_id, version, len(restored))

        # Snapshot the revert itself so the history stays linear.
        new_version = self.storage.latest_version(project_id) + 1
        state.version = new_version
        state.touch()
        state.metadata["reverted_from"] = version
        state.metadata["last_action"] = "revert"
        snap_dir = ensure_dir(constants.STATE_DIR / project_id / f"v{new_version}")
        state_path = snap_dir / "state.json"
        write_json(state_path, state.model_dump(mode="json"))
        snapshot_assets(project_id, new_version, restored)
        self.storage.append_version(
            project_id=project_id,
            version=new_version,
            state_path=str(state_path),
            asset_paths=restored,
            description=f"revert to v{version}",
            parent_version=version,
            edit_intent={"intent": "revert", "target": "version", "scope": f"v{version}"},
            created_at=datetime.utcnow().isoformat(),
        )
        return state

    # ---- history ---------------------------------------------------------

    def history(self, project_id: str) -> List[Dict[str, Any]]:
        return self.storage.list_versions(project_id)

    def latest(self, project_id: str) -> Optional[PipelineState]:
        v = self.storage.latest_version(project_id)
        if v == 0:
            return None
        rec = self.storage.get_version(project_id, v)
        return PipelineState.model_validate(read_json(rec["state_path"]))

    def load_version(self, project_id: str, version: int) -> Optional[PipelineState]:
        rec = self.storage.get_version(project_id, version)
        if not rec:
            return None
        return PipelineState.model_validate(read_json(rec["state_path"]))

    # ---- edit log --------------------------------------------------------

    def log_edit(
        self,
        project_id: str,
        query: str,
        intent: Dict[str, Any],
        result: Dict[str, Any],
    ) -> None:
        self.storage.log_edit(
            project_id=project_id,
            query=query,
            intent=intent,
            result=result,
            created_at=datetime.utcnow().isoformat(),
        )

    def edit_history(self, project_id: str) -> List[Dict[str, Any]]:
        return self.storage.list_edits(project_id)

    def list_projects(self) -> List[str]:
        return self.storage.list_projects()
