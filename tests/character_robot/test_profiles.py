from __future__ import annotations

import pytest

from character_robot.profiles import (
    ComponentEnvelope,
    ProfileRegistry,
    UnknownHardwareProfileError,
)


def test_registry_exposes_exactly_the_two_digital_profiles() -> None:
    profiles = ProfileRegistry().list_profiles()

    assert [profile.profile_id for profile in profiles] == [
        "m5-cores3-goplus2/v1",
        "pi-zero2wh-crickit-ws2/v1",
    ]
    assert all(profile.qualification == "digital_only" for profile in profiles)
    assert all(
        {"controller", "display", "driver"}.issubset(profile.envelopes)
        for profile in profiles
    )
    assert all("differential_drive" in profile.capabilities for profile in profiles)
    assert all("head_pan_tilt" in profile.capabilities for profile in profiles)


def test_m5_profile_preserves_published_dimensions_and_partial_evidence() -> None:
    profile = ProfileRegistry().get_profile("m5-cores3-goplus2/v1")

    assert profile.board_envelope.reported_size_mm == (54.0, 15.5, 54.0)
    assert profile.driver_envelope.reported_size_mm == (54.0, 13.0, 54.0)
    assert profile.mass.known_component_mass_g == 38.0
    assert profile.mass.complete_assembly_mass_g is None
    assert profile.power.battery_capacity_mah == 500
    assert profile.power.complete_peak_current_a is None
    assert profile.unknowns


def test_profile_digital_envelope_includes_access_keepouts() -> None:
    registry = ProfileRegistry()
    m5 = registry.get_profile("m5-cores3-goplus2/v1").digital_envelope
    pi = registry.get_profile("pi-zero2wh-crickit-ws2/v1").digital_envelope

    assert m5.minimum_mm == pytest.approx((-27.0, -34.75, -27.0))
    assert m5.maximum_mm == pytest.approx((27.0, 34.25, 27.0))
    assert m5.size_mm == pytest.approx((54.0, 69.0, 54.0))
    assert pi.minimum_mm == pytest.approx((-32.5, -48.0, -6.0))
    assert pi.maximum_mm == pytest.approx((32.5, 52.0, 53.5))
    assert pi.size_mm == pytest.approx((65.0, 100.0, 59.5))


def test_pi_profile_marks_unverified_depth_mass_and_current_as_unknown() -> None:
    profile = ProfileRegistry().get_profile("pi-zero2wh-crickit-ws2/v1")

    assert profile.board_envelope.reported_size_mm == (65.0, 30.0, None)
    assert profile.board_envelope.basis == "planning_allowance"
    assert profile.driver_envelope.reported_size_mm == (None, None, None)
    assert profile.mass.evidence == "unknown"
    assert profile.power.required_input_voltage_v == 5.0
    assert profile.power.complete_peak_current_a is None


def test_unknown_profile_is_a_typed_safe_failure() -> None:
    with pytest.raises(UnknownHardwareProfileError) as caught:
        ProfileRegistry().get_profile("custom-board/v1")

    assert caught.value.code == "HARDWARE_PROFILE_NOT_FOUND"
    assert caught.value.retryable is False


def test_component_envelope_rejects_nonfinite_or_nonpositive_sizes() -> None:
    with pytest.raises(ValueError, match="positive"):
        ComponentEnvelope("bad", "driver", (10.0, 0.0, 2.0))
    with pytest.raises(ValueError, match="finite"):
        ComponentEnvelope("bad", "driver", (10.0, 4.0, float("nan")))
