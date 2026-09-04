from __future__ import annotations

import hashlib

import pytest

from character_robot.artifacts import ArtifactStoreError, SessionArtifactStore
from character_robot.schemas import ArtifactDescriptor


def _artifact(content: bytes, *, name: str = "preview.glb") -> ArtifactDescriptor:
    return ArtifactDescriptor(
        kind="glb",
        file_name=name,
        media_type="model/gltf-binary",
        sha256=hashlib.sha256(content).hexdigest(),
        byte_size=len(content),
        experimental=True,
    )


def test_session_artifacts_are_content_addressed_and_verified(tmp_path) -> None:
    store = SessionArtifactStore(tmp_path)
    content = b"glTF payload"
    descriptor = _artifact(content)

    store.put(descriptor, content)

    assert store.read(descriptor.sha256) == (content, descriptor)
    assert store.objects.path_for(descriptor.sha256).is_file()


def test_session_artifacts_reject_descriptor_mismatch(tmp_path) -> None:
    store = SessionArtifactStore(tmp_path)
    descriptor = _artifact(b"expected")

    with pytest.raises(ArtifactStoreError, match="byte size"):
        store.put(descriptor, b"different payload")


def test_session_artifacts_evict_oldest_payload_within_private_budget(tmp_path) -> None:
    store = SessionArtifactStore(tmp_path, maximum_artifacts=2, maximum_bytes=8)
    first = _artifact(b"1111", name="first.glb")
    second = _artifact(b"22", name="second.glb")
    third = _artifact(b"33", name="third.glb")

    store.put(first, b"1111")
    store.put(second, b"22")
    store.put(third, b"33")

    assert first.sha256 not in store
    assert not store.objects.path_for(first.sha256).exists()
    assert store.read(second.sha256)[0] == b"22"
    assert store.read(third.sha256)[0] == b"33"


def test_session_artifacts_drop_corrupt_payload_on_read(tmp_path) -> None:
    store = SessionArtifactStore(tmp_path)
    descriptor = _artifact(b"correct")
    store.put(descriptor, b"correct")
    store.objects.path_for(descriptor.sha256).write_bytes(b"corrupt")

    with pytest.raises(ArtifactStoreError, match="integrity"):
        store.read(descriptor.sha256)

    assert descriptor.sha256 not in store


def test_session_artifact_download_pin_survives_budget_eviction(tmp_path) -> None:
    store = SessionArtifactStore(tmp_path, maximum_artifacts=1, maximum_bytes=4)
    first = _artifact(b"1111", name="first.glb")
    second = _artifact(b"2222", name="second.glb")
    store.put(first, b"1111")

    download = store.prepare_download(first.sha256)
    try:
        store.put(second, b"2222")
        assert first.sha256 in store
        assert second.sha256 in store
        assert store._eviction_pending is True
        assert download.source.read() == b"1111"
    finally:
        download.close()

    assert first.sha256 not in store
    assert second.sha256 in store
    assert store.read(second.sha256)[0] == b"2222"
    assert store._eviction_pending is False


def test_session_artifact_display_pin_survives_budget_pressure_until_release(
    tmp_path,
) -> None:
    store = SessionArtifactStore(tmp_path, maximum_artifacts=2, maximum_bytes=8)
    first = _artifact(b"1111", name="first.glb")
    second = _artifact(b"2222", name="second.glb")
    third = _artifact(b"3333", name="third.glb")

    store.put(first, b"1111")
    store.put(second, b"2222")
    store.reserve_display_pins([first.sha256, second.sha256])

    store.put(third, b"3333")

    assert first.sha256 in store
    assert second.sha256 in store
    assert third.sha256 in store
    assert store._eviction_pending is True
    assert store.read(first.sha256)[0] == b"1111"
    assert store.read(second.sha256)[0] == b"2222"
    assert store.read(third.sha256)[0] == b"3333"

    store.release_display_pins()

    assert first.sha256 not in store
    assert second.sha256 in store
    assert third.sha256 in store
    assert store._eviction_pending is False


