from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from types import MappingProxyType
from typing import Literal, Mapping

from pydantic import TypeAdapter, ValidationError


Vector3 = tuple[float, float, float]
Qualification = Literal["digital_only", "profile_qualified", "exact_build_verified"]
EvidenceBasis = Literal["manufacturer_spec", "derived", "planning_allowance"]


class HardwareProfileError(RuntimeError):
    """A safe, typed hardware-profile boundary failure."""

    def __init__(self, code: str, safe_message: str) -> None:
        self.code = code
        self.safe_message = safe_message
        self.retryable = False
        super().__init__(safe_message)


class UnknownHardwareProfileError(HardwareProfileError):
    def __init__(self, _profile_id: str) -> None:
        super().__init__(
            "HARDWARE_PROFILE_NOT_FOUND",
            "The requested hardware profile is not available.",
        )


@dataclass(frozen=True, slots=True)
class ComponentEnvelope:
    """Axis-aligned component volume in the profile's x/y/z frame.

    ``size_mm`` is always usable by the digital layout. ``reported_size_mm``
    keeps unknown manufacturer dimensions explicit instead of presenting a
    planning allowance as a measured fact.
    """

    component_id: str
    role: Literal["controller", "display", "driver", "keepout"]
    size_mm: Vector3
    center_mm: Vector3 = (0.0, 0.0, 0.0)
    basis: EvidenceBasis = "manufacturer_spec"
    reported_size_mm: tuple[float | None, float | None, float | None] = (
        None,
        None,
        None,
    )
    source_url: str | None = None

    def __post_init__(self) -> None:
        values = (*self.size_mm, *self.center_mm)
        if not all(isinstance(value, (int, float)) for value in values):
            raise TypeError("component envelope coordinates must be numeric")
        if not all(math.isfinite(float(value)) for value in values):
            raise ValueError("component envelope coordinates must be finite")
        if any(value <= 0 for value in self.size_mm):
            raise ValueError("component envelope sizes must be positive")
        for value in self.reported_size_mm:
            if value is not None and (not math.isfinite(value) or value <= 0):
                raise ValueError("reported component sizes must be positive and finite")

    @property
    def minimum_mm(self) -> Vector3:
        return tuple(
            center - size / 2 for center, size in zip(self.center_mm, self.size_mm)
        )  # type: ignore[return-value]

    @property
    def maximum_mm(self) -> Vector3:
        return tuple(
            center + size / 2 for center, size in zip(self.center_mm, self.size_mm)
        )  # type: ignore[return-value]


@dataclass(frozen=True, slots=True)
class HardwareComponent:
    component_id: str
    display_name: str
    quantity: int
    envelope: ComponentEnvelope
    known_mass_g: float | None = None
    included_in: str | None = None

    def __post_init__(self) -> None:
        if self.quantity < 1:
            raise ValueError("component quantity must be positive")
        if self.known_mass_g is not None and (
            not math.isfinite(self.known_mass_g) or self.known_mass_g <= 0
        ):
            raise ValueError("known component mass must be positive and finite")


@dataclass(frozen=True, slots=True)
class MassMetadata:
    known_component_mass_g: float | None
    complete_assembly_mass_g: float | None
    evidence: Literal["unknown", "partial", "complete"]


@dataclass(frozen=True, slots=True)
class PowerMetadata:
    required_input_voltage_v: float | None
    battery_capacity_mah: int | None
    complete_peak_current_a: float | None
    evidence: Literal["unknown", "partial", "complete"]


@dataclass(frozen=True, slots=True)
class HardwareProfile:
    profile_id: str
    display_name: str
    qualification: Qualification
    components: tuple[HardwareComponent, ...]
    keepouts: tuple[ComponentEnvelope, ...]
    capabilities: tuple[str, ...]
    mass: MassMetadata
    power: PowerMetadata
    source_urls: tuple[str, ...]
    unknowns: tuple[str, ...]

    def __post_init__(self) -> None:
        roles = [component.envelope.role for component in self.components]
        required_roles = {"controller", "display", "driver"}
        if not required_roles.issubset(roles):
            raise ValueError(
                "profile must define controller, display, and driver envelopes"
            )
        if len(set(self.capabilities)) != len(self.capabilities):
            raise ValueError("profile capabilities must be unique")
        if not self.source_urls:
            raise ValueError("profile must cite at least one primary source")

    @property
    def id(self) -> str:
        return self.profile_id

    @property
    def qualification_level(self) -> Qualification:
        return self.qualification

    @property
    def envelopes(self) -> Mapping[str, ComponentEnvelope]:
        return MappingProxyType(
            {
                component.envelope.role: component.envelope
                for component in self.components
            }
        )

    @property
    def board_envelope(self) -> ComponentEnvelope:
        return self.envelopes["controller"]

    @property
    def display_envelope(self) -> ComponentEnvelope:
        return self.envelopes["display"]

    @property
    def driver_envelope(self) -> ComponentEnvelope:
        return self.envelopes["driver"]

    @property
    def dimensions_mm(self) -> Vector3:
        return self.digital_envelope.size_mm

    @property
    def digital_envelope(self) -> ComponentEnvelope:
        """Planning AABB for every component and access keep-out in profile space.

        This is deliberately a digital planning envelope, not a measured enclosure or
        a physical-fit claim.  Keeping its non-zero center is important because the
        complete profile is placed with one rigid translation by the CAD compiler.
        """

        envelopes = (
            *(component.envelope for component in self.components),
            *self.keepouts,
        )
        minimum = tuple(
            min(item.minimum_mm[axis] for item in envelopes) for axis in range(3)
        )
        maximum = tuple(
            max(item.maximum_mm[axis] for item in envelopes) for axis in range(3)
        )
        size = tuple(maximum[axis] - minimum[axis] for axis in range(3))
        center = tuple((minimum[axis] + maximum[axis]) / 2 for axis in range(3))
        return ComponentEnvelope(
            component_id=f"{self.profile_id}-digital-envelope",
            role="keepout",
            size_mm=size,  # type: ignore[arg-type]
            center_mm=center,  # type: ignore[arg-type]
            basis="planning_allowance",
        )

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


