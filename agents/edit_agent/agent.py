"""Phase 5 — top-level edit agent with versioned undo.

Flow:
  user query -> IntentClassifier -> planner.plan -> EditExecutor.execute
                                  -> StateManager.snapshot (new version)
The agent itself is stateful via StateManager; LangGraph's MemorySaver concept
is mirrored by storing the running PipelineState per project.
"""
from __future__ import annotations
from typing import Any, Callable, Dict, List, Optional, Tuple

from shared.schemas.edit import EditCommand, EditIntent, EditResult
from shared.schemas.pipeline import PipelineState
from shared.utils.logging import get_logger
from state_manager.snapshot import referenced_files, restore_assets
from state_manager.state_manager import StateManager

from .executor import EditExecutor
from .intent_classifier import IntentClassifier
from .planner import plan as plan_steps

log = get_logger("edit_agent")


class EditAgent:
    def __init__(self, state_manager: Optional[StateManager] = None):
        self.classifier = IntentClassifier()
        self.executor = EditExecutor()
        self.sm = state_manager or StateManager()

    # ---- public API ------------------------------------------------------

    def classify(self, command: EditCommand,
                 state: Optional[PipelineState] = None) -> EditIntent:
        """Stateless intent classification — used by /api/edit/classify."""
        return self.classifier.classify(command.query, *self._context(state))

    def edit(self, command: EditCommand,
             on_progress: Optional[Callable[[str, Dict[str, Any]], None]] = None) -> EditResult:
        """Classify, plan, execute, snapshot. Returns the new version + result.

        `on_progress(kind, info)` hears "understood" once the request is
        classified, then "step" before each step. It may raise to stop the
        edit (that is how a cancelled job stops) — the files go back to the
        saved version either way.
        """
        progress = on_progress or (lambda _kind, _info: None)
        state = self.sm.latest(command.project_id)
        if not state:
            return EditResult(
                success=False,
                intent=EditIntent(intent="noop", target="video", scope="global"),
                error=f"no project state for {command.project_id}",
            )

        intent = self.classifier.classify(command.query, *self._context(state))
        log.info("[%s] classified '%s' -> %s/%s/%s %s",
                 command.project_id, command.query, intent.intent,
                 intent.target, intent.scope, intent.parameters)
        try:
            steps = plan_steps(intent)
        except ValueError as e:
            # Not understood, or missing what it needs: say so, change nothing.
            res = EditResult(success=False, intent=intent, error=str(e))
            self.sm.log_edit(command.project_id, command.query,
                             intent.model_dump(mode="json"), res.model_dump(mode="json"))
            return res
        progress("understood", {"intent": intent.model_dump(mode="json"),
                                "steps": [s.name for s in steps]})

        # Edits change files in place, so start from exactly what the saved
        # version holds: an earlier edit that died half way (a killed worker)
        # must not leave its half-applied change for this one to bake in.
        self._put_back(state)
        affected: List[str] = []
        try:
            for i, step in enumerate(steps):
                progress("step", {"name": step.name, "index": i, "total": len(steps)})
                affected.extend(self.executor.execute(state, step))
        except BaseException as e:  # noqa: BLE001 — a cancelled job stops here too
            self._put_back(state)
            if not isinstance(e, Exception):
                raise
            log.exception("edit execution failed")
            res = EditResult(
                success=False,
                intent=intent,
                affected_assets=affected,
                error=f"{type(e).__name__}: {e}",
            )
            self.sm.log_edit(command.project_id, command.query,
                             intent.model_dump(mode="json"),
                             res.model_dump(mode="json"))
            return res

        # Snapshot the new state.
        version = self.sm.snapshot(
            state,
            asset_paths=self._collect_assets(state),
            description=f"edit: {intent.intent} ({intent.target})",
            # The creator's own words travel with the version, so the history
            # can show what they asked for rather than an intent name.
            edit_intent={**intent.model_dump(mode="json"), "query": command.query},
        )

        result = EditResult(
            success=True,
            intent=intent,
            new_version=version.version,
            affected_assets=list(set(affected)),
            message=f"applied '{intent.intent}' on {intent.target} ({intent.scope})",
        )
        self.sm.log_edit(command.project_id, command.query,
                         intent.model_dump(mode="json"),
                         result.model_dump(mode="json"))
        return result

    def revert(self, project_id: str, version: int) -> PipelineState:
        return self.sm.revert(project_id, version)

    def history(self, project_id: str) -> List[dict]:
        return self.sm.history(project_id)

    def edit_log(self, project_id: str) -> List[dict]:
        return self.sm.edit_history(project_id)

    # ---- helpers ---------------------------------------------------------

    @staticmethod
    def _put_back(state: PipelineState) -> None:
        """Make the project's files match the version `state` was loaded from."""
        try:
            restore_assets(state.project_id, state.version)
        except Exception:  # noqa: BLE001 — never hide the edit's own error behind this
            log.exception("could not restore %s v%d", state.project_id, state.version)

    @staticmethod
    def _context(state: Optional[PipelineState]
                 ) -> Tuple[List[str], List[str], Dict[str, str], Dict[str, str]]:
        """(scene ids, character ids, character id -> name, scene id -> title) for
        the classifier — titles let "the market scene" find scene_1."""
        if not state or not state.script:
            return [], [], {}, {}
        chars = state.script.characters.characters
        return ([s.scene_id for s in state.script.scenes],
                [c.id for c in chars], {c.id: c.name for c in chars},
                {s.scene_id: s.title for s in state.script.scenes})

    @staticmethod
    def _collect_assets(state: PipelineState) -> List[str]:
        return referenced_files(state)
