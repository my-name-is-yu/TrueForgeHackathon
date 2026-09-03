from __future__ import annotations

import argparse
import os
import re
from pathlib import Path
from typing import Sequence

from .cad import CadCompiler
from .cad_jobs import (
    CadJobFailure,
    decode_cad_job_request,
    encode_cad_job_failure,
    encode_cad_job_success,
)


_ERROR_CODE = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")


def _bounded_failure(error: Exception) -> CadJobFailure:
    code = getattr(error, "code", "CAD_JOB_INTERNAL_ERROR")
    message = getattr(
        error,
        "safe_message",
        "The isolated CAD worker could not compile this design.",
    )
    retryable = bool(getattr(error, "retryable", False))
    details = getattr(error, "details", {})
    if not isinstance(code, str) or _ERROR_CODE.fullmatch(code) is None:
        code = "CAD_JOB_INTERNAL_ERROR"
    if (
        not isinstance(message, str)
        or not message
        or any(character in message for character in ("\x00", "\n", "\r"))
    ):
        message = "The isolated CAD worker could not compile this design."
    if not isinstance(details, dict):
        details = {}
    return CadJobFailure(
        code=code,
        safe_message=message[:240],
        retryable=retryable,
        details=details,
    )


def _set_resource_limit(resource: object, kind: int, value: int) -> None:
    _current_soft, current_hard = resource.getrlimit(kind)  # type: ignore[attr-defined]
    infinity = resource.RLIM_INFINITY  # type: ignore[attr-defined]
    target = value if current_hard == infinity else min(value, current_hard)
    resource.setrlimit(kind, (target, target))  # type: ignore[attr-defined]


def _apply_resource_limits(
    *,
    cpu_seconds: int,
    max_file_bytes: int,
    max_open_files: int,
    memory_bytes: int | None,
) -> None:
    try:
        import resource
    except ImportError:
        return
    _set_resource_limit(resource, resource.RLIMIT_CPU, cpu_seconds)
    _set_resource_limit(resource, resource.RLIMIT_FSIZE, max_file_bytes)
    _set_resource_limit(resource, resource.RLIMIT_NOFILE, max_open_files)
    if memory_bytes is not None and hasattr(resource, "RLIMIT_AS"):
        _set_resource_limit(resource, resource.RLIMIT_AS, memory_bytes)


def _write_atomic(path: Path, content: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("xb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--response", type=Path, required=True)
    parser.add_argument("--cpu-seconds", type=int, required=True)
    parser.add_argument("--max-file-bytes", type=int, required=True)
    parser.add_argument("--max-open-files", type=int, required=True)
    parser.add_argument("--memory-bytes", type=int)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    _apply_resource_limits(
        cpu_seconds=arguments.cpu_seconds,
        max_file_bytes=arguments.max_file_bytes,
        max_open_files=arguments.max_open_files,
        memory_bytes=arguments.memory_bytes,
    )
    try:
        spec, profile = decode_cad_job_request(arguments.request.read_bytes())
        result = CadCompiler().compile(spec, profile)
        response = encode_cad_job_success(result)
    except Exception as error:
        response = encode_cad_job_failure(_bounded_failure(error))
    _write_atomic(arguments.response, response)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main"]
