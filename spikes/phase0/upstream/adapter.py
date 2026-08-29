from __future__ import annotations

import json
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
    texts = [
        block.text
        for block in blocks
        if getattr(block, "type", None) == "text"
        and isinstance(getattr(block, "text", None), str)
    ]
    if not texts:
        raise UpstreamToolError(
            BAD_RESPONSE,
            "Upstream response contained no JSON text.",
            False,
            SAFE_SLOT_ACTION,
        )
    return texts


def _is_error_result(result: Any) -> bool:
    return bool(
        getattr(result, "isError", False)
        or getattr(result, "is_error", False)
    )


def normalize_json_result(result: Any) -> dict[str, Any]:
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
    return payload