_HARDWARE_PROFILE_ADAPTER = TypeAdapter(HardwareProfile)


def hardware_profile_from_dict(value: object) -> HardwareProfile:
    """Reconstruct and validate a canonical profile at a process boundary."""

    try:
        return _HARDWARE_PROFILE_ADAPTER.validate_python(value)
    except (TypeError, ValidationError, ValueError) as error:
        raise ValueError("hardware profile payload is invalid") from error


_M5STACK_SOURCES = (
    "https://docs.m5stack.com/en/core/CoreS3",
    "https://docs.m5stack.com/en/module/goplus2",
)
_PI_SOURCES = (
    "https://www.raspberrypi.com/products/raspberry-pi-zero-2-w/",
    "https://learn.adafruit.com/adafruit-crickit-hat-for-raspberry-pi-linux-computers",
    "https://www.waveshare.com/wiki/2inch_LCD_Module",
)


M5_CORES3_GOPLUS2 = HardwareProfile(
    profile_id="m5-cores3-goplus2/v1",
    display_name="M5Stack CoreS3 + GoPlus2",
    qualification="digital_only",
    components=(
        HardwareComponent(
            component_id="m5stack-cores3",
            display_name="M5Stack CoreS3",
            quantity=1,
            envelope=ComponentEnvelope(
                component_id="m5stack-cores3",
                role="controller",
                size_mm=(54.0, 15.5, 54.0),
                center_mm=(0.0, -6.5, 0.0),
                reported_size_mm=(54.0, 15.5, 54.0),
                source_url=_M5STACK_SOURCES[0],
            ),
        ),
        HardwareComponent(
            component_id="cores3-integrated-display",
            display_name="CoreS3 2-inch 320x240 touch display",
            quantity=1,
            envelope=ComponentEnvelope(
                component_id="cores3-integrated-display",
                role="display",
                size_mm=(40.64, 0.5, 30.48),
                center_mm=(0.0, -14.5, 0.0),
                basis="derived",
                # Active-area dimensions are derived from a 2-inch 4:3 diagonal.
                reported_size_mm=(None, None, None),
                source_url=_M5STACK_SOURCES[0],
            ),
            included_in="m5stack-cores3",
        ),
        HardwareComponent(
            component_id="m5stack-goplus2",
            display_name="M5Stack Module13.2 GoPlus2",
            quantity=1,
            envelope=ComponentEnvelope(
                component_id="m5stack-goplus2",
                role="driver",
                size_mm=(54.0, 13.0, 54.0),
                center_mm=(0.0, 7.75, 0.0),
                reported_size_mm=(54.0, 13.0, 54.0),
                source_url=_M5STACK_SOURCES[1],
            ),
            known_mass_g=38.0,
        ),
    ),
    keepouts=(
        ComponentEnvelope(
            component_id="cores3-front-access",
            role="keepout",
            size_mm=(54.0, 20.0, 54.0),
            center_mm=(0.0, -24.75, 0.0),
            basis="planning_allowance",
            source_url=_M5STACK_SOURCES[0],
        ),
        ComponentEnvelope(
            component_id="goplus2-rear-cable-access",
            role="keepout",
            size_mm=(54.0, 20.0, 32.0),
            center_mm=(0.0, 24.25, -8.0),
            basis="planning_allowance",
            source_url=_M5STACK_SOURCES[1],
        ),
    ),
    capabilities=(
        "differential_drive",
        "dc_motor_channels:2",
        "head_pan_tilt",
        "servo_channels:4",
        "display:320x240_touch",
        "camera",
        "microphone",
        "speaker",
        "wifi",
        "imu",
        "proximity",
    ),
    mass=MassMetadata(
        known_component_mass_g=38.0,
        complete_assembly_mass_g=None,
        evidence="partial",
    ),
    power=PowerMetadata(
        required_input_voltage_v=None,
        battery_capacity_mah=500,
        complete_peak_current_a=None,
        evidence="partial",
    ),
    source_urls=_M5STACK_SOURCES,
    unknowns=(
        "Selected wheel motors and their electrical limits are not fixed.",
        "Selected pan and tilt servos are not fixed.",
        "Complete moving assembly mass and peak current are not measured.",
        "Keepout distances are planning allowances, not measured clearances.",
    ),
)


