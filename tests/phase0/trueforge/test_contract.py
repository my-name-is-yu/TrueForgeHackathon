from __future__ import annotations

import json

from spikes.phase0.trueforge.protocol import DUMMY_TOOLS, PLANNED_TOOLS, inspection_payload, make_png, planned_tool_schemas, png_dimensions


def test_resolved_tool_boundary_is_exact() -> None:
    assert PLANNED_TOOLS == (
        "open_case",
        "inspect_asset",
        "run_task",
        "run_probe",
        "create_revision",
        "verify_revision",
        "publish_revision",
    )
    assert DUMMY_TOOLS == ("inspect_asset", "publish_revision")
    schemas = {schema["name"]: schema for schema in planned_tool_schemas()}
    assert list(schemas) == list(PLANNED_TOOLS)
    assert [name for name, schema in schemas.items() if schema["annotations"]["destructiveHint"]] == ["publish_revision"]


def test_large_tool_fixture_is_exactly_256_rows() -> None:
    payload = inspection_payload()
    assert len(payload["rows"]) == 256
    serialized = json.dumps(payload, separators=(",", ":"))
    assert len(serialized) > 20_000
    assert "traceback" not in serialized


def test_image_fixture_is_160_by_120_png() -> None:
    assert png_dimensions(make_png()) == (160, 120)
