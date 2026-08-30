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


MAX_XML_BYTES = 65_536
MAX_XML_DEPTH = 32
MAX_XML_ELEMENTS = 256
_EXTERNAL_URI = re.compile(r"(?:data|file|ftp|https?):", re.IGNORECASE)
_DOCTYPE = re.compile(rb"<!\s*doctype\b", re.IGNORECASE)
_NAMESPACE_DECLARATION = re.compile(
    rb"(?:\A|[\s<])xmlns(?::[A-Za-z_][\w.-]*)?\s*=", re.IGNORECASE
)
_XML_DECLARATION = re.compile(rb"\A[ \t\r\n]*<\?xml\b", re.IGNORECASE)
_DOCUMENT_TAG = "__asset_autopsy_document"
_SUPPORTED_ELEMENT = {
    "actuator",
    "body",
    "compiler",
    "geom",
    "joint",
    "mujoco",
    "option",
    "position",
    "worldbody",
}
_UNSAFE_ELEMENT = {
    "asset",
    "hfield",
    "include",
    "mesh",
    "plugin",
    "skin",
    "texture",
}
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


def _as_xml_bytes(xml: object) -> bytes:
    if not isinstance(xml, bytes):
        raise PatcherError("INVALID_XML", "MJCF source must be UTF-8 bytes")
    source = bytes(xml)
    if not source:
        raise PatcherError("INVALID_XML", "MJCF source is empty")
    if len(source) > MAX_XML_BYTES:
        raise PatcherError(
            "INVALID_XML", f"MJCF source exceeds the {MAX_XML_BYTES}-byte limit"
        )
    if source.startswith(b"\xef\xbb\xbf") or b"\x00" in source:
        raise PatcherError(
            "INVALID_XML", "MJCF source must be UTF-8 without a byte-order mark"
        )
    try:
        source.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise PatcherError("INVALID_XML", "MJCF source must be UTF-8 bytes") from None
    if _XML_DECLARATION.match(source):
        raise PatcherError("INVALID_XML", "XML declarations are not supported")
    if _NAMESPACE_DECLARATION.search(source):
        raise PatcherError("INVALID_XML", "XML namespaces are not supported")
    if _DOCTYPE.search(source):
        raise PatcherError(
            "UNSAFE_XML", "DOCTYPE and entity declarations are not supported"
        )
    return source


def _node_kind(tag: object) -> str:
    if tag is ET.Comment:
        return "<comment>"
    if tag is ET.ProcessingInstruction:
        return "<processing-instruction>"
    return tag if isinstance(tag, str) else ""


def _validate_tree(root: ET.Element) -> None:
    if root.tag != "mujoco":
        raise PatcherError(
            "INVALID_XML", "MJCF root must be a non-namespaced mujoco element"
        )
    element_count = 0
    pending = [(root, 1)]
    while pending:
        element, depth = pending.pop()
        element_count += 1
        if element_count > MAX_XML_ELEMENTS:
            raise PatcherError(
                "INVALID_XML", f"MJCF exceeds the {MAX_XML_ELEMENTS}-element limit"
            )
        if depth > MAX_XML_DEPTH:
            raise PatcherError(
                "INVALID_XML", f"MJCF exceeds the {MAX_XML_DEPTH}-level depth limit"
            )
        if not isinstance(element.tag, str) or element.tag.startswith("{"):
            raise PatcherError("INVALID_XML", "XML namespaces are not supported")
        if element.tag in _UNSAFE_ELEMENT:
            raise PatcherError("UNSAFE_XML", "external MJCF features are not supported")
        if element.tag not in _SUPPORTED_ELEMENT:
            raise PatcherError("INVALID_XML", "MJCF element is outside the fixture contract")
        for name, value in element.attrib.items():
            if name.startswith("{"):
                raise PatcherError("INVALID_XML", "XML namespaces are not supported")
            if name.lower() in _UNSAFE_ATTRIBUTE or _EXTERNAL_URI.search(value):
                raise PatcherError(
                    "UNSAFE_XML", "external MJCF references are not supported"
                )
        for child in element:
            if child.tag is ET.ProcessingInstruction:
                raise PatcherError(
                    "INVALID_XML", "processing instructions are not supported"
                )
            if isinstance(child.tag, str):
                pending.append((child, depth + 1))


def _parse_safe(source: bytes) -> ET.Element:
    wrapped_source = (
        f"<{_DOCUMENT_TAG}>".encode("ascii")
        + source
        + f"</{_DOCUMENT_TAG}>".encode("ascii")
    )
    try:
        wrapper_parser = ET.XMLParser(
            target=ET.TreeBuilder(insert_comments=True, insert_pis=True)
        )
        document_root = ET.fromstring(wrapped_source, parser=wrapper_parser)
    except ET.ParseError:
        raise PatcherError("INVALID_XML", "XML is not well formed") from None
    document_nodes = list(document_root)
    if (
        len(document_nodes) != 1
        or not isinstance(document_nodes[0].tag, str)
        or (document_root.text or "").strip()
        or (document_nodes[0].tail or "").strip()
    ):
        raise PatcherError("INVALID_XML", "MJCF must contain only one document element")
    root = document_nodes[0]
    _validate_tree(root)
    return root


