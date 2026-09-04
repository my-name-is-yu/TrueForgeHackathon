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
        changed_entities: tuple[str, ...] | list[str] = (),
        invalidated_domains: tuple[DomainName, ...] | list[DomainName] = (),
        invalidated_artifacts: tuple[str, ...] | list[str] = (),
        invalidated_evidence: tuple[str, ...] | list[str] = (),
        blockers: tuple[str, ...] | list[str] = (),
        next_actions: tuple[str, ...] | list[str] = (),
    ) -> WriteResult:
        """Apply one shell write and return the next target plus invalidations.

        ``spec`` is the complete candidate shell.  ``update`` is a convenience
        for tests and future typed domain adapters; it is applied to the current
        shell with Pydantic's strict validation before the token-checked write.
        Requirements are compared to the stored value and can never be replaced.
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
        saved = self.store.save_project(
            candidate_snapshot,
            expected_target_token=active_target_token,
        )
        changed = tuple(changed_entities)
        if not changed:
            changed = self._changed_entities(current.spec, candidate)
        invalidated = tuple(invalidated_domains)
        if not invalidated:
            invalidated = self._changed_domains(current.spec, candidate)
        derived_blockers = tuple(blockers)
        derived_actions = tuple(next_actions)
        if not derived_blockers or not derived_actions:
            readiness = saved.readiness
            generated_blockers, generated_actions = self._readiness_actions(readiness)
            if not derived_blockers:
                derived_blockers = generated_blockers
            if not derived_actions:
                derived_actions = generated_actions
        return WriteResult(
            state=saved,
            changed_entities=changed,
            invalidated_domains=invalidated,
            invalidated_artifacts=tuple(invalidated_artifacts),
            invalidated_evidence=tuple(invalidated_evidence),
            blockers=derived_blockers,
            next_actions=derived_actions,
            next_target_token=saved.active_target_token,
        )

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
        if before.active_draft_id != after.active_draft_id:
            changed.append("active_draft")
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
                blockers.extend(
                    f"{domain.domain_id}: {blocker}" for blocker in domain.blockers
                )
                if not domain.blockers:
                    blockers.append(f"{domain.domain_id}: blocked")
                actions.append(f"Resolve blockers in {domain.domain_id}.")
            elif domain.state in {"missing", "dirty"}:
                blockers.append(f"{domain.domain_id}: {domain.state}")
                actions.append(f"Complete and check {domain.domain_id}.")
        return tuple(blockers), tuple(actions)


__all__ = ["V2ProjectService"]