def test_session_artifact_compile_batch_pin_is_replaced_without_leaking(
    tmp_path,
) -> None:
    store = SessionArtifactStore(tmp_path, maximum_artifacts=2, maximum_bytes=8)
    first = _artifact(b"1111", name="first.glb")
    second = _artifact(b"2222", name="second.glb")
    third = _artifact(b"3333", name="third.glb")

    store.put(first, b"1111")
    store.put(second, b"2222")
    store.replace_compile_pins([first.sha256, second.sha256])

    store.reserve_compile_pins([third.sha256])
    store.put(third, b"3333")
    assert store.compile_pins == frozenset({first.sha256, second.sha256, third.sha256})
    assert store._eviction_pending is True

    # An interrupted replacement restores the prior batch and drops its
    # partial candidate once the old batch is the only protected set.
    store.replace_compile_pins([first.sha256, second.sha256])
    assert third.sha256 not in store
    assert store.compile_pins == frozenset({first.sha256, second.sha256})
    assert store._eviction_pending is False

    store.reserve_compile_pins([third.sha256])
    store.put(third, b"3333")
    store.replace_compile_pins([third.sha256])
    assert store.compile_pins == frozenset({third.sha256})
    assert first.sha256 not in store
    assert second.sha256 in store
    assert third.sha256 in store
    assert store._eviction_pending is False


def test_session_artifact_download_defers_drop_until_close(tmp_path) -> None:
    store = SessionArtifactStore(tmp_path)
    descriptor = _artifact(b"pinned")
    store.put(descriptor, b"pinned")
    download = store.prepare_download(descriptor.sha256)

    try:
        store._drop(descriptor.sha256)
        assert descriptor.sha256 in store
        assert store.objects.path_for(descriptor.sha256).is_file()
    finally:
        download.close()

    assert descriptor.sha256 not in store
    assert not store.objects.path_for(descriptor.sha256).exists()


def test_session_artifact_download_rejects_corrupt_payload_without_a_pin(
    tmp_path,
) -> None:
    store = SessionArtifactStore(tmp_path)
    descriptor = _artifact(b"correct")
    store.put(descriptor, b"correct")
    store.objects.path_for(descriptor.sha256).write_bytes(b"corrupt")

    with pytest.raises(ArtifactStoreError, match="integrity"):
        store.prepare_download(descriptor.sha256)

    assert descriptor.sha256 not in store
    assert not store._download_pins


def test_session_artifacts_reject_payload_larger_than_budget_before_storage(
    tmp_path,
) -> None:
    store = SessionArtifactStore(tmp_path, maximum_bytes=3)
    descriptor = _artifact(b"1234")

    with pytest.raises(ArtifactStoreError, match="byte budget"):
        store.put(descriptor, b"1234")

    assert descriptor.sha256 not in store
    assert not store.objects.path_for(descriptor.sha256).exists()


def test_restore_replaces_the_descriptor_index_and_deletes_unindexed_objects(
    tmp_path,
) -> None:
    store = SessionArtifactStore(tmp_path)
    retained = _artifact(b"retained", name="retained.glb")
    discarded = _artifact(b"discarded", name="discarded.glb")
    store.put(retained, b"retained")
    store.put(discarded, b"discarded")
    interrupted_temporary = store.objects.hash_root / ".tmp-interrupted"
    interrupted_temporary.write_bytes(b"partial")

    store.restore([retained])

    assert store.artifact_count == 1
    assert store.total_bytes == retained.byte_size
    assert store.read(retained.sha256) == (b"retained", retained)
    with pytest.raises(ArtifactStoreError, match="not indexed"):
        store.read(discarded.sha256)
    assert not store.objects.path_for(discarded.sha256).exists()
    assert not interrupted_temporary.exists()


def test_restore_refuses_to_sweep_through_a_symlinked_object_root(tmp_path) -> None:
    session_root = tmp_path / "session"
    outside_root = tmp_path / "outside"
    victim_contents = (b"outside-1", b"outside-2")
    victims = tuple(_artifact(content) for content in victim_contents)
    victim_paths = tuple(
        outside_root / "sha256" / victim.sha256[:2] / victim.sha256
        for victim in victims
    )
    for content, victim_path in zip(victim_contents, victim_paths, strict=True):
        victim_path.parent.mkdir(parents=True, exist_ok=True)
        victim_path.write_bytes(content)
    session_root.mkdir()
    (session_root / "objects").symlink_to(outside_root, target_is_directory=True)

    store = SessionArtifactStore(session_root, maximum_artifacts=1)

    with pytest.raises(ArtifactStoreError, match="object root is invalid"):
        store.restore(list(victims))

    assert all(path.is_file() for path in victim_paths)
