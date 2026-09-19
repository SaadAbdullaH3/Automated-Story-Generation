"""Phase 5 — top-level edit agent with versioned undo.

Flow:
  user query -> IntentClassifier -> planner.plan -> EditExecutor.execute
                                  -> StateManager.snapshot (new version)
The agent itself is stateful via StateManager; LangGraph's MemorySaver concept
is mirrored by storing the running PipelineState per project.
"""
from __future__ import annotations
from typing import Dict, List, Optional, Tuple

from shared.schemas.edit import EditCommand, EditIntent, EditResult
from shared.schemas.pipeline import PipelineState
from shared.utils.logging import get_logger
from state_manager.snapshot import referenced_files
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

    def edit(self, command: EditCommand) -> EditResult:
        """Classify, plan, execute, snapshot. Returns the new version + result."""
        state = self.sm.latest(command.project_id)
        if not state:
            return EditResult(
                success=False,
                intent=EditIntent(intent="noop", target="video", scope="global"),
                error=f"no project state for {command.project_id}",
            )

        intent = self.classifier.classify(command.query, *self._context(state))
        log.info("[%s] classified '%s' -> %s/%s/%s",
                 command.project_id, command.query, intent.intent,
                 intent.target, intent.scope)

        affected: List[str] = []
        try:
            for step in plan_steps(intent):
                affected.extend(self.executor.execute(state, step))
        except Exception as e:  # noqa: BLE001
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
            edit_intent=intent.model_dump(mode="json"),
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
    def _context(state: Optional[PipelineState]) -> Tuple[List[str], List[str], Dict[str, str]]:
        """(scene ids, character ids, character id -> name) for the classifier."""
        if not state or not state.script:
            return [], [], {}
        chars = state.script.characters.characters
        return ([s.scene_id for s in state.script.scenes],
                [c.id for c in chars], {c.id: c.name for c in chars})

    @staticmethod
    def _collect_assets(state: PipelineState) -> List[str]:
        return referenced_files(state)
