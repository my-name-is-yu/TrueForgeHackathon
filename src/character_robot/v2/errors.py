"""Structured errors shared by the V2 project boundary."""

from __future__ import annotations

import json
import math
from typing import Any


_MAX_ERROR_TEXT = 512
_MAX_ERROR_ITEMS = 16
_MAX_ERROR_DEPTH = 4
_MAX_ERROR_BYTES = 16 * 1024


def _safe_type_name(value: object) -> str:
    try:
        return type(value).__name__[:_MAX_ERROR_TEXT]
    except Exception:
        return "unknown"


def _safe_text(value: object, *, limit: int = _MAX_ERROR_TEXT) -> str:
    try:
        text = str(value)
    except Exception:
        text = f"<{_safe_type_name(value)}>"
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 1)]}…"


def _safe_contract_text(value: object, *, limit: int = 128) -> str | None:
    return None if value is None else _safe_text(value, limit=limit)


def _limited_iterable(value: object) -> tuple[list[object], bool] | None:
    try:
        iterator = iter(value)  # type: ignore[arg-type]
    except Exception:
        return None
    items: list[object] = []
    try:
        for item in iterator:
            if len(items) >= _MAX_ERROR_ITEMS:
                return items, True
            items.append(item)
    except Exception:
        pass
    return items, False


def _safe_json_value(value: object, *, depth: int = 0) -> object:
    """Convert untrusted error fields to bounded JSON-compatible values."""

    if depth >= _MAX_ERROR_DEPTH:
        return "<truncated>"
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        return _safe_text(value)
    if isinstance(value, int):
        try:
            return value if len(str(value)) <= _MAX_ERROR_TEXT else _safe_text(value)
        except (ValueError, OverflowError):
            return f"<oversized {_safe_type_name(value)}>"
    if isinstance(value, float):
        return value if math.isfinite(value) else "<non-finite float>"
    if isinstance(value, (bytes, bytearray, memoryview)):
        try:
            raw = bytes(value)
            encoded = raw[: _MAX_ERROR_TEXT // 2].hex()
            suffix = "" if len(raw) <= _MAX_ERROR_TEXT // 2 else "…"
            return {"type": "bytes", "hex": f"{encoded}{suffix}"}
        except Exception:
            return f"<unreadable {_safe_type_name(value)}>"
    if isinstance(value, dict):
        result: dict[str, object] = {}
        try:
            limited = _limited_iterable(value.items())
        except Exception:
            return f"<unreadable {_safe_type_name(value)}>"
        if limited is None:
            return f"<unreadable {_safe_type_name(value)}>"
        items, truncated = limited
        for key, item in items[:_MAX_ERROR_ITEMS]:
            result[_safe_text(key, limit=128)] = _safe_json_value(item, depth=depth + 1)
        if truncated:
            result["<truncated>"] = "more items"
        return result
    if isinstance(value, (list, tuple, set, frozenset)):
        limited = _limited_iterable(value)
        if limited is None:
            return f"<unreadable {_safe_type_name(value)}>"
        items, truncated = limited
        result = [
            _safe_json_value(item, depth=depth + 1) for item in items[:_MAX_ERROR_ITEMS]
        ]
        if truncated:
            result.append("<more items>")
        return result
    return f"<unserializable {_safe_type_name(value)}>"


def _bounded_error_payload(payload: dict[str, object]) -> dict[str, object]:
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, OverflowError, RecursionError):
        encoded = b""
    if len(encoded) <= _MAX_ERROR_BYTES:
        return payload
    return {
        "code": _safe_text(payload.get("code"), limit=128),
        "path": _safe_text(payload.get("path"), limit=128),
        "expected": _safe_text(payload.get("expected")),
        "actual": _safe_text(payload.get("actual")),
        "retryable": bool(payload.get("retryable")),
        "current_target": _safe_contract_text(payload.get("current_target"), limit=128),
        "next_actions": [
            _safe_text(action, limit=256)
            for action in payload.get("next_actions", [])[:_MAX_ERROR_ITEMS]  # type: ignore[index]
        ],
    }


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
        if code is None:
            self.code = self.code
        else:
            try:
                self.code = code if bool(code) else self.code
            except Exception:
                self.code = self.code
        self.path = path
        self.expected = expected
        self.actual = actual
        try:
            self.retryable = bool(retryable)
        except Exception:
            self.retryable = False
        self.current_target = current_target
        try:
            self.next_actions = tuple(next_actions)
        except Exception:
            self.next_actions = (next_actions,)
        self.message = message or self.code
        super().__init__(self.message)

    def as_dict(self) -> dict[str, Any]:
        actions = [
            _safe_text(action, limit=256)
            for action in self.next_actions[:_MAX_ERROR_ITEMS]
        ]
        if len(self.next_actions) > _MAX_ERROR_ITEMS:
            actions.append(
                f"<{len(self.next_actions) - _MAX_ERROR_ITEMS} more actions>"
            )
        payload: dict[str, object] = {
            "code": _safe_text(self.code, limit=128),
            "path": _safe_text(self.path, limit=128),
            "expected": self.expected,
            "actual": self.actual,
            "retryable": self.retryable,
            "current_target": _safe_contract_text(self.current_target),
            "next_actions": actions,
        }
        return _bounded_error_payload(
            {key: _safe_json_value(value) for key, value in payload.items()}
        )  # type: ignore[return-value]

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