def _serialize_document(root: ET.Element) -> bytes:
    return ET.tostring(root, encoding="utf-8", short_empty_elements=True)


def provision_fixture(xml: bytes) -> bytes:
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
    before_kind = _node_kind(before.tag)
    after_kind = _node_kind(after.tag)
    if before_kind != after_kind:
        changes.append(
            CanonicalChange(before_path, "<element>", before_kind, after_kind)
        )
        return
    before_attrs = dict(before.attrib)
    after_attrs = dict(after.attrib)
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
        changes.append(
            CanonicalChange(
                before_path,
                "<text>",
                _node_text(before.text),
                _node_text(after.text),
            )
        )
    if _node_text(before.tail) != _node_text(after.tail):
        changes.append(
            CanonicalChange(
                before_path,
                "<tail>",
                _node_text(before.tail),
                _node_text(after.tail),
            )
        )
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
    for index, (before_child, after_child) in enumerate(
        zip(before_children, after_children)
    ):
        child_name = _node_kind(before_child.tag)
        child_path = f"{before_path}/{child_name}[{index + 1}]"
        _compare_nodes(before_child, after_child, child_path, changes)


def canonical_document_diff(
    before_xml: bytes,
    after_xml: bytes,
) -> tuple[CanonicalChange, ...]:
    before_source = _as_xml_bytes(before_xml)
    after_source = _as_xml_bytes(after_xml)
    before_root = _parse_safe(before_source)
    after_root = _parse_safe(after_source)
    changes: list[CanonicalChange] = []
    _compare_nodes(before_root, after_root, "/mujoco", changes)
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


def _axis_matches(
    actual: tuple[float, float, float],
    expected: tuple[float, float, float],
) -> bool:
    return all(
        math.isclose(component, wanted, rel_tol=1e-12, abs_tol=1e-12)
        for component, wanted in zip(actual, expected)
    )


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
    base_xml: bytes,
    expected_base_sha256: str,
    patch: AttributePatch | Mapping[str, object],
) -> PatchedArtifact:
    source = _as_xml_bytes(base_xml)
    base_root = _parse_safe(source)
    actual_base_sha256 = hashlib.sha256(source).hexdigest()
    if not isinstance(expected_base_sha256, str) or not re.fullmatch(
        r"[0-9a-f]{64}", expected_base_sha256
    ):
        raise PatcherError("BASE_HASH_MISMATCH", "base hash is invalid")
    if actual_base_sha256 != expected_base_sha256:
        raise PatcherError("BASE_HASH_MISMATCH", "base hash does not match")
    validated_patch = _validate_patch(patch)
    revised_root = copy.deepcopy(base_root)
    target = validated_patch.target
    matches = [
        element
        for element in revised_root.iter()
        if element.tag == "joint" and element.attrib.get("name") == target.name
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
        if not _axis_matches(current_value, validated_patch.expected_old_value):
            raise PatcherError("OLD_VALUE_MISMATCH", "expected authored value does not match")
        if _axis_matches(current_value, validated_patch.new_value):
            raise PatcherError("NO_CHANGE", "patch does not change the authored document")
        replacement = _format_axis(validated_patch.new_value)
    else:
        current_value = _parse_finite_values(authored_value, 1)[0]
        if current_value != validated_patch.expected_old_value:
            raise PatcherError("OLD_VALUE_MISMATCH", "expected authored value does not match")
        if current_value == validated_patch.new_value:
            raise PatcherError("NO_CHANGE", "patch does not change the authored document")
        replacement = _format_float(validated_patch.new_value)
    joint.set(xml_attribute, replacement)
    changes: list[CanonicalChange] = []
    _compare_nodes(base_root, revised_root, "/mujoco", changes)
    if len(changes) != 1 or changes[0].attribute != xml_attribute:
        raise PatcherError("UNDECLARED_EDIT", "candidate changes more than the declared attribute")
    revised_xml = _serialize_document(revised_root)
    return PatchedArtifact(
        xml=revised_xml,
        base_sha256=actual_base_sha256,
        asset_sha256=hashlib.sha256(revised_xml).hexdigest(),
        canonical_diff=tuple(changes),
    )


def apply_patch(
    base_xml: bytes,
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
    "MAX_XML_DEPTH",
    "MAX_XML_ELEMENTS",
    "PatchedArtifact",
    "PatcherError",
    "apply_one_attribute_patch",
    "apply_patch",
    "canonical_document_diff",
    "provision_fixture",
]