PI_ZERO2WH_CRICKIT_WS2 = HardwareProfile(
    profile_id="pi-zero2wh-crickit-ws2/v1",
    display_name="Raspberry Pi Zero 2 WH + CRICKIT HAT + Waveshare 2-inch LCD",
    qualification="digital_only",
    components=(
        HardwareComponent(
            component_id="raspberry-pi-zero-2-wh",
            display_name="Raspberry Pi Zero 2 WH",
            quantity=1,
            envelope=ComponentEnvelope(
                component_id="raspberry-pi-zero-2-wh",
                role="controller",
                size_mm=(65.0, 30.0, 12.0),
                basis="planning_allowance",
                reported_size_mm=(65.0, 30.0, None),
                source_url=_PI_SOURCES[0],
            ),
        ),
        HardwareComponent(
            component_id="waveshare-2inch-lcd-module",
            display_name="Waveshare 2-inch SPI LCD module",
            quantity=1,
            envelope=ComponentEnvelope(
                component_id="waveshare-2inch-lcd-module",
                role="display",
                size_mm=(58.0, 8.0, 35.0),
                center_mm=(0.0, -24.0, 36.0),
                basis="planning_allowance",
                reported_size_mm=(58.0, None, 35.0),
                source_url=_PI_SOURCES[2],
            ),
        ),
        HardwareComponent(
            component_id="adafruit-crickit-hat",
            display_name="Adafruit CRICKIT HAT",
            quantity=1,
            envelope=ComponentEnvelope(
                component_id="adafruit-crickit-hat",
                role="driver",
                size_mm=(65.0, 56.0, 18.0),
                center_mm=(0.0, 0.0, 18.0),
                basis="planning_allowance",
                reported_size_mm=(None, None, None),
                source_url=_PI_SOURCES[1],
            ),
        ),
    ),
    keepouts=(
        ComponentEnvelope(
            component_id="waveshare-display-front-access",
            role="keepout",
            size_mm=(58.0, 20.0, 35.0),
            center_mm=(0.0, -38.0, 36.0),
            basis="planning_allowance",
            source_url=_PI_SOURCES[2],
        ),
        ComponentEnvelope(
            component_id="crickit-terminal-access",
            role="keepout",
            size_mm=(65.0, 24.0, 18.0),
            center_mm=(0.0, 40.0, 18.0),
            basis="planning_allowance",
            source_url=_PI_SOURCES[1],
        ),
    ),
    capabilities=(
        "differential_drive",
        "dc_motor_channels:2",
        "dc_motor_current_limit_a:1",
        "head_pan_tilt",
        "servo_channels:4",
        "display:320x240_spi",
        "speaker_amplifier_w:3",
        "wifi",
        "bluetooth",
        "linux_runtime",
    ),
    mass=MassMetadata(
        known_component_mass_g=None,
        complete_assembly_mass_g=None,
        evidence="unknown",
    ),
    power=PowerMetadata(
        required_input_voltage_v=5.0,
        battery_capacity_mah=None,
        complete_peak_current_a=None,
        evidence="partial",
    ),
    source_urls=_PI_SOURCES,
    unknowns=(
        "CRICKIT HAT and LCD depth envelopes are planning allowances.",
        "Selected battery, wheel motors, and pan/tilt servos are not fixed.",
        "Complete assembly mass and peak current are not measured.",
        "The LCD and CRICKIT mounting geometry has not been measured.",
    ),
)


DEFAULT_HARDWARE_PROFILES = (M5_CORES3_GOPLUS2, PI_ZERO2WH_CRICKIT_WS2)


class ProfileRegistry:
    def __init__(
        self, profiles: tuple[HardwareProfile, ...] = DEFAULT_HARDWARE_PROFILES
    ) -> None:
        by_id = {profile.profile_id: profile for profile in profiles}
        if len(by_id) != len(profiles):
            raise ValueError("hardware profile ids must be unique")
        self._profiles = MappingProxyType(by_id)

    def list_profiles(self) -> tuple[HardwareProfile, ...]:
        return tuple(
            self._profiles[profile_id] for profile_id in sorted(self._profiles)
        )

    def get_profile(self, profile_id: str) -> HardwareProfile:
        try:
            return self._profiles[profile_id]
        except KeyError:
            raise UnknownHardwareProfileError(profile_id) from None

    def list(self) -> tuple[HardwareProfile, ...]:
        return self.list_profiles()

    def get(self, profile_id: str) -> HardwareProfile:
        return self.get_profile(profile_id)


__all__ = [
    "ComponentEnvelope",
    "DEFAULT_HARDWARE_PROFILES",
    "HardwareComponent",
    "HardwareProfile",
    "HardwareProfileError",
    "hardware_profile_from_dict",
    "M5_CORES3_GOPLUS2",
    "MassMetadata",
    "PI_ZERO2WH_CRICKIT_WS2",
    "PowerMetadata",
    "ProfileRegistry",
    "UnknownHardwareProfileError",
]
