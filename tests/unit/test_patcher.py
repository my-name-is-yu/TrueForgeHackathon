import hashlib

import pytest

from asset_autopsy.patcher import (
    PatcherError,
    apply_one_attribute_patch,
    canonical_document_diff,
    provision_fixture,
)


BASE_XML = b"""<mujoco>
  <worldbody>
    <body name="arm">
      <joint name="elbow" axis="0 0 2" damping="0.3" armature="0.01" frictionloss="0"/>
    </body>
  </worldbody>
</mujoco>"""


def _base_hash() -> str:
    return hashlib.sha256(BASE_XML).hexdigest()


def test_scalar_patch_is_copy_on_write_and_has_one_canonical_change() -> None:
    result = apply_one_attribute_patch(
        base_xml=BASE_XML,
        expected_base_sha256=_base_hash(),
        patch={
            "target": {"kind": "joint", "name": "elbow"},
            "attribute": "damping",
            "expected_old_value": 0.3,
            "new_value": 0.5,
        },
    )

    assert BASE_XML == b"""<mujoco>
  <worldbody>
    <body name="arm">
      <joint name="elbow" axis="0 0 2" damping="0.3" armature="0.01" frictionloss="0"/>
    </body>
  </worldbody>
</mujoco>"""
    assert len(result.canonical_diff) == 1
    assert result.canonical_diff[0].attribute == "damping"
    assert result.canonical_diff[0].before == "0.3"
    assert result.canonical_diff[0].after == "0.5"
    assert b'damping="0.5"' in result.xml
    assert b'axis="0 0 2"' in result.xml


def test_axis_patch_normalizes_the_new_value_without_editing_other_attributes() -> None:
    result = apply_one_attribute_patch(
        base_xml=BASE_XML,
        expected_base_sha256=_base_hash(),
        patch={
            "target": {"kind": "joint", "name": "elbow"},
            "attribute": "axis",
            "expected_old_value": [0, 0, 1],
            "new_value": [0, 4, 0],
        },
    )

    assert len(result.canonical_diff) == 1
    assert result.canonical_diff[0].attribute == "axis"
    assert b'axis="0 1.0 0"' in result.xml
    assert b'damping="0.3"' in result.xml


def test_patch_preserves_comments_and_processing_instructions() -> None:
    xml = b"""<mujoco>
  <!-- preserve this comment -->
  <?fixture keep?>
  <worldbody><body name="arm"><joint name="elbow" damping="0.3"/></body></worldbody>
</mujoco>"""
    result = apply_one_attribute_patch(
        base_xml=xml,
        expected_base_sha256=hashlib.sha256(xml).hexdigest(),
        patch={
            "target": {"kind": "joint", "name": "elbow"},
            "attribute": "damping",
            "expected_old_value": 0.3,
            "new_value": 0.5,
        },
    )

    assert b"<!-- preserve this comment -->" in result.xml
    assert b"<?fixture keep?>" in result.xml
    assert len(result.canonical_diff) == 1
    assert result.canonical_diff[0].attribute == "damping"


def test_axis_expected_value_allows_only_normalization_roundoff() -> None:
    component = 1.0 / (2.0**0.5)
    xml = BASE_XML.replace(b'axis="0 0 2"', b'axis="1 1 0"')
    result = apply_one_attribute_patch(
        base_xml=xml,
        expected_base_sha256=hashlib.sha256(xml).hexdigest(),
        patch={
            "target": {"kind": "joint", "name": "elbow"},
            "attribute": "axis",
            "expected_old_value": [component, component, 0.0],
            "new_value": [0.0, 4.0, 0.0],
        },
    )

    assert result.canonical_diff[0].attribute == "axis"

    with pytest.raises(PatcherError) as exc_info:
        apply_one_attribute_patch(
            base_xml=xml,
            expected_base_sha256=hashlib.sha256(xml).hexdigest(),
            patch={
                "target": {"kind": "joint", "name": "elbow"},
                "attribute": "axis",
                "expected_old_value": [1.0, 0.0, 0.0],
                "new_value": [0.0, 4.0, 0.0],
            },
        )
    assert exc_info.value.code == "OLD_VALUE_MISMATCH"


@pytest.mark.parametrize(
    ("xml", "expected_code"),
    [
        (b"<mujoco><include file=\"fixture.xml\"/></mujoco>", "UNSAFE_XML"),
        (b"<!DOCTYPE mujoco [<!ENTITY x \"external\">]><mujoco/>", "UNSAFE_XML"),
        (b"<mujoco><asset><mesh file=\"mesh.stl\"/></asset></mujoco>", "UNSAFE_XML"),
        (b"<mujoco><plugin plugin=\"external\"/></mujoco>", "UNSAFE_XML"),
        (b"<mujoco><worldbody><body path=\"../secret\"/></worldbody></mujoco>", "UNSAFE_XML"),
    ],
)
def test_fixture_provisioning_rejects_unsafe_external_features(xml: bytes, expected_code: str) -> None:
    with pytest.raises(PatcherError) as exc_info:
        provision_fixture(xml)
    assert exc_info.value.code == expected_code


def test_base_hash_and_expected_old_value_guards_fail_closed() -> None:
    patch = {
        "target": {"kind": "joint", "name": "elbow"},
        "attribute": "damping",
        "expected_old_value": 0.3,
        "new_value": 0.5,
    }
    with pytest.raises(PatcherError, match="base hash") as exc_info:
        apply_one_attribute_patch(base_xml=BASE_XML, expected_base_sha256="0" * 64, patch=patch)
    assert exc_info.value.code == "BASE_HASH_MISMATCH"

    patch["expected_old_value"] = 0.4
    with pytest.raises(PatcherError) as exc_info:
        apply_one_attribute_patch(base_xml=BASE_XML, expected_base_sha256=_base_hash(), patch=patch)
    assert exc_info.value.code == "OLD_VALUE_MISMATCH"


def test_selector_and_undeclared_document_changes_are_rejected() -> None:
    patch = {
        "target": {"kind": "joint", "name": "missing"},
        "attribute": "damping",
        "expected_old_value": 0.3,
        "new_value": 0.5,
    }
    with pytest.raises(PatcherError) as exc_info:
        apply_one_attribute_patch(base_xml=BASE_XML, expected_base_sha256=_base_hash(), patch=patch)
    assert exc_info.value.code == "SELECTOR_MISMATCH"

    undeclared = BASE_XML.replace(b'axis="0 0 2" damping="0.3"', b'axis="1 0 0" damping="0.5"')
    changes = canonical_document_diff(BASE_XML, undeclared)
    assert {change.attribute for change in changes} == {"axis", "damping"}


def test_invalid_patch_objects_never_create_partial_revisions() -> None:
    with pytest.raises(PatcherError) as exc_info:
        apply_one_attribute_patch(
            base_xml=BASE_XML,
            expected_base_sha256=_base_hash(),
            patch=[
                {
                    "target": {"kind": "joint", "name": "elbow"},
                    "attribute": "damping",
                    "expected_old_value": 0.3,
                    "new_value": 0.5,
                }
            ],
        )
    assert exc_info.value.code == "PATCH_NOT_ALLOWED"
