from __future__ import annotations

import copy
import hashlib
import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Mapping

from pydantic import TypeAdapter, ValidationError

from .schemas import AttributePatch, AxisPatch, ScalarPatch


MAX_XML_BYTES = 1_048_576
_UNSAFE_DECLARATION = re.compile(r"<!\s*(?:doctype|entity)\b", re.IGNORECASE)
_UNSAFE_ELEMENT = re.compile(
    r"<\s*(?:[A-Za-z_][\w.-]*:)?(?:include|plugin|mesh|texture|hfield|skin)\b",
    re.IGNORECASE,
)
_UNKNOWN_ENTITY = re.compile(r"&(?!(?:amp|lt|gt|quot|apos);)[A-Za-z_][A-Za-z0-9_.-]*;")
_EXTERNAL_URI = re.compile(r"(?:data|file|ftp|https?):", re.IGNORECASE)
_UNSAFE_ATTRIBUTE = {
    "contentdir",
    "file",
    "filename",
    "filepath",
    "href",
    "meshdir",
    "path",
    "texturedir",
    "uri",
    "url",
}


class PatcherError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class CanonicalChange:
    path: str
    attribute: str
    before: str
    after: str


@dataclass(frozen=True)
class PatchedArtifact:
    xml: bytes
    base_sha256: str
    asset_sha256: str
    canonical_diff: tuple[CanonicalChange, ...]


def _as_xml_bytes(xml: bytes | str) -> bytes:
    if isinstance(xml, str):
        source = xml.encode("utf-8")
    elif isinstance(xml, bytes):
        source = bytes(xml)
    else:
        raise PatcherError("INVALID_XML", "XML must be bytes or text")
    if not source or len(source) > MAX_XML_BYTES or b"\x00" in source:
        raise PatcherError("INVALID_XML", "XML is empty or outside the size limit")
    return source


def _local_name(tag: object) -> str:
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


def _reject_external_features(source: bytes) -> None:
    text = source.decode("utf-8", errors="strict")
    if _UNSAFE_DECLARATION.search(text):
        raise PatcherError("UNSAFE_XML", "XML declarations are not allowed")
    if _UNSAFE_ELEMENT.search(text) or _UNKNOWN_ENTITY.search(text):
        raise PatcherError("UNSAFE_XML", "external XML features are not allowed")
    if _EXTERNAL_URI.search(text):
        raise PatcherError("UNSAFE_XML", "external XML references are not allowed")


def _parse_safe(xml: bytes | str) -> ET.Element:
    source = _as_xml_bytes(xml)
    try:
        _reject_external_features(source)
    except UnicodeDecodeError:
        raise PatcherError("INVALID_XML", "XML must be UTF-8") from None
    try:
        root = ET.fromstring(source)
    except ET.ParseError:
        raise PatcherError("INVALID_XML", "XML is not well formed") from None
    if _local_name(root.tag) != "mujoco":
        raise PatcherError("INVALID_XML", "XML root must be mujoco")
    for element in root.iter():
        if _local_name(element.tag) in {"include", "plugin", "mesh", "texture", "hfield", "skin"}:
            raise PatcherError("UNSAFE_XML", "external XML features are not allowed")
        for name, value in element.attrib.items():
            local_name = _local_name(name).lower()
            if local_name in _UNSAFE_ATTRIBUTE or _EXTERNAL_URI.search(value):
                raise PatcherError("UNSAFE_XML", "external XML references are not allowed")
    return root


def canonicalize_xml(xml: bytes | str) -> bytes:
    source = _as_xml_bytes(xml)
    _parse_safe(source)
    try:
        canonical = ET.canonicalize(xml_data=source, with_comments=True, strip_text=False)
    except (ET.ParseError, UnicodeDecodeError):
        raise PatcherError("INVALID_XML", "XML cannot be canonicalized") from None
    return canonical.encode("utf-8")


def provision_fixture(xml: bytes | str) -> bytes:
    source = _as_xml_bytes(xml)
    _parse_safe(source)
    return source


def _display_value(value: object) -> str:
    if value is None:
        return "<missing>"
    return str(value)


def _node_text(value: str | None) -> str:
    return "" if value is None else value


def _compare_nodes(
    before: ET.Element,
    after: ET.Element,
    before_path: str,
    changes: list[CanonicalChange],
) -> None:
    if _local_name(before.tag) != _local_name(after.tag):
        changes.append(
            CanonicalChange(before_path, "<element>", _local_name(before.tag), _local_name(after.tag))
        )
        return
    before_attrs = {_local_name(k): v for k, v in before.attrib.items()}
    after_attrs = {_local_name(k): v for k, v in after.attrib.items()}
    for attribute in sorted(set(before_attrs) | set(after_attrs)):
        if before_attrs.get(attribute) != after_attrs.get(attribute):
            changes.append(
                CanonicalChange(
                    before_path,
                    attribute,
                    _display_value(before_attrs.get(attribute)),
                    _display_value(after_attrs.get(attribute)),
                )
            )
    if _node_text(before.text) != _node_text(after.text):
        changes.append(CanonicalChange(before_path, "<text>", _node_text(before.text), _node_text(after.text)))
    if _node_text(before.tail) != _node_text(after.tail):
        changes.append(CanonicalChange(before_path, "<tail>", _node_text(before.tail), _node_text(after.tail)))
    before_children = list(before)
    after_children = list(after)
    if len(before_children) != len(after_children):
        changes.append(
            CanonicalChange(
                before_path,
                "<children>",
                str(len(before_children)),
                str(len(after_children)),
            )
        )
        return
    for index, (before_child, after_child) in enumerate(zip(before_children, after_children)):
        child_name = _local_name(before_child.tag)
        child_path = f"{before_path}/{child_name}[{index + 1}]"
        _compare_nodes(before_child, after_child, child_path, changes)


