from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .schemas import ArtifactRef
from .storage import canonical_json_bytes


class PublicationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PublicationBundle:
    artifacts: tuple[ArtifactRef, ...]
    manifest_sha256: str
    created: bool


class Publisher:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._publication_count = 0

    @property
    def publication_count(self) -> int:
        return self._publication_count

    @property
    def public_artifact_count(self) -> int:
        return self._publication_count * 4

    def publish(
        self,
        *,
        case_id: str,
        revision_id: str,
        repaired_mjcf: bytes,
        patch_manifest: Mapping[str, object],
        qualification: Mapping[str, object],
        evidence_ledger: object,
    ) -> PublicationBundle:
        payloads = {
            "repaired.mjcf": bytes(repaired_mjcf),
            "patch-manifest.json": canonical_json_bytes(patch_manifest),
            "qualification.json": canonical_json_bytes(qualification),
            "evidence-ledger.json": canonical_json_bytes(evidence_ledger),
        }
        manifest = {
            name: {
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
            }
            for name, data in sorted(payloads.items())
        }
        manifest_sha256 = hashlib.sha256(canonical_json_bytes(manifest)).hexdigest()
        destination = self.root / case_id / revision_id
        if destination.exists():
            self._verify_existing(destination, payloads)
            return PublicationBundle(
                artifacts=_artifact_refs(case_id, revision_id, payloads),
                manifest_sha256=manifest_sha256,
                created=False,
            )

        self.root.mkdir(parents=True, exist_ok=True)
        case_root = self.root / case_id
        case_root.mkdir(exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=f".{revision_id}-", dir=case_root))
        try:
            for name, data in payloads.items():
                path = temporary / name
                with path.open("xb") as output:
                    output.write(data)
                    output.flush()
                    os.fsync(output.fileno())
            os.replace(temporary, destination)
            temporary = Path()
            _fsync_directory(case_root)
        except (OSError, FileExistsError) as exc:
            raise PublicationError(
                "publication bundle could not be materialized"
            ) from exc
        finally:
            if temporary != Path() and temporary.exists():
                shutil.rmtree(temporary)
        self._verify_existing(destination, payloads)
        self._publication_count += 1
        return PublicationBundle(
            artifacts=_artifact_refs(case_id, revision_id, payloads),
            manifest_sha256=manifest_sha256,
            created=True,
        )

    def read_artifact(self, case_id: str, revision_id: str, name: str) -> bytes:
        if name not in {
            "repaired.mjcf",
            "patch-manifest.json",
            "qualification.json",
            "evidence-ledger.json",
        }:
            raise PublicationError("publication artifact name is invalid")
        try:
            return (self.root / case_id / revision_id / name).read_bytes()
        except OSError as exc:
            raise PublicationError("publication artifact cannot be read") from exc

    def load_existing(self, case_id: str, revision_id: str) -> PublicationBundle:
        payloads = {
            name: self.read_artifact(case_id, revision_id, name)
            for name in (
                "repaired.mjcf",
                "patch-manifest.json",
                "qualification.json",
                "evidence-ledger.json",
            )
        }
        self._verify_existing(self.root / case_id / revision_id, payloads)
        manifest = {
            name: {
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
            }
            for name, data in sorted(payloads.items())
        }
        return PublicationBundle(
            artifacts=_artifact_refs(case_id, revision_id, payloads),
            manifest_sha256=hashlib.sha256(canonical_json_bytes(manifest)).hexdigest(),
            created=False,
        )

    @staticmethod
    def _verify_existing(destination: Path, payloads: Mapping[str, bytes]) -> None:
        if not destination.is_dir():
            raise PublicationError("publication destination is invalid")
        names = {path.name for path in destination.iterdir() if path.is_file()}
        if names != set(payloads):
            raise PublicationError("publication bundle is incomplete")
        for name, expected in payloads.items():
            try:
                actual = (destination / name).read_bytes()
            except OSError as exc:
                raise PublicationError("publication artifact cannot be read") from exc
            if actual != expected:
                raise PublicationError("publication artifact failed verification")


def _artifact_refs(
    case_id: str, revision_id: str, payloads: Mapping[str, bytes]
) -> tuple[ArtifactRef, ...]:
    metadata = {
        "repaired.mjcf": ("repaired_mjcf", "application/xml"),
        "patch-manifest.json": ("patch_manifest", "application/json"),
        "qualification.json": ("qualification", "application/json"),
        "evidence-ledger.json": ("evidence_ledger", "application/json"),
    }
    refs = []
    for name in (
        "repaired.mjcf",
        "patch-manifest.json",
        "qualification.json",
        "evidence-ledger.json",
    ):
        data = payloads[name]
        digest = hashlib.sha256(data).hexdigest()
        kind, media_type = metadata[name]
        refs.append(
            ArtifactRef(
                artifact_id=f"art_{digest[:24]}",
                kind=kind,
                uri=f"autopsy://publications/{case_id}/{revision_id}/{name}",
                media_type=media_type,
                sha256=digest,
                bytes=len(data),
            )
        )
    return tuple(refs)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


__all__ = ["PublicationBundle", "PublicationError", "Publisher"]
