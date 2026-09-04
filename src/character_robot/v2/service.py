"""Application-facing V2 project and concurrency operations."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .contract import (
    DomainName,
    READINESS_DOMAINS,
    Requirements,
    RobotSystemSpec,
    SAFE_TEXT_MAX_LENGTH,
    V2ProjectSnapshot,
    WriteResult,
)
from .errors import ImmutableRequirementsError, V2ValidationError
from .store import V2ProjectStore


class V2ProjectService:
    """Small service boundary that enforces immutable requirements and tokens."""

    def __init__(self, store: V2ProjectStore | Path) -> None:
        self.store = (
            store if isinstance(store, V2ProjectStore) else V2ProjectStore(store)
        )

    def create_project(
        self, project_id: str, requirements: Requirements | dict[str, Any]
    ) -> V2ProjectSnapshot:
        try:
            normalized = (
                requirements
                if isinstance(requirements, Requirements)
                else Requirements.model_validate(requirements)
            )
        except (TypeError, ValueError, ValidationError) as error:
            raise V2ValidationError(
                path="requirements",
                expected="a strict immutable Requirements object",
                actual=str(error),
            ) from error
        return self.store.create_project(project_id, normalized)

    def get_project_state(self, project_id: str) -> V2ProjectSnapshot:
        return self.store.load_project(project_id)

    def readiness(self, project_id: str):
        return self.get_project_state(project_id).readiness

    def write_project(
        self,
        project_id: str,
        active_target_token: str,
        spec: RobotSystemSpec | None = None,
        *,
        updated_spec: RobotSystemSpec | None = None,
        update: dict[str, Any] | None = None,
    ) -> WriteResult:
        """Apply one shell write and return server-derived result metadata.

        ``spec`` is the complete candidate shell.  ``update`` is a convenience
        for tests and future typed domain adapters; it is applied to the current
        shell with Pydantic's strict validation before the token-checked write.
        Requirements are compared to the stored value and can never be replaced.
        Changed entities, invalidations, blockers, and next actions are derived
        by this boundary; callers cannot supply result claims.
        """

        current = self.store.load_project(project_id)
        if spec is not None and updated_spec is not None:
            raise V2ValidationError(
                path="spec",
                expected="one candidate RobotSystemSpec",
                actual="both spec and updated_spec",
            )
        candidate = updated_spec or spec
        if candidate is not None and update is not None:
            raise V2ValidationError(
                path="spec",
                expected="one candidate RobotSystemSpec or update mapping",
                actual="both candidate and update",
            )
        if candidate is None:
            if update is None:
                candidate = current.spec
            else:
                try:
                    candidate_payload = current.spec.model_dump(mode="python")
                    candidate_payload.update(update)
                    candidate = RobotSystemSpec.model_validate(candidate_payload)
                except (TypeError, ValueError, ValidationError) as error:
                    raise V2ValidationError(
                        path="spec",
                        expected="a valid RobotSystemSpec update",
                        actual=str(error),
                    ) from error
        if candidate.project_id != current.project_id:
            raise V2ValidationError(
                path="spec.project_id",
                expected=current.project_id,
                actual=candidate.project_id,
            )
        if candidate.requirements != current.spec.requirements:
            raise ImmutableRequirementsError(current_target=current.active_target_token)

        candidate_snapshot = current.model_copy(update={"spec": candidate})
        changed = self._changed_entities(current.spec, candidate)
        invalidated = self._changed_domains(current.spec, candidate)
        next_state = candidate_snapshot.model_copy(
            update={"generation": current.generation + 1}
        )
        try:
            derived_blockers, derived_actions = self._readiness_actions(
                next_state.readiness
            )
            result = WriteResult(
                state=next_state,
                changed_entities=changed,
                invalidated_domains=invalidated,
                # Artifact and evidence stores are introduced by later V2 issues.
                invalidated_artifacts=(),
                invalidated_evidence=(),
                blockers=derived_blockers,
                next_actions=derived_actions,
                next_target_token=next_state.active_target_token,
            )
        except (TypeError, ValueError, ValidationError) as error:
            raise V2ValidationError(
                path="write_result",
                expected="a valid server-derived WriteResult",
                actual=str(error),
            ) from error

        self.store.save_project(
            candidate_snapshot,
            expected_target_token=active_target_token,
        )
        # ``save_project`` applies the same one-step generation transition that
        # was validated above and returns the equivalent persisted snapshot.
        return result

    @staticmethod
    def _changed_entities(
        before: RobotSystemSpec, after: RobotSystemSpec
    ) -> tuple[str, ...]:
        before_domains = {record.domain_id: record for record in before.domains}
        after_domains = {record.domain_id: record for record in after.domains}
        changed = [
            domain_id
            for domain_id in READINESS_DOMAINS
            if before_domains.get(domain_id) != after_domains.get(domain_id)
        ]
        if before.committed_head_id != after.committed_head_id:
            changed.append("committed_head")
        if before.committed_head_digest != after.committed_head_digest:
            changed.append("committed_head_digest")
        if before.active_draft_id != after.active_draft_id:
            changed.append("active_draft")
        if before.active_draft_digest != after.active_draft_digest:
            changed.append("active_draft_digest")
        return tuple(changed)

    @staticmethod
    def _changed_domains(
        before: RobotSystemSpec, after: RobotSystemSpec
    ) -> tuple[DomainName, ...]:
        before_domains = {record.domain_id: record for record in before.domains}
        after_domains = {record.domain_id: record for record in after.domains}
        return tuple(
            domain_id
            for domain_id in READINESS_DOMAINS
            if before_domains.get(domain_id) != after_domains.get(domain_id)
        )  # type: ignore[return-value]

    @staticmethod
    def _readiness_actions(readiness) -> tuple[tuple[str, ...], tuple[str, ...]]:
        blockers: list[str] = []
        actions: list[str] = []
        for domain in readiness.domains:
            if domain.state == "blocked":
                prefix = f"{domain.domain_id}: "
                blockers.extend(
                    blocker
                    if len(prefix) + len(blocker) > SAFE_TEXT_MAX_LENGTH
                    else f"{prefix}{blocker}"
                    for blocker in domain.blockers
                )
                if not domain.blockers:
                    blockers.append(f"{domain.domain_id}: blocked")
                actions.append(f"Resolve blockers in {domain.domain_id}.")
            elif domain.state in {"missing", "dirty"}:
                blockers.append(f"{domain.domain_id}: {domain.state}")
                actions.append(f"Complete and check {domain.domain_id}.")
        return tuple(blockers), tuple(actions)


__all__ = ["V2ProjectService"]
