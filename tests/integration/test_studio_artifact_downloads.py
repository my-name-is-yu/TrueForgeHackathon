from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from pathlib import Path

from starlette.applications import Starlette

import character_robot.workbench as workbench_module
from character_robot.schemas import ArtifactDescriptor
from character_robot.service import CharacterRobotService
from character_robot.workbench import (
    ARTIFACT_DOWNLOAD_CHUNK_BYTES,
    STUDIO_SESSION_COOKIE,
    StudioSessionManager,
    create_studio_routes,
)


async def _seeded_app(
    root: Path,
    content: bytes,
    *,
    admission=None,
) -> tuple[StudioSessionManager, Starlette, str, ArtifactDescriptor, Path]:
    manager = StudioSessionManager(
        root=root / "sessions",
        service_factory=lambda path: CharacterRobotService(
            data_root=path,
            cad_compiler=None,
        ),
        artifact_download_admission=admission,
    )

    async def seed() -> tuple[str, ArtifactDescriptor, Path]:
        async with manager.lease(None) as (session_id, session, _created):
            descriptor = ArtifactDescriptor(
                kind="glb",
                file_name="slow-preview.glb",
                media_type="model/gltf-binary",
                sha256=hashlib.sha256(content).hexdigest(),
                byte_size=len(content),
                experimental=True,
            )
            session.service._artifacts.put(descriptor, content)
            return session_id, descriptor, session.data_root

    session_id, descriptor, data_root = await seed()
    app = Starlette(routes=create_studio_routes(manager))
    return manager, app, session_id, descriptor, data_root


async def _asgi_request(
    app: Starlette,
    path: str,
    session_id: str,
    *,
    method: str = "GET",
    asgi_spec: str = "2.4",
    receive: Callable[[], Awaitable[dict[str, object]]] | None = None,
    send_hook: Callable[[dict[str, object]], Awaitable[None]] | None = None,
) -> list[dict[str, object]]:
    messages: list[dict[str, object]] = []
    request_sent = False

    async def default_receive() -> dict[str, object]:
        nonlocal request_sent
        if request_sent:
            return {"type": "http.disconnect"}
        request_sent = True
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, object]) -> None:
        messages.append(message)
        if send_hook is not None:
            await send_hook(message)

    scope: dict[str, object] = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": asgi_spec},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"testserver"),
            (
                b"cookie",
                f"{STUDIO_SESSION_COOKIE}={session_id}".encode("ascii"),
            ),
        ],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }
    await app(scope, receive or default_receive, send)
    return messages


def _status(messages: list[dict[str, object]]) -> int:
    start = next(
        message for message in messages if message["type"] == "http.response.start"
    )
    return int(start["status"])


def _body(messages: list[dict[str, object]]) -> bytes:
    return b"".join(
        message["body"]
        for message in messages
        if message["type"] == "http.response.body"
    )


def test_slow_download_pins_generation_and_keeps_other_requests_live(
    tmp_path: Path,
) -> None:
    payload_size = ARTIFACT_DOWNLOAD_CHUNK_BYTES * 2 + 17
    content = bytes(index % 251 for index in range(payload_size))

    async def exercise() -> None:
        manager, app, session_id, descriptor, old_root = await _seeded_app(
            tmp_path,
            content,
        )
        first_chunk = asyncio.Event()
        release_body = asyncio.Event()

        async def hold_first_chunk(message: dict[str, object]) -> None:
            if (
                message["type"] == "http.response.body"
                and message["body"]
                and not first_chunk.is_set()
            ):
                first_chunk.set()
                await release_body.wait()

        download = asyncio.create_task(
            _asgi_request(
                app,
                f"/api/studio/v1/artifacts/{descriptor.sha256}",
                session_id,
                send_hook=hold_first_chunk,
            )
        )
        await asyncio.wait_for(first_chunk.wait(), timeout=1)
        assert old_root.is_dir()

        context = await asyncio.wait_for(
            _asgi_request(
                app,
                "/api/studio/v1/context",
                session_id,
            ),
            timeout=1,
        )
        assert _status(context) == 200

        reset = await asyncio.wait_for(
            _asgi_request(
                app,
                "/api/studio/v1/reset",
                session_id,
                method="POST",
            ),
            timeout=1,
        )
        assert _status(reset) == 200
        assert manager.sessions[session_id].data_root != old_root
        assert old_root.is_dir()

        release_body.set()
        messages = await asyncio.wait_for(download, timeout=2)
        assert _status(messages) == 200
        assert all(
            len(message["body"]) <= ARTIFACT_DOWNLOAD_CHUNK_BYTES
            for message in messages
            if message["type"] == "http.response.body" and message["body"]
        )
        assert _body(messages) == content
        assert not old_root.exists()
        assert not manager._download_generation_pins

    asyncio.run(exercise())


