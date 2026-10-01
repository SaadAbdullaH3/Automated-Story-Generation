"""Append-only version log.

Backed by `shared.db`, so it is SQLite on a laptop and Postgres in a deployment
without the calling code knowing which.
"""
from __future__ import annotations
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select

from shared import db


class VersionStore:
    """Thin wrapper around the version log tables."""

    def __init__(self, db_path: Path | str | None = None, url: str | None = None):
        if url is None and db_path is not None:
            url = db.url_for_path(db_path)
        self.engine = db.get_engine(url)

    @property
    def db_path(self) -> Path:
        """The SQLite file behind this store (for logs and tests)."""
        return Path(str(self.engine.url.database or ""))

    def append_version(
        self,
        project_id: str,
        version: int,
        state_path: str,
        asset_paths: List[str],
        description: str = "",
        parent_version: Optional[int] = None,
        edit_intent: Optional[Dict[str, Any]] = None,
        created_at: str = "",
    ) -> int:
        with self.engine.begin() as c:
            result = c.execute(db.versions.insert().values(
                project_id=project_id,
                version=version,
                parent_version=parent_version,
                created_at=created_at,
                description=description,
                state_path=state_path,
                asset_paths=list(asset_paths),
                edit_intent=edit_intent,
            ))
            return int(result.inserted_primary_key[0])

    def get_version(self, project_id: str, version: int) -> Optional[Dict[str, Any]]:
        with self.engine.connect() as c:
            row = c.execute(
                select(db.versions).where(db.versions.c.project_id == project_id,
                                          db.versions.c.version == version)
            ).mappings().first()
        return self._row_to_dict(row) if row else None

    def list_versions(self, project_id: str) -> List[Dict[str, Any]]:
        with self.engine.connect() as c:
            rows = c.execute(
                select(db.versions)
                .where(db.versions.c.project_id == project_id)
                .order_by(db.versions.c.version.asc())
            ).mappings().all()
        return [self._row_to_dict(r) for r in rows]

    def latest_version(self, project_id: str) -> int:
        with self.engine.connect() as c:
            value = c.execute(
                select(func.max(db.versions.c.version))
                .where(db.versions.c.project_id == project_id)
            ).scalar()
        return int(value or 0)

    def list_projects(self) -> List[str]:
        with self.engine.connect() as c:
            rows = c.execute(
                select(db.versions.c.project_id).distinct()
                .order_by(db.versions.c.project_id.desc())
            ).all()
        return [r[0] for r in rows]

    def log_edit(
        self,
        project_id: str,
        query: str,
        intent: Dict[str, Any],
        result: Dict[str, Any],
        created_at: str,
    ) -> int:
        with self.engine.begin() as c:
            res = c.execute(db.edit_log.insert().values(
                project_id=project_id, created_at=created_at, query=query,
                intent_json=intent, result_json=result,
            ))
            return int(res.inserted_primary_key[0])

    def list_edits(self, project_id: str) -> List[Dict[str, Any]]:
        with self.engine.connect() as c:
            rows = c.execute(
                select(db.edit_log)
                .where(db.edit_log.c.project_id == project_id)
                .order_by(db.edit_log.c.id.desc())
            ).mappings().all()
        return [
            {
                "id": r["id"],
                "project_id": r["project_id"],
                "created_at": r["created_at"],
                "query": r["query"],
                "intent": r["intent_json"],
                "result": r["result_json"],
            }
            for r in rows
        ]

    @staticmethod
    def _row_to_dict(row) -> Dict[str, Any]:
        return {
            "id": row["id"],
            "project_id": row["project_id"],
            "version": row["version"],
            "parent_version": row["parent_version"],
            "created_at": row["created_at"],
            "description": row["description"],
            "state_path": row["state_path"],
            "asset_paths": row["asset_paths"] or [],
            "edit_intent": row["edit_intent"],
        }


# The store was SQLite-only until M4; keep the old name working.
SqliteStorage = VersionStore