def canonical_document_diff(
    before_xml: bytes | str,
    after_xml: bytes | str,
) -> tuple[CanonicalChange, ...]:
    before_source = _as_xml_bytes(before_xml)
    after_source = _as_xml_bytes(after_xml)
    before_root = _parse_safe(before_source)
    after_root = _parse_safe(after_source)
    changes: list[CanonicalChange] = []
    _compare_nodes(before_root, after_root, "/mujoco", changes)
    if not changes and canonicalize_xml(before_source) != canonicalize_xml(after_source):
        changes.append(CanonicalChange("/mujoco", "<document>", "different", "different"))
    return tuple(changes)


def _parse_finite_values(value: str, expected_count: int) -> tuple[float, ...]:
    tokens = value.split()
    if len(tokens) != expected_count:
        raise PatcherError("OLD_VALUE_MISMATCH", "stored attribute has an invalid shape")
    try:
        parsed = tuple(float(token) for token in tokens)
    except ValueError:
        raise PatcherError("OLD_VALUE_MISMATCH", "stored attribute is not numeric") from None
    if not all(math.isfinite(item) for item in parsed):
        raise PatcherError("OLD_VALUE_MISMATCH", "stored attribute is not finite")
    return parsed


def _normalize_axis(value: tuple[float, float, float]) -> tuple[float, float, float]:
    length = math.sqrt(sum(component * component for component in value))
    if length <= 0.0 or not math.isfinite(length):
        raise PatcherError("OLD_VALUE_MISMATCH", "axis must be non-zero and finite")
    return tuple(component / length for component in value)


def _format_float(value: float) -> str:
    return "0" if value == 0.0 else repr(value)


def _format_axis(value: tuple[float, float, float]) -> str:
    return " ".join(_format_float(component) for component in value)


def _validate_patch(patch: AttributePatch | Mapping[str, object]) -> AxisPatch | ScalarPatch:
    try:
        return TypeAdapter(AttributePatch).validate_python(patch)
    except ValidationError:
        raise PatcherError("PATCH_NOT_ALLOWED", "patch does not match the frozen contract") from None


def apply_one_attribute_patch(
    *,
    base_xml: bytes | str,
    expected_base_sha256: str,
    patch: AttributePatch | Mapping[str, object],
) -> PatchedArtifact:
    source = _as_xml_bytes(base_xml)
    actual_base_sha256 = hashlib.sha256(source).hexdigest()
    if not isinstance(expected_base_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_base_sha256):
        raise PatcherError("BASE_HASH_MISMATCH", "base hash is invalid")
    if actual_base_sha256 != expected_base_sha256:
        raise PatcherError("BASE_HASH_MISMATCH", "base hash does not match")
    validated_patch = _validate_patch(patch)
    base_root = _parse_safe(source)
    revised_root = copy.deepcopy(base_root)
    target = validated_patch.target
    matches = [
        element
        for element in revised_root.iter()
        if _local_name(element.tag) == "joint" and element.attrib.get("name") == target.name
    ]
    if len(matches) != 1:
        raise PatcherError("SELECTOR_MISMATCH", "patch selector did not match exactly one joint")
    joint = matches[0]
    xml_attribute = validated_patch.attribute
    if xml_attribute not in joint.attrib:
        raise PatcherError("OLD_VALUE_MISMATCH", "expected authored attribute is missing")
    authored_value = joint.attrib[xml_attribute]
    if isinstance(validated_patch, AxisPatch):
        current_value = _normalize_axis(_parse_finite_values(authored_value, 3))
        if current_value != validated_patch.expected_old_value:
            raise PatcherError("OLD_VALUE_MISMATCH", "expected authored value does not match")
        replacement = _format_axis(validated_patch.new_value)
    else:
        current_value = _parse_finite_values(authored_value, 1)[0]
        if current_value != validated_patch.expected_old_value:
            raise PatcherError("OLD_VALUE_MISMATCH", "expected authored value does not match")
        replacement = _format_float(validated_patch.new_value)
    if authored_value == replacement:
        raise PatcherError("NO_CHANGE", "patch does not change the authored document")
    joint.set(xml_attribute, replacement)
    revised_xml = ET.tostring(revised_root, encoding="utf-8", short_empty_elements=True)
    changes = canonical_document_diff(source, revised_xml)
    if len(changes) != 1 or changes[0].attribute != xml_attribute:
        raise PatcherError("UNDECLARED_EDIT", "candidate changes more than the declared attribute")
    if canonicalize_xml(source) == canonicalize_xml(revised_xml):
        raise PatcherError("NO_CHANGE", "patch does not change the canonical document")
    return PatchedArtifact(
        xml=revised_xml,
        base_sha256=actual_base_sha256,
        asset_sha256=hashlib.sha256(revised_xml).hexdigest(),
        canonical_diff=changes,
    )


def apply_patch(
    base_xml: bytes | str,
    patch: AttributePatch | Mapping[str, object],
    *,
    expected_base_sha256: str,
) -> PatchedArtifact:
    return apply_one_attribute_patch(
        base_xml=base_xml,
        expected_base_sha256=expected_base_sha256,
        patch=patch,
    )


__all__ = [
    "CanonicalChange",
    "MAX_XML_BYTES",
    "PatchedArtifact",
    "PatcherError",
    "apply_one_attribute_patch",
    "apply_patch",
    "canonical_document_diff",
    "canonicalize_xml",
    "provision_fixture",
]