def test_download_admission_bounds_concurrent_streams_and_releases_on_success(
    tmp_path: Path,
) -> None:
    content = b"bounded artifact" * 10000

    async def exercise() -> None:
        manager, app, session_id, descriptor, _old_root = await _seeded_app(
            tmp_path,
            content,
            admission=workbench_module._ArtifactDownloadAdmission(1),
        )
        first_chunk = asyncio.Event()
        release_body = asyncio.Event()

        async def hold_first_chunk(message: dict[str, object]) -> None:
            if (
                message["type"] == "http.response.body"
                and message["body"]
                and not first_chunk.is_set()
            ):
                first_chunk.set()
                await release_body.wait()

        first = asyncio.create_task(
            _asgi_request(
                app,
                f"/api/studio/v1/artifacts/{descriptor.sha256}",
                session_id,
                send_hook=hold_first_chunk,
            )
        )
        await asyncio.wait_for(first_chunk.wait(), timeout=1)

        second = asyncio.create_task(
            _asgi_request(
                app,
                f"/api/studio/v1/artifacts/{descriptor.sha256}",
                session_id,
            )
        )
        await asyncio.sleep(0.05)
        assert not second.done()
        assert manager.sessions[session_id].service._artifacts._download_pins == {
            descriptor.sha256: 1
        }

        release_body.set()
        first_messages, second_messages = await asyncio.wait_for(
            asyncio.gather(first, second), timeout=2
        )
        assert _status(first_messages) == 200
        assert _status(second_messages) == 200
        assert _body(first_messages) == content
        assert _body(second_messages) == content
        assert all(
            len(message["body"]) <= ARTIFACT_DOWNLOAD_CHUNK_BYTES
            for message in first_messages + second_messages
            if message["type"] == "http.response.body" and message["body"]
        )
        assert not manager.sessions[session_id].service._artifacts._download_pins

    asyncio.run(exercise())


def test_download_admission_can_bound_multiple_managers_with_one_policy(
    tmp_path: Path,
) -> None:
    content = b"shared process admission" * 10000

    async def exercise() -> None:
        admission = workbench_module._ArtifactDownloadAdmission(1)
        (
            first_manager,
            first_app,
            first_session,
            first_descriptor,
            _,
        ) = await _seeded_app(tmp_path / "first", content, admission=admission)
        (
            second_manager,
            second_app,
            second_session,
            second_descriptor,
            _,
        ) = await _seeded_app(tmp_path / "second", content, admission=admission)
        assert first_manager.artifact_download_admission is admission
        assert second_manager.artifact_download_admission is admission

        first_chunk = asyncio.Event()
        release_body = asyncio.Event()

        async def hold_first_chunk(message: dict[str, object]) -> None:
            if (
                message["type"] == "http.response.body"
                and message["body"]
                and not first_chunk.is_set()
            ):
                first_chunk.set()
                await release_body.wait()

        first = asyncio.create_task(
            _asgi_request(
                first_app,
                f"/api/studio/v1/artifacts/{first_descriptor.sha256}",
                first_session,
                send_hook=hold_first_chunk,
            )
        )
        await asyncio.wait_for(first_chunk.wait(), timeout=1)
        second = asyncio.create_task(
            _asgi_request(
                second_app,
                f"/api/studio/v1/artifacts/{second_descriptor.sha256}",
                second_session,
            )
        )
        await asyncio.sleep(0.05)
        assert not second.done()

        release_body.set()
        first_messages, second_messages = await asyncio.wait_for(
            asyncio.gather(first, second), timeout=2
        )
        assert _status(first_messages) == 200
        assert _status(second_messages) == 200
        assert _body(first_messages) == content
        assert _body(second_messages) == content

    asyncio.run(exercise())


def test_default_download_admission_is_shared_across_managers(tmp_path: Path) -> None:
    def factory(path: Path) -> CharacterRobotService:
        return CharacterRobotService(data_root=path, cad_compiler=None)

    first = StudioSessionManager(root=tmp_path / "first", service_factory=factory)
    second = StudioSessionManager(root=tmp_path / "second", service_factory=factory)

    assert first.artifact_download_admission is second.artifact_download_admission


def test_download_releases_admission_and_pins_when_client_disconnects(
    tmp_path: Path,
) -> None:
    content = b"disconnect-safe artifact" * 10000

    async def exercise() -> None:
        manager, app, session_id, descriptor, _old_root = await _seeded_app(
            tmp_path,
            content,
            admission=workbench_module._ArtifactDownloadAdmission(1),
        )
        first_chunk = asyncio.Event()
        disconnect = asyncio.Event()
        request_sent = False
        never_release = asyncio.Event()

        async def receive() -> dict[str, object]:
            nonlocal request_sent
            if not request_sent:
                request_sent = True
                return {"type": "http.request", "body": b"", "more_body": False}
            await disconnect.wait()
            return {"type": "http.disconnect"}

        async def block_body(message: dict[str, object]) -> None:
            if (
                message["type"] == "http.response.body"
                and message["body"]
                and not first_chunk.is_set()
            ):
                first_chunk.set()
                await never_release.wait()

        cancelled = asyncio.create_task(
            _asgi_request(
                app,
                f"/api/studio/v1/artifacts/{descriptor.sha256}",
                session_id,
                asgi_spec="2.0",
                receive=receive,
                send_hook=block_body,
            )
        )
        await asyncio.wait_for(first_chunk.wait(), timeout=1)
        disconnect.set()
        await asyncio.wait_for(cancelled, timeout=2)

        assert not manager._download_generation_pins
        assert not manager.sessions[session_id].service._artifacts._download_pins

        follow_up = await asyncio.wait_for(
            _asgi_request(
                app,
                f"/api/studio/v1/artifacts/{descriptor.sha256}",
                session_id,
            ),
            timeout=2,
        )
        assert _status(follow_up) == 200
        assert _body(follow_up) == content

    asyncio.run(exercise())
