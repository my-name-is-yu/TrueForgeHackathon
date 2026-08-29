from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


BAD_RESPONSE = "UPSTREAM_BAD_RESPONSE"
UPSTREAM_UNAVAILABLE = "UPSTREAM_UNAVAILABLE"
SAFE_MESSAGE = "The upstream simulation operation failed."
SAFE_NEXT_ACTION = "Reload the immutable model and retry once."
SAFE_SLOT_ACTION = "Do not reuse the affected simulation slot."


@dataclass(frozen=True)
class UpstreamToolError(Exception):
    code: str
    message: str
    retryable: bool
    next_action: str

    def __str__(self) -> str:
        return self.message

    def envelope(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "next_action": self.next_action,
        }


def _text_blocks(result: Any) -> list[str]:
    blocks = getattr(result, "content", None)
    if not isinstance(blocks, list):
        raise UpstreamToolError(
            BAD_RESPONSE,
            "Upstream response content was invalid.",
            False,
            SAFE_SLOT_ACTION,
        )
    if (
        len(blocks) != 1
        or getattr(blocks[0], "type", None) != "text"
        or not isinstance(getattr(blocks[0], "text", None), str)
    ):
        raise UpstreamToolError(
            BAD_RESPONSE,
            "Upstream response content was unexpected.",
            False,
            SAFE_SLOT_ACTION,
        )
    return [blocks[0].text]


def _is_error_result(result: Any) -> bool:
    return bool(
        getattr(result, "isError", False)
        or getattr(result, "is_error", False)
    )


def normalize_json_result(
    result: Any,
    validate: Callable[[dict[str, Any]], bool],
) -> dict[str, Any]:
    texts = _text_blocks(result)
    try:
        payload = json.loads(texts[0])
    except (TypeError, json.JSONDecodeError) as exc:
        raise UpstreamToolError(
            BAD_RESPONSE,
            "Upstream response was not valid JSON.",
            False,
            SAFE_SLOT_ACTION,
        ) from exc

    if not isinstance(payload, dict):
        raise UpstreamToolError(
            BAD_RESPONSE,
            "Upstream response JSON had an invalid shape.",
            False,
            SAFE_SLOT_ACTION,
        )

    if _is_error_result(result) or "error" in payload:
        raise UpstreamToolError(
            UPSTREAM_UNAVAILABLE,
            SAFE_MESSAGE,
            True,
            SAFE_NEXT_ACTION,
        )
    if not validate(payload):
        raise UpstreamToolError(
            BAD_RESPONSE,
            "Upstream response JSON did not match the expected schema.",
            False,
            SAFE_SLOT_ACTION,
        )
    return payload
