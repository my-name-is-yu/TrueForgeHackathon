"""Structured errors shared by the V2 project boundary."""

from __future__ import annotations

from typing import Any


class V2ContractError(RuntimeError):
    """An expected contract failure with a stable, JSON-safe error shape."""

    code = "V2_CONTRACT_ERROR"

    def __init__(
        self,
        *,
        code: str | None = None,
        path: str = "$",
        expected: Any = None,
        actual: Any = None,
        retryable: bool = False,
        current_target: str | None = None,
        next_actions: tuple[str, ...] = (),
        message: str | None = None,
    ) -> None:
        self.code = code or self.code
        self.path = path
        self.expected = expected
        self.actual = actual
        self.retryable = bool(retryable)
        self.current_target = current_target
        self.next_actions = tuple(next_actions)
        self.message = message or self.code
        super().__init__(self.message)

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "path": self.path,
            "expected": self.expected,
            "actual": self.actual,
            "retryable": self.retryable,
            "current_target": self.current_target,
            "next_actions": list(self.next_actions),
        }

    to_dict = as_dict


class UnsupportedSchemaVersionError(V2ContractError):
    code = "UNSUPPORTED_SCHEMA_VERSION"

    def __init__(
        self,
        *,
        actual: Any,
        path: str = "schema_version",
        current_target: str | None = None,
    ) -> None:
        super().__init__(
            code=self.code,
            path=path,
            expected="character-project/v2",
            actual=actual,
            retryable=False,
            current_target=current_target,
            next_actions=(
                "Create a new Character Robot Studio V2 project; V1 migration is not supported.",
            ),
            message="the project schema is not supported by the V2 boundary",
        )


class StaleTargetTokenError(V2ContractError):
    code = "STALE_TARGET_TOKEN"

    def __init__(
        self,
        *,
        actual: Any,
        current_target: str,
        path: str = "active_target_token",
    ) -> None:
        super().__init__(
            code=self.code,
            path=path,
            expected="the current active_target_token",
            actual=actual,
            retryable=True,
            current_target=current_target,
            next_actions=(
                "Reload project state and retry the write against the returned active target.",
            ),
            message="the active target token is stale",
        )


class V2ProjectNotFoundError(V2ContractError):
    code = "PROJECT_NOT_FOUND"

    def __init__(self, project_id: str) -> None:
        super().__init__(
            code=self.code,
            path="project_id",
            expected="an existing V2 project",
            actual=project_id,
            retryable=False,
            next_actions=("Create the V2 project before reading or writing it.",),
            message=f"V2 project {project_id!r} does not exist",
        )


class V2ProjectAlreadyExistsError(V2ContractError):
    code = "PROJECT_ALREADY_EXISTS"

    def __init__(self, project_id: str) -> None:
        super().__init__(
            code=self.code,
            path="project_id",
            expected="a new project ID",
            actual=project_id,
            retryable=False,
            next_actions=("Load the existing project instead.",),
            message=f"V2 project {project_id!r} already exists",
        )


class V2ValidationError(V2ContractError):
    code = "INVALID_V2_PROJECT"

    def __init__(
        self,
        *,
        path: str = "$",
        expected: Any = "a valid Character Robot Studio V2 project",
        actual: Any = None,
        message: str = "the V2 project is invalid",
    ) -> None:
        super().__init__(
            code=self.code,
            path=path,
            expected=expected,
            actual=actual,
            retryable=False,
            next_actions=("Correct the project payload and retry.",),
            message=message,
        )


class ImmutableRequirementsError(V2ContractError):
    code = "IMMUTABLE_REQUIREMENTS"

    def __init__(self, *, current_target: str | None = None) -> None:
        super().__init__(
            code=self.code,
            path="spec.requirements",
            expected="the original immutable requirements",
            actual="a different requirements object",
            retryable=False,
            current_target=current_target,
            next_actions=(
                "Create a new V2 project for a materially different user goal.",
            ),
            message="requirements cannot be replaced after project creation",
        )


__all__ = [
    "ImmutableRequirementsError",
    "StaleTargetTokenError",
    "UnsupportedSchemaVersionError",
    "V2ContractError",
    "V2ProjectAlreadyExistsError",
    "V2ProjectNotFoundError",
    "V2ValidationError",
]
