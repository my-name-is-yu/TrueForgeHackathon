"""Provenance-aware V2 component catalog.

The catalog is deliberately separate from the V1 qualification dataclasses.  It
stores manufacturer claims without turning planning values into measured facts,
and computes eligibility for a concrete use rather than carrying a mutable
``eligible`` flag on an entry.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import date
from typing import Annotated, Literal, Self, TypeAlias

from pydantic import Field, StrictBool, field_validator, model_validator

from .contract import FiniteFloat, SafeIdentifier, SafeText, Sha256, V2Model


CATALOG_SCHEMA_VERSION = "character-catalog/v2"

CatalogCategory: TypeAlias = Literal[
    "controller",
    "motor",
    "wheel",
    "hub",
    "shaft",
    "servo",
    "horn",
    "caster",
    "battery",
    "charger",
    "regulator",
    "motor_driver",
    "fuse",
    "switch",
    "e_stop",
    "connector",
    "wire",
    "fastener",
    "insert",
    "spacer",
]

CatalogUse: TypeAlias = Literal[
    "controller_isolated",
    "controller_auxiliary",
    "board_motor_stage",
    "motor_drive",
    "wheel_drive",
    "head_servo",
    "head_horn",
    "caster",
    "battery",
    "charger",
    "regulator",
    "motor_driver",
    "protection",
    "main_switch",
    "e_stop",
    "connector",
    "wire",
    "fastener",
    "insert",
    "spacer",
]

FactKey: TypeAlias = Literal[
    "envelope_x_mm",
    "envelope_y_mm",
    "envelope_z_mm",
    "outer_diameter_mm",
    "part_width_mm",
    "height_mm",
    "mass_g",
    "product_set_mass_g",
    "ball_diameter_mm",
    "mount_hole_spacing_mm",
    "mount_hole_diameter_mm",
    "shaft_diameter_mm",
    "shaft_profile",
    "mount_pattern",
    "connector_mpn",
    "connector_family",
    "operating_voltage_min_v",
    "operating_voltage_nominal_v",
    "operating_voltage_max_v",
    "current_continuous_a",
    "current_peak_a",
    "current_stall_a",
    "current_limit_a",
    "rail_current_limit_a",
    "contact_rating_a",
    "thermal_limit_c",
    "torque_continuous_nm",
    "torque_stall_nm",
    "speed_nominal_rpm",
    "speed_no_load_rpm",
    "speed_max_rpm",
    "capacity_mah",
    "quantity_per_pack",
    "wire_gauge_awg",
    "power_isolation",
    "battery_protection",
    "battery_chemistry",
    "motor_driver_model",
    "communication_protocol",
    "ir_pin_map",
    "revision",
    "material",
]

FactKind: TypeAlias = Literal["numeric", "text", "boolean"]
FactBasis: TypeAlias = Literal[
    "manufacturer_stated", "converted", "derived", "assumption"
]
FactState: TypeAlias = Literal["known", "derived", "assumption", "unknown", "conflict"]

Unit: TypeAlias = Literal[
    "mm",
    "cm",
    "m",
    "in",
    "ft",
    "g",
    "kg",
    "oz",
    "lb",
    "V",
    "mV",
    "kV",
    "A",
    "mA",
    "N*m",
    "Nmm",
    "rpm",
    "m/s",
    "mAh",
    "Ah",
    "C",
    "deg",
    "none",
]

ConversionRule: TypeAlias = Literal[
    "identity",
    "cm_to_mm",
    "m_to_mm",
    "in_to_mm",
    "ft_to_mm",
    "kg_to_g",
    "oz_to_g",
    "lb_to_g",
    "mV_to_V",
    "kV_to_V",
    "mA_to_A",
    "Nmm_to_Nm",
    "Ah_to_mAh",
]

EligibilityReason: TypeAlias = Literal[
    "CATEGORY_MISMATCH",
    "MISSING_ENVELOPE",
    "MISSING_MASS",
    "MISSING_OPERATING_VOLTAGE",
    "MISSING_CURRENT_LIMIT",
    "MISSING_TORQUE",
    "MISSING_SPEED",
    "MISSING_MOUNT_GEOMETRY",
    "MISSING_SHAFT_GEOMETRY",
    "MISSING_CONNECTOR",
    "MISSING_REVISION",
    "MISSING_POWER_ISOLATION",
    "MISSING_THERMAL_LIMIT",
    "MISSING_PROTECTION",
    "MISSING_CAPACITY",
    "MISSING_CHEMISTRY",
    "MISSING_CONTACT_RATING",
    "MISSING_INTERFACE",
    "UNKNOWN_OFFICIAL_FACT",
    "UNKNOWN_ASSEMBLY_SCOPE",
    "UNTRUSTED_FACT_BASIS",
    "CONFLICTING_FACT",
    "CONFLICTING_ENVELOPE",
    "CONFLICTING_REVISION",
    "CONFLICTING_INTERFACE",
]

IsoDate: TypeAlias = Annotated[
    str,
    Field(
        min_length=10,
        max_length=10,
        pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$",
    ),
]


_FACT_SPECS: dict[FactKey, tuple[FactKind, str | None]] = {
    "envelope_x_mm": ("numeric", "mm"),
    "envelope_y_mm": ("numeric", "mm"),
    "envelope_z_mm": ("numeric", "mm"),
    "outer_diameter_mm": ("numeric", "mm"),
    "part_width_mm": ("numeric", "mm"),
    "height_mm": ("numeric", "mm"),
    "mass_g": ("numeric", "g"),
    "product_set_mass_g": ("numeric", "g"),
    "ball_diameter_mm": ("numeric", "mm"),
    "mount_hole_spacing_mm": ("numeric", "mm"),
    "mount_hole_diameter_mm": ("numeric", "mm"),
    "shaft_diameter_mm": ("numeric", "mm"),
    "shaft_profile": ("text", None),
    "mount_pattern": ("text", None),
    "connector_mpn": ("text", None),
    "connector_family": ("text", None),
    "operating_voltage_min_v": ("numeric", "V"),
    "operating_voltage_nominal_v": ("numeric", "V"),
    "operating_voltage_max_v": ("numeric", "V"),
    "current_continuous_a": ("numeric", "A"),
    "current_peak_a": ("numeric", "A"),
    "current_stall_a": ("numeric", "A"),
    "current_limit_a": ("numeric", "A"),
    "rail_current_limit_a": ("numeric", "A"),
    "contact_rating_a": ("numeric", "A"),
    "thermal_limit_c": ("numeric", "C"),
    "torque_continuous_nm": ("numeric", "N*m"),
    "torque_stall_nm": ("numeric", "N*m"),
    "speed_nominal_rpm": ("numeric", "rpm"),
    "speed_no_load_rpm": ("numeric", "rpm"),
    "speed_max_rpm": ("numeric", "rpm"),
    "capacity_mah": ("numeric", "mAh"),
    "quantity_per_pack": ("numeric", "none"),
    "wire_gauge_awg": ("numeric", "none"),
    "power_isolation": ("boolean", None),
    "battery_protection": ("text", None),
    "battery_chemistry": ("text", None),
    "motor_driver_model": ("text", None),
    "communication_protocol": ("text", None),
    "ir_pin_map": ("text", None),
    "revision": ("text", None),
    "material": ("text", None),
}

_NUMERIC_STRICT_POSITIVE_KEYS = frozenset(
    key
    for key, (kind, _unit) in _FACT_SPECS.items()
    if kind == "numeric"
    and key
    in {
        "envelope_x_mm",
        "envelope_y_mm",
        "envelope_z_mm",
        "outer_diameter_mm",
        "part_width_mm",
        "height_mm",
        "mass_g",
        "product_set_mass_g",
        "ball_diameter_mm",
        "mount_hole_spacing_mm",
        "mount_hole_diameter_mm",
        "shaft_diameter_mm",
        "operating_voltage_min_v",
        "operating_voltage_nominal_v",
        "operating_voltage_max_v",
        "capacity_mah",
        "contact_rating_a",
    }
)

_UNIT_CONVERSIONS: dict[tuple[Unit, str], tuple[ConversionRule, float]] = {
    ("mm", "mm"): ("identity", 1.0),
    ("cm", "mm"): ("cm_to_mm", 10.0),
    ("m", "mm"): ("m_to_mm", 1000.0),
    ("in", "mm"): ("in_to_mm", 25.4),
    ("ft", "mm"): ("ft_to_mm", 304.8),
    ("g", "g"): ("identity", 1.0),
    ("kg", "g"): ("kg_to_g", 1000.0),
    ("oz", "g"): ("oz_to_g", 28.349523125),
    ("lb", "g"): ("lb_to_g", 453.59237),
    ("V", "V"): ("identity", 1.0),
    ("mV", "V"): ("mV_to_V", 0.001),
    ("kV", "V"): ("kV_to_V", 1000.0),
    ("A", "A"): ("identity", 1.0),
    ("mA", "A"): ("mA_to_A", 0.001),
    ("N*m", "N*m"): ("identity", 1.0),
    ("Nmm", "N*m"): ("Nmm_to_Nm", 0.001),
    ("rpm", "rpm"): ("identity", 1.0),
    ("m/s", "m/s"): ("identity", 1.0),
    ("mAh", "mAh"): ("identity", 1.0),
    ("Ah", "mAh"): ("Ah_to_mAh", 1000.0),
    ("C", "C"): ("identity", 1.0),
    ("deg", "deg"): ("identity", 1.0),
    ("none", "none"): ("identity", 1.0),
}

_SAFE_URL = re.compile(r"^https://[^\s]+$")


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _matches_exact_conversion(expected: float, actual: float) -> bool:
    """Allow only a one-ULP representation difference for converted values."""

    if not math.isfinite(expected) or not math.isfinite(actual):
        return False
    if expected == actual:
        return True
    return abs(expected - actual) <= max(math.ulp(expected), math.ulp(actual))


class CatalogSource(V2Model):
    """One immutable manufacturer document observed at an evidence date."""

    source_id: SafeIdentifier
    manufacturer: SafeText
    title: SafeText
    url: SafeText
    media_type: Literal["text/html", "application/pdf", "text/plain"]
    document_sha256: Sha256
    evidence_date: IsoDate
    published_date: IsoDate | None = None

    @field_validator("url")
    @classmethod
    def require_https_url(cls, value: str) -> str:
        if _SAFE_URL.fullmatch(value) is None:
            raise ValueError("catalog source URL must be an HTTPS URL")
        return value

    @field_validator("evidence_date", "published_date")
    @classmethod
    def require_calendar_date(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                date.fromisoformat(value)
            except ValueError as error:
                raise ValueError(
                    "catalog dates must be real ISO calendar dates"
                ) from error
        return value


class EvidenceRef(V2Model):
    """A locator copied into a fact so the fact is independently portable."""

    source_id: SafeIdentifier
    source_url: SafeText
    locator: SafeText
    document_sha256: Sha256
    evidence_date: IsoDate

    @field_validator("source_url")
    @classmethod
    def require_https_url(cls, value: str) -> str:
        if _SAFE_URL.fullmatch(value) is None:
            raise ValueError("evidence source URL must be an HTTPS URL")
        return value

    @field_validator("evidence_date")
    @classmethod
    def require_calendar_date(cls, value: str) -> str:
        try:
            date.fromisoformat(value)
        except ValueError as error:
            raise ValueError(
                "evidence dates must be real ISO calendar dates"
            ) from error
        return value


class NumericClaim(V2Model):
    kind: Literal["numeric"] = "numeric"
    original_value: FiniteFloat
    original_unit: Unit
    canonical_value: FiniteFloat
    canonical_unit: Unit
    basis: FactBasis
    conversion_rule: ConversionRule
    scope: SafeIdentifier = "component"
    evidence: EvidenceRef | None = None
    derived_from: tuple[SafeIdentifier, ...] = ()

    @model_validator(mode="after")
    def validate_claim(self) -> Self:
        conversion = _UNIT_CONVERSIONS.get((self.original_unit, self.canonical_unit))
        if conversion is None:
            raise ValueError(
                f"unsupported conversion {self.original_unit!r} to "
                f"{self.canonical_unit!r}"
            )
        expected_rule, factor = conversion
        if self.conversion_rule != expected_rule:
            raise ValueError("conversion_rule does not match original/canonical units")
        expected = self.original_value * factor
        if self.conversion_rule == "identity":
            if self.original_value != self.canonical_value:
                raise ValueError(
                    "identity conversion requires original_value == canonical_value"
                )
        elif not _matches_exact_conversion(expected, self.canonical_value):
            raise ValueError("canonical_value does not match the conversion rule")
        if self.basis == "converted" and self.conversion_rule == "identity":
            raise ValueError("converted claims must apply a non-identity conversion")
        if self.basis == "manufacturer_stated" and self.conversion_rule != "identity":
            raise ValueError(
                "manufacturer_stated claims must retain the source unit; "
                "use converted for a unit conversion"
            )
        if self.basis in {"manufacturer_stated", "converted"} and self.evidence is None:
            raise ValueError("manufacturer claims must cite evidence")
        if self.basis == "assumption" and self.evidence is not None:
            raise ValueError("assumptions cannot cite manufacturer evidence")
        if self.basis == "derived" and not self.derived_from:
            raise ValueError("derived claims must identify their source facts")
        return self


class TextClaim(V2Model):
    kind: Literal["text"] = "text"
    original_value: SafeText
    canonical_value: SafeText
    basis: FactBasis
    conversion_rule: Literal["identity"] = "identity"
    scope: SafeIdentifier = "component"
    evidence: EvidenceRef | None = None
    derived_from: tuple[SafeIdentifier, ...] = ()

    @model_validator(mode="after")
    def validate_claim(self) -> Self:
        if self.original_value != self.canonical_value:
            raise ValueError(
                "identity conversion requires original_value == canonical_value"
            )
        if self.basis in {"manufacturer_stated", "converted"} and self.evidence is None:
            raise ValueError("manufacturer text claims must cite evidence")
        if self.basis == "converted":
            raise ValueError("text claims cannot use converted basis")
        if self.basis == "assumption" and self.evidence is not None:
            raise ValueError("assumptions cannot cite manufacturer evidence")
        if self.basis == "derived" and not self.derived_from:
            raise ValueError("derived claims must identify their source facts")
        return self


class BooleanClaim(V2Model):
    kind: Literal["boolean"] = "boolean"
    original_value: StrictBool
    canonical_value: StrictBool
    basis: FactBasis
    conversion_rule: Literal["identity"] = "identity"
    scope: SafeIdentifier = "component"
    evidence: EvidenceRef | None = None
    derived_from: tuple[SafeIdentifier, ...] = ()

    @model_validator(mode="after")
    def validate_claim(self) -> Self:
        if self.original_value != self.canonical_value:
            raise ValueError(
                "identity conversion requires original_value == canonical_value"
            )
        if self.basis in {"manufacturer_stated", "converted"} and self.evidence is None:
            raise ValueError("manufacturer boolean claims must cite evidence")
        if self.basis == "converted":
            raise ValueError("boolean claims cannot use converted basis")
        if self.basis == "assumption" and self.evidence is not None:
            raise ValueError("assumptions cannot cite manufacturer evidence")
        if self.basis == "derived" and not self.derived_from:
            raise ValueError("derived claims must identify their source facts")
        return self


FactClaim: TypeAlias = Annotated[
    NumericClaim | TextClaim | BooleanClaim,
    Field(discriminator="kind"),
]


class CatalogFact(V2Model):
    """One fact key with claims, or an explicit unknown."""

    fact_key: FactKey
    claims: tuple[FactClaim, ...] = ()
    unknown_reason: EligibilityReason | None = None
    unknown_evidence: tuple[EvidenceRef, ...] = ()

    @model_validator(mode="after")
    def validate_fact(self) -> Self:
        if not self.claims and self.unknown_reason is None:
            raise ValueError("a catalog fact needs claims or an unknown reason")
        if self.claims and self.unknown_reason is not None:
            raise ValueError("known/conflicting facts cannot also be unknown")
        if self.claims and self.unknown_evidence:
            raise ValueError(
                "known/conflicting facts cannot also include unknown evidence"
            )
        if (
            not self.claims
            and self.unknown_reason
            not in {"UNKNOWN_OFFICIAL_FACT", "UNKNOWN_ASSEMBLY_SCOPE"}
            and self.unknown_reason != _reason_for_missing(self.fact_key)
        ):
            raise ValueError("unknown_reason does not match the fact key")
        expected_kind, expected_unit = _FACT_SPECS[self.fact_key]
        if self.claims:
            if any(claim.kind != expected_kind for claim in self.claims):
                raise ValueError("fact claim type does not match its fact key")
            if expected_kind == "numeric":
                assert expected_unit is not None
                for claim in self.claims:
                    assert isinstance(claim, NumericClaim)
                    if claim.canonical_unit != expected_unit:
                        raise ValueError(
                            f"{self.fact_key} must use canonical unit {expected_unit}"
                        )
                    if claim.canonical_value < 0:
                        if self.fact_key != "thermal_limit_c":
                            raise ValueError(f"{self.fact_key} cannot be negative")
                    elif (
                        self.fact_key in _NUMERIC_STRICT_POSITIVE_KEYS
                        and claim.canonical_value <= 0
                    ):
                        raise ValueError(f"{self.fact_key} must be positive")
                    if self.fact_key == "quantity_per_pack" and (
                        claim.canonical_value < 1
                        or not claim.canonical_value.is_integer()
                    ):
                        raise ValueError("quantity_per_pack must be a positive integer")
                    if self.fact_key == "wire_gauge_awg" and (
                        not claim.canonical_value.is_integer()
                    ):
                        raise ValueError("wire_gauge_awg must be an integer")
            if len(self.claims) > 1:
                fingerprints = {
                    _canonical_json_bytes(claim.model_dump(mode="json"))
                    for claim in self.claims
                }
                if len(fingerprints) != len(self.claims):
                    raise ValueError("duplicate claims for a fact are not allowed")
        return self

    @property
    def state(self) -> FactState:
        if not self.claims:
            return "unknown"
        if self.fact_key in _FACT_SPECS and _FACT_SPECS[self.fact_key][0] == "numeric":
            values = {
                claim.canonical_value
                for claim in self.claims
                if isinstance(claim, NumericClaim)
            }
        elif _FACT_SPECS[self.fact_key][0] == "boolean":
            values = {
                claim.canonical_value
                for claim in self.claims
                if isinstance(claim, BooleanClaim)
            }
        else:
            values = {
                claim.canonical_value
                for claim in self.claims
                if isinstance(claim, TextClaim)
            }
        if len(values) > 1:
            return "conflict"
        if any(claim.basis == "assumption" for claim in self.claims):
            return "assumption"
        if any(claim.basis == "derived" for claim in self.claims):
            return "derived"
        return "known"

    @property
    def canonical_value(self) -> float | str | bool | None:
        if self.state != "known" or not self.claims:
            return None
        claim = self.claims[0]
        if isinstance(claim, NumericClaim):
            return claim.canonical_value
        return claim.canonical_value


class AdvisoryData(V2Model):
    """Timestamped non-engineering information excluded from the catalog digest."""

    price_amount: FiniteFloat | None = Field(default=None, ge=0.0)
    price_currency: Literal["USD", "JPY", "EUR"] | None = None
    availability: Literal["active", "out_of_stock", "backorder", "unknown"] | None = (
        None
    )
    observed_at: IsoDate | None = None

    @field_validator("observed_at")
    @classmethod
    def require_calendar_date(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                date.fromisoformat(value)
            except ValueError as error:
                raise ValueError(
                    "advisory dates must be real ISO calendar dates"
                ) from error
        return value

    @model_validator(mode="after")
    def require_advisory_timestamp(self) -> Self:
        if (
            self.price_amount is not None
            or self.price_currency is not None
            or self.availability is not None
        ) and self.observed_at is None:
            raise ValueError("advisory price/availability requires observed_at")
        if (self.price_amount is None) != (self.price_currency is None):
            raise ValueError("price amount and currency must be provided together")
        return self


class CatalogEntry(V2Model):
    entry_id: SafeIdentifier
    manufacturer: SafeText
    manufacturer_sku: SafeText
    variant: SafeText
    category: CatalogCategory
    capabilities: tuple[SafeIdentifier, ...] = ()
    facts: tuple[CatalogFact, ...] = ()
    advisory: AdvisoryData | None = None

    @field_validator("capabilities")
    @classmethod
    def require_unique_capabilities(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("catalog capabilities must be unique")
        return value

    @model_validator(mode="after")
    def validate_facts(self) -> Self:
        keys = [fact.fact_key for fact in self.facts]
        if len(keys) != len(set(keys)):
            raise ValueError("catalog fact keys must be unique per entry")
        voltage_values: dict[str, float] = {}
        for fact_key in (
            "operating_voltage_min_v",
            "operating_voltage_nominal_v",
            "operating_voltage_max_v",
        ):
            fact = next(
                (
                    candidate
                    for candidate in self.facts
                    if candidate.fact_key == fact_key
                ),
                None,
            )
            if fact is not None and fact.state == "known":
                value = fact.canonical_value
                if isinstance(value, float):
                    voltage_values[fact_key] = value
        minimum = voltage_values.get("operating_voltage_min_v")
        nominal = voltage_values.get("operating_voltage_nominal_v")
        maximum = voltage_values.get("operating_voltage_max_v")
        if minimum is not None and maximum is not None and minimum > maximum:
            raise ValueError("operating voltage minimum cannot exceed maximum")
        if minimum is not None and nominal is not None and nominal < minimum:
            raise ValueError("operating voltage nominal cannot be below minimum")
        if nominal is not None and maximum is not None and nominal > maximum:
            raise ValueError("operating voltage nominal cannot exceed maximum")
        return self

    @property
    def identity(self) -> tuple[str, str, str]:
        return (self.manufacturer, self.manufacturer_sku, self.variant)

    def fact(self, fact_key: FactKey) -> CatalogFact | None:
        return next((fact for fact in self.facts if fact.fact_key == fact_key), None)

    def fact_state(self, fact_key: FactKey) -> FactState:
        fact = self.fact(fact_key)
        return "unknown" if fact is None else fact.state

    def numeric(self, fact_key: FactKey) -> float | None:
        fact = self.fact(fact_key)
        if fact is None or fact.state != "known":
            return None
        value = fact.canonical_value
        return value if isinstance(value, float) else None

    def text(self, fact_key: FactKey) -> str | None:
        fact = self.fact(fact_key)
        if fact is None or fact.state != "known":
            return None
        value = fact.canonical_value
        return value if isinstance(value, str) else None

    def boolean(self, fact_key: FactKey) -> bool | None:
        fact = self.fact(fact_key)
        if fact is None or fact.state != "known":
            return None
        value = fact.canonical_value
        return value if isinstance(value, bool) else None


_USE_CATEGORIES: dict[CatalogUse, frozenset[CatalogCategory]] = {
    "controller_isolated": frozenset({"controller"}),
    "controller_auxiliary": frozenset({"controller"}),
    "board_motor_stage": frozenset({"motor_driver", "controller"}),
    "motor_drive": frozenset({"motor"}),
    "wheel_drive": frozenset({"wheel", "hub"}),
    "head_servo": frozenset({"servo"}),
    "head_horn": frozenset({"horn"}),
    "caster": frozenset({"caster"}),
    "battery": frozenset({"battery"}),
    "charger": frozenset({"charger"}),
    "regulator": frozenset({"regulator"}),
    "motor_driver": frozenset({"motor_driver"}),
    "protection": frozenset({"fuse", "switch", "e_stop"}),
    "main_switch": frozenset({"switch"}),
    "e_stop": frozenset({"e_stop", "switch"}),
    "connector": frozenset({"connector"}),
    "wire": frozenset({"wire"}),
    "fastener": frozenset({"fastener"}),
    "insert": frozenset({"insert"}),
    "spacer": frozenset({"spacer"}),
}

_REQUIRED_FACTS: dict[CatalogUse, tuple[FactKey, ...]] = {
    # The isolated-controller use intentionally does not claim a motor-power
    # path.  Physical mass remains an explicit unknown for downstream CoG work.
    "controller_isolated": (
        "envelope_x_mm",
        "envelope_y_mm",
        "envelope_z_mm",
        "operating_voltage_min_v",
        "operating_voltage_max_v",
    ),
    "controller_auxiliary": (
        "envelope_x_mm",
        "envelope_y_mm",
        "envelope_z_mm",
        "connector_mpn",
        "communication_protocol",
        "revision",
    ),
    "board_motor_stage": (
        "envelope_x_mm",
        "envelope_y_mm",
        "envelope_z_mm",
        "mass_g",
        "operating_voltage_min_v",
        "operating_voltage_max_v",
        "current_continuous_a",
        "current_peak_a",
        "thermal_limit_c",
        "connector_mpn",
        "revision",
        "power_isolation",
    ),
    "motor_drive": (
        "envelope_x_mm",
        "envelope_y_mm",
        "envelope_z_mm",
        "mass_g",
        "operating_voltage_nominal_v",
        "current_continuous_a",
        "current_stall_a",
        "torque_continuous_nm",
        "speed_nominal_rpm",
        "mount_pattern",
        "shaft_diameter_mm",
    ),
    "wheel_drive": (
        "envelope_x_mm",
        "envelope_y_mm",
        "envelope_z_mm",
        "mass_g",
        "shaft_diameter_mm",
        "shaft_profile",
    ),
    "head_servo": (
        "envelope_x_mm",
        "envelope_y_mm",
        "envelope_z_mm",
        "mass_g",
        "operating_voltage_nominal_v",
        "current_stall_a",
        "torque_stall_nm",
        "speed_max_rpm",
        "mount_pattern",
        "connector_mpn",
        "revision",
    ),
    "head_horn": (
        "envelope_x_mm",
        "envelope_y_mm",
        "envelope_z_mm",
        "mass_g",
        "shaft_profile",
        "mount_pattern",
    ),
    "caster": (
        "envelope_x_mm",
        "envelope_y_mm",
        "envelope_z_mm",
        "mass_g",
        "ball_diameter_mm",
        "mount_hole_spacing_mm",
        "mount_hole_diameter_mm",
    ),
    "battery": (
        "envelope_x_mm",
        "envelope_y_mm",
        "envelope_z_mm",
        "mass_g",
        "operating_voltage_nominal_v",
        "capacity_mah",
        "current_continuous_a",
        "battery_protection",
        "connector_mpn",
        "revision",
    ),
    "charger": (
        "mass_g",
        "operating_voltage_nominal_v",
        "current_continuous_a",
        "battery_chemistry",
        "connector_mpn",
        "revision",
    ),
    "regulator": (
        "envelope_x_mm",
        "envelope_y_mm",
        "envelope_z_mm",
        "mass_g",
        "operating_voltage_min_v",
        "operating_voltage_max_v",
        "current_limit_a",
        "thermal_limit_c",
        "connector_mpn",
        "revision",
    ),
    "motor_driver": (
        "envelope_x_mm",
        "envelope_y_mm",
        "envelope_z_mm",
        "mass_g",
        "operating_voltage_min_v",
        "operating_voltage_max_v",
        "current_continuous_a",
        "current_peak_a",
        "thermal_limit_c",
        "connector_mpn",
        "revision",
    ),
    "protection": (
        "envelope_x_mm",
        "envelope_y_mm",
        "envelope_z_mm",
        "mass_g",
        "operating_voltage_max_v",
        "current_limit_a",
        "revision",
    ),
    "main_switch": (
        "envelope_x_mm",
        "envelope_y_mm",
        "envelope_z_mm",
        "mass_g",
        "operating_voltage_max_v",
        "current_limit_a",
        "contact_rating_a",
        "revision",
    ),
    "e_stop": (
        "envelope_x_mm",
        "envelope_y_mm",
        "envelope_z_mm",
        "mass_g",
        "operating_voltage_max_v",
        "current_limit_a",
        "contact_rating_a",
        "mount_pattern",
        "revision",
    ),
    "connector": (
        "envelope_x_mm",
        "envelope_y_mm",
        "envelope_z_mm",
        "mass_g",
        "connector_mpn",
        "revision",
    ),
    "wire": (
        "mass_g",
        "wire_gauge_awg",
        "current_continuous_a",
        "revision",
    ),
    "fastener": ("mass_g", "mount_pattern", "revision"),
    "insert": ("mass_g", "mount_pattern", "revision"),
    "spacer": ("envelope_x_mm", "envelope_y_mm", "envelope_z_mm", "mass_g", "revision"),
}


def _reason_for_missing(fact_key: FactKey) -> EligibilityReason:
    if fact_key.startswith("envelope_"):
        return "MISSING_ENVELOPE"
    if fact_key in {"mass_g", "product_set_mass_g"}:
        return "MISSING_MASS"
    if fact_key.startswith("operating_voltage_"):
        return "MISSING_OPERATING_VOLTAGE"
    if fact_key.startswith("current_") or fact_key == "rail_current_limit_a":
        return "MISSING_CURRENT_LIMIT"
    if fact_key.startswith("torque_"):
        return "MISSING_TORQUE"
    if fact_key.startswith("speed_"):
        return "MISSING_SPEED"
    if fact_key in {"mount_pattern", "mount_hole_spacing_mm", "mount_hole_diameter_mm"}:
        return "MISSING_MOUNT_GEOMETRY"
    if fact_key in {"shaft_diameter_mm", "shaft_profile"}:
        return "MISSING_SHAFT_GEOMETRY"
    if fact_key in {"connector_mpn", "connector_family"}:
        return "MISSING_CONNECTOR"
    if fact_key in {"communication_protocol", "motor_driver_model", "ir_pin_map"}:
        return "MISSING_INTERFACE"
    if fact_key == "revision":
        return "MISSING_REVISION"
    if fact_key == "power_isolation":
        return "MISSING_POWER_ISOLATION"
    if fact_key == "thermal_limit_c":
        return "MISSING_THERMAL_LIMIT"
    if fact_key == "contact_rating_a":
        return "MISSING_CONTACT_RATING"
    if fact_key == "capacity_mah":
        return "MISSING_CAPACITY"
    if fact_key == "battery_chemistry":
        return "MISSING_CHEMISTRY"
    if fact_key == "battery_protection":
        return "MISSING_PROTECTION"
    return "UNKNOWN_OFFICIAL_FACT"


def _reason_for_conflict(fact_key: FactKey) -> EligibilityReason:
    if fact_key.startswith("envelope_"):
        return "CONFLICTING_ENVELOPE"
    if fact_key == "revision":
        return "CONFLICTING_REVISION"
    if fact_key in {
        "mount_pattern",
        "mount_hole_spacing_mm",
        "mount_hole_diameter_mm",
        "shaft_diameter_mm",
        "shaft_profile",
        "connector_mpn",
        "connector_family",
        "ir_pin_map",
    }:
        return "CONFLICTING_INTERFACE"
    return "CONFLICTING_FACT"


class EligibilityAssessment(V2Model):
    entry_id: SafeIdentifier
    use: CatalogUse
    eligible: StrictBool
    blocking_reasons: tuple[EligibilityReason, ...] = ()
    blocking_facts: tuple[FactKey, ...] = ()


def assess_eligibility(entry: CatalogEntry, use: CatalogUse) -> EligibilityAssessment:
    """Derive eligibility from facts and the concrete intended use."""

    reasons: list[EligibilityReason] = []
    blocking_facts: list[FactKey] = []
    if entry.category not in _USE_CATEGORIES[use]:
        reasons.append("CATEGORY_MISMATCH")
    for fact_key in _REQUIRED_FACTS[use]:
        fact = entry.fact(fact_key)
        if fact is None or fact.state == "unknown":
            reasons.append(
                fact.unknown_reason
                if fact and fact.unknown_reason
                else _reason_for_missing(fact_key)
            )
            blocking_facts.append(fact_key)
        elif fact.state == "conflict":
            reasons.append(_reason_for_conflict(fact_key))
            blocking_facts.append(fact_key)
        elif fact.state in {"derived", "assumption"}:
            reasons.append("UNTRUSTED_FACT_BASIS")
            blocking_facts.append(fact_key)
    unique_reasons = tuple(dict.fromkeys(reasons))
    unique_facts = tuple(dict.fromkeys(blocking_facts))
    return EligibilityAssessment(
        entry_id=entry.entry_id,
        use=use,
        eligible=not unique_reasons,
        blocking_reasons=unique_reasons,
        blocking_facts=unique_facts,
    )


class CatalogSnapshot(V2Model):
    schema_version: Literal[CATALOG_SCHEMA_VERSION] = CATALOG_SCHEMA_VERSION
    catalog_version: SafeIdentifier
    sources: tuple[CatalogSource, ...]
    entries: tuple[CatalogEntry, ...]

    @model_validator(mode="after")
    def validate_references(self) -> Self:
        sources = {source.source_id: source for source in self.sources}
        if len(sources) != len(self.sources):
            raise ValueError("catalog source IDs must be unique")
        by_url: dict[str, str] = {}
        for source in self.sources:
            previous = by_url.get(source.url)
            if previous is not None and previous != source.document_sha256:
                raise ValueError("one source URL cannot have conflicting digests")
            by_url[source.url] = source.document_sha256
        entry_ids = [entry.entry_id for entry in self.entries]
        if len(entry_ids) != len(set(entry_ids)):
            raise ValueError("catalog entry IDs must be unique")
        identities = [entry.identity for entry in self.entries]
        if len(identities) != len(set(identities)):
            raise ValueError("duplicate exact manufacturer entries are not allowed")
        for entry in self.entries:
            for fact in entry.facts:
                references = [
                    *(
                        claim.evidence
                        for claim in fact.claims
                        if claim.evidence is not None
                    ),
                    *fact.unknown_evidence,
                ]
                for reference in references:
                    source = sources.get(reference.source_id)
                    if source is None:
                        raise ValueError("fact references an unknown catalog source")
                    if reference.source_url != source.url:
                        raise ValueError(
                            "fact source URL does not match catalog source"
                        )
                    if reference.document_sha256 != source.document_sha256:
                        raise ValueError(
                            "fact source digest does not match catalog source"
                        )
                    if reference.evidence_date != source.evidence_date:
                        raise ValueError(
                            "fact evidence date does not match catalog source"
                        )
                    if source.manufacturer != entry.manufacturer:
                        raise ValueError(
                            "fact evidence manufacturer does not match catalog entry"
                        )
        return self

    @property
    def content_digest(self) -> str:
        return catalog_digest(self)

    def entry(self, entry_id: str) -> CatalogEntry:
        for entry in self.entries:
            if entry.entry_id == entry_id:
                return entry
        raise KeyError(entry_id)

    def assess(self, entry_id: str, use: CatalogUse) -> EligibilityAssessment:
        return assess_eligibility(self.entry(entry_id), use)


class CatalogQuery(V2Model):
    category: CatalogCategory | None = None
    capability: SafeIdentifier | None = None
    eligible_for: CatalogUse | None = None
    eligible_only: StrictBool = False
    blocking_reason: EligibilityReason | None = None
    max_envelope_x_mm: FiniteFloat | None = Field(default=None, gt=0.0)
    max_envelope_y_mm: FiniteFloat | None = Field(default=None, gt=0.0)
    max_envelope_z_mm: FiniteFloat | None = Field(default=None, gt=0.0)
    min_voltage_v: FiniteFloat | None = Field(default=None, gt=0.0)
    max_voltage_v: FiniteFloat | None = Field(default=None, gt=0.0)
    min_current_a: FiniteFloat | None = Field(default=None, gt=0.0)
    max_current_a: FiniteFloat | None = Field(default=None, gt=0.0)
    min_torque_nm: FiniteFloat | None = Field(default=None, gt=0.0)
    max_torque_nm: FiniteFloat | None = Field(default=None, gt=0.0)
    min_speed_rpm: FiniteFloat | None = Field(default=None, gt=0.0)
    max_speed_rpm: FiniteFloat | None = Field(default=None, gt=0.0)
    min_mass_g: FiniteFloat | None = Field(default=None, gt=0.0)
    max_mass_g: FiniteFloat | None = Field(default=None, gt=0.0)
    shaft_profile: SafeText | None = None
    min_shaft_diameter_mm: FiniteFloat | None = Field(default=None, gt=0.0)
    max_shaft_diameter_mm: FiniteFloat | None = Field(default=None, gt=0.0)
    mount_pattern: SafeText | None = None
    connector_mpn: SafeText | None = None

    @model_validator(mode="after")
    def validate_ranges(self) -> Self:
        for lower, upper, name in (
            (self.min_voltage_v, self.max_voltage_v, "voltage"),
            (self.min_current_a, self.max_current_a, "current"),
            (self.min_torque_nm, self.max_torque_nm, "torque"),
            (self.min_speed_rpm, self.max_speed_rpm, "speed"),
            (self.min_mass_g, self.max_mass_g, "mass"),
            (self.min_shaft_diameter_mm, self.max_shaft_diameter_mm, "shaft diameter"),
        ):
            if lower is not None and upper is not None and lower > upper:
                raise ValueError(f"minimum {name} cannot exceed maximum")
        if self.eligible_only and self.eligible_for is None:
            raise ValueError("eligible_only requires eligible_for")
        if self.blocking_reason is not None and self.eligible_for is None:
            raise ValueError("blocking_reason requires eligible_for")
        return self


class CatalogMatch(V2Model):
    entry: CatalogEntry
    eligibility: EligibilityAssessment | None = None


class CatalogQueryResult(V2Model):
    catalog_digest: Sha256
    matches: tuple[CatalogMatch, ...]


def _within(value: float | None, lower: float | None, upper: float | None) -> bool:
    if value is None:
        return False
    return (lower is None or value >= lower) and (upper is None or value <= upper)


def _first_numeric(entry: CatalogEntry, fact_keys: tuple[FactKey, ...]) -> float | None:
    for fact_key in fact_keys:
        value = entry.numeric(fact_key)
        if value is not None:
            return value
    return None


def _operating_voltage_interval(
    entry: CatalogEntry,
) -> tuple[float, float] | None:
    """Return the documented operating interval, expanding a nominal-only point."""

    voltage_fact_keys = (
        "operating_voltage_min_v",
        "operating_voltage_nominal_v",
        "operating_voltage_max_v",
    )
    if any(
        (fact := entry.fact(fact_key)) is not None and fact.state != "known"
        for fact_key in voltage_fact_keys
    ):
        return None
    minimum = entry.numeric("operating_voltage_min_v")
    nominal = entry.numeric("operating_voltage_nominal_v")
    maximum = entry.numeric("operating_voltage_max_v")
    if minimum is None and nominal is not None:
        minimum = nominal
    if maximum is None and nominal is not None:
        maximum = nominal
    if minimum is None or maximum is None:
        return None
    return minimum, maximum


def _covers_interval(
    interval: tuple[float, float] | None,
    minimum: float | None,
    maximum: float | None,
) -> bool:
    """Return whether every requested voltage bound is documented as supported."""

    if interval is None:
        return False
    documented_minimum, documented_maximum = interval
    return all(
        requested is None or documented_minimum <= requested <= documented_maximum
        for requested in (minimum, maximum)
    )


def query_catalog(catalog: CatalogSnapshot, query: CatalogQuery) -> CatalogQueryResult:
    matches: list[CatalogMatch] = []
    for entry in catalog.entries:
        if query.category is not None and entry.category != query.category:
            continue
        if query.capability is not None and query.capability not in entry.capabilities:
            continue
        if (
            query.shaft_profile is not None
            and entry.text("shaft_profile") != query.shaft_profile
        ):
            continue
        if (
            query.mount_pattern is not None
            and entry.text("mount_pattern") != query.mount_pattern
        ):
            continue
        if (
            query.connector_mpn is not None
            and entry.text("connector_mpn") != query.connector_mpn
        ):
            continue
        if (
            not _within(entry.numeric("envelope_x_mm"), None, query.max_envelope_x_mm)
            and query.max_envelope_x_mm is not None
        ):
            continue
        if (
            not _within(entry.numeric("envelope_y_mm"), None, query.max_envelope_y_mm)
            and query.max_envelope_y_mm is not None
        ):
            continue
        if (
            not _within(entry.numeric("envelope_z_mm"), None, query.max_envelope_z_mm)
            and query.max_envelope_z_mm is not None
        ):
            continue
        if query.min_voltage_v is not None or query.max_voltage_v is not None:
            if not _covers_interval(
                _operating_voltage_interval(entry),
                query.min_voltage_v,
                query.max_voltage_v,
            ):
                continue
        if query.min_current_a is not None or query.max_current_a is not None:
            current = _first_numeric(
                entry,
                (
                    "current_continuous_a",
                    "current_limit_a",
                    "current_peak_a",
                    "current_stall_a",
                ),
            )
            if not _within(current, query.min_current_a, query.max_current_a):
                continue
        if query.min_torque_nm is not None or query.max_torque_nm is not None:
            torque = _first_numeric(entry, ("torque_continuous_nm", "torque_stall_nm"))
            if not _within(torque, query.min_torque_nm, query.max_torque_nm):
                continue
        if query.min_speed_rpm is not None or query.max_speed_rpm is not None:
            speed = _first_numeric(
                entry,
                ("speed_nominal_rpm", "speed_max_rpm", "speed_no_load_rpm"),
            )
            if not _within(speed, query.min_speed_rpm, query.max_speed_rpm):
                continue
        if query.min_mass_g is not None or query.max_mass_g is not None:
            if not _within(entry.numeric("mass_g"), query.min_mass_g, query.max_mass_g):
                continue
        if (
            query.min_shaft_diameter_mm is not None
            or query.max_shaft_diameter_mm is not None
        ) and not _within(
            entry.numeric("shaft_diameter_mm"),
            query.min_shaft_diameter_mm,
            query.max_shaft_diameter_mm,
        ):
            continue
        assessment = (
            assess_eligibility(entry, query.eligible_for)
            if query.eligible_for is not None
            else None
        )
        if query.eligible_only and (assessment is None or not assessment.eligible):
            continue
        if query.blocking_reason is not None and (
            assessment is None
            or query.blocking_reason not in assessment.blocking_reasons
        ):
            continue
        matches.append(CatalogMatch(entry=entry, eligibility=assessment))
    return CatalogQueryResult(
        catalog_digest=catalog.content_digest,
        matches=tuple(matches),
    )


def catalog_digest(catalog: CatalogSnapshot) -> str:
    """Hash engineering identity, provenance, and facts, excluding advisory data."""

    payload = catalog.model_dump(mode="json")
    for entry in payload["entries"]:
        entry.pop("advisory", None)
        entry["capabilities"] = sorted(entry["capabilities"])
        entry["facts"] = sorted(entry["facts"], key=lambda fact: fact["fact_key"])
        for fact in entry["facts"]:
            for claim in fact["claims"]:
                claim["derived_from"] = sorted(claim["derived_from"])
            fact["claims"] = sorted(
                fact["claims"],
                key=lambda claim: _canonical_json_bytes(claim),
            )
            fact["unknown_evidence"] = sorted(
                fact["unknown_evidence"],
                key=lambda ref: _canonical_json_bytes(ref),
            )
    payload["entries"] = sorted(payload["entries"], key=lambda entry: entry["entry_id"])
    payload["sources"] = sorted(
        payload["sources"], key=lambda source: source["source_id"]
    )
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def _source(
    source_id: str,
    manufacturer: str,
    title: str,
    url: str,
    media_type: Literal["text/html", "application/pdf", "text/plain"],
    document_sha256: str,
) -> CatalogSource:
    return CatalogSource(
        source_id=source_id,
        manufacturer=manufacturer,
        title=title,
        url=url,
        media_type=media_type,
        document_sha256=document_sha256,
        evidence_date="2026-09-04",
    )


_SOURCES = {
    "cores3-page": _source(
        "cores3-page",
        "M5Stack",
        "CoreS3 product page",
        "https://docs.m5stack.com/en/core/CoreS3",
        "text/html",
        "9a24d4201e8e04bb384ccea8dbc6a232613579f4efbf935f130d9323d78500b5",
    ),
    "cores3-schematic": _source(
        "cores3-schematic",
        "M5Stack",
        "CoreS3 schematic v1.0",
        "https://m5stack-doc.oss-cn-shenzhen.aliyuncs.com/490/Sch_M5_CoreS3_v1.0.pdf",
        "application/pdf",
        "58a15454eccb11d2668e1a9a3ad85943b9a58b104c5f1ed137b790192ec27c04",
    ),
    "goplus2-page": _source(
        "goplus2-page",
        "M5Stack",
        "Module13.2 GoPlus2 product page",
        "https://docs.m5stack.com/en/module/goplus2",
        "text/html",
        "5eea0ec9899b7a18c054bda329c12e0154810d97c24beb3d757d5b424f48c600",
    ),
    "goplus2-pdf": _source(
        "goplus2-pdf",
        "M5Stack",
        "GoPlus2 official model document",
        "https://m5stack-doc.oss-cn-shenzhen.aliyuncs.com/977/goplus2.pdf",
        "application/pdf",
        "b229966cc6d1fc58505df822efd6595f5e33c354b2277efd26debe5bafc3d99c",
    ),
    "goplus2-compatibility": _source(
        "goplus2-compatibility",
        "M5Stack",
        "Stack Compatibility table for CoreS3 and GoPlus2",
        "https://docs.m5stack.com/en/compatible_stack?host=K128&module=M025-B",
        "text/html",
        "d1485770966f92bc869f14f37013ca29dd670a10fd47ea89e204fd4da7ef4cb3",
    ),
    "wheel-1087-specs": _source(
        "wheel-1087-specs",
        "Pololu",
        "Wheel 32x7mm #1087 specifications",
        "https://www.pololu.com/product/1087/specs",
        "text/html",
        "c58d8a1b332f1993e1293940e4fccae526341e9e737f3af79b5365dc52c91077",
    ),
    "wheel-1087-drawing": _source(
        "wheel-1087-drawing",
        "Pololu",
        "Wheel dimensions drawing",
        "https://www.pololu.com/file/0J1708/pololu-wheel-dimensions.pdf",
        "application/pdf",
        "08eb3e501bdc41329dc299361dba27a9835f1fb9dbdd0f806ba637fcecc7f9cd",
    ),
    "caster-950-specs": _source(
        "caster-950-specs",
        "Pololu",
        "Ball caster #950 specifications",
        "https://www.pololu.com/product/950/specs",
        "text/html",
        "d703fbd187a1813d530d98cda8bc9c396eb2b455f850854d42c398c7b8f7a363",
    ),
    "caster-950-drawing": _source(
        "caster-950-drawing",
        "Pololu",
        "Ball caster #950 dimension drawing",
        "https://www.pololu.com/file/0J1636/pololu-ball-caster-with-0.375in-ball.pdf",
        "application/pdf",
        "8c2b8159fbc16246b6469c83d89d31eb6aa87890ee4794c3a6100273ee02a238",
    ),
}


def _evidence(source_id: str, locator: str) -> EvidenceRef:
    source = _SOURCES[source_id]
    return EvidenceRef(
        source_id=source.source_id,
        source_url=source.url,
        locator=locator,
        document_sha256=source.document_sha256,
        evidence_date=source.evidence_date,
    )


def _numeric(
    fact_key: FactKey,
    value: float,
    unit: Unit,
    *,
    source_id: str,
    locator: str,
    scope: str = "component",
    basis: FactBasis = "manufacturer_stated",
    canonical_value: float | None = None,
    canonical_unit: Unit | None = None,
    conversion_rule: ConversionRule = "identity",
) -> CatalogFact:
    expected_unit = _FACT_SPECS[fact_key][1]
    if expected_unit is None:
        raise ValueError(f"{fact_key} is not numeric")
    return CatalogFact(
        fact_key=fact_key,
        claims=(
            NumericClaim(
                original_value=value,
                original_unit=unit,
                canonical_value=value if canonical_value is None else canonical_value,
                canonical_unit=expected_unit
                if canonical_unit is None
                else canonical_unit,
                basis=basis,
                conversion_rule=conversion_rule,
                scope=scope,
                evidence=_evidence(source_id, locator),
            ),
        ),
    )


def _text(
    fact_key: FactKey,
    value: str,
    *,
    source_id: str,
    locator: str,
    scope: str = "component",
) -> CatalogFact:
    return CatalogFact(
        fact_key=fact_key,
        claims=(
            TextClaim(
                original_value=value,
                canonical_value=value,
                basis="manufacturer_stated",
                scope=scope,
                evidence=_evidence(source_id, locator),
            ),
        ),
    )


def _boolean_unknown(
    fact_key: FactKey, reason: EligibilityReason, *source_ids: str
) -> CatalogFact:
    return CatalogFact(
        fact_key=fact_key,
        unknown_reason=reason,
        unknown_evidence=tuple(
            _evidence(
                source_id, "manufacturer document reviewed; no exact fact published"
            )
            for source_id in source_ids
        ),
    )


def _unknown(
    fact_key: FactKey, reason: EligibilityReason, *source_ids: str
) -> CatalogFact:
    return _boolean_unknown(fact_key, reason, *source_ids)


def _conflicting_text(
    fact_key: FactKey,
    first_value: str,
    first_source: str,
    first_locator: str,
    second_value: str,
    second_source: str,
    second_locator: str,
) -> CatalogFact:
    return CatalogFact(
        fact_key=fact_key,
        claims=(
            TextClaim(
                original_value=first_value,
                canonical_value=first_value,
                basis="manufacturer_stated",
                evidence=_evidence(first_source, first_locator),
            ),
            TextClaim(
                original_value=second_value,
                canonical_value=second_value,
                basis="manufacturer_stated",
                evidence=_evidence(second_source, second_locator),
            ),
        ),
    )


def _conflicting_numeric(
    fact_key: FactKey,
    first_value: float,
    first_source: str,
    first_locator: str,
    second_value: float,
    second_source: str,
    second_locator: str,
    unit: Unit,
) -> CatalogFact:
    return CatalogFact(
        fact_key=fact_key,
        claims=(
            NumericClaim(
                original_value=first_value,
                original_unit=unit,
                canonical_value=first_value,
                canonical_unit=_FACT_SPECS[fact_key][1],  # type: ignore[arg-type]
                basis="manufacturer_stated",
                conversion_rule="identity",
                evidence=_evidence(first_source, first_locator),
            ),
            NumericClaim(
                original_value=second_value,
                original_unit=unit,
                canonical_value=second_value,
                canonical_unit=_FACT_SPECS[fact_key][1],  # type: ignore[arg-type]
                basis="manufacturer_stated",
                conversion_rule="identity",
                evidence=_evidence(second_source, second_locator),
            ),
        ),
    )


def _entry(
    entry_id: str,
    manufacturer: str,
    sku: str,
    variant: str,
    category: CatalogCategory,
    facts: tuple[CatalogFact, ...],
    capabilities: tuple[str, ...] = (),
) -> CatalogEntry:
    return CatalogEntry(
        entry_id=entry_id,
        manufacturer=manufacturer,
        manufacturer_sku=sku,
        variant=variant,
        category=category,
        capabilities=capabilities,
        facts=facts,
    )


CORES3_K128 = _entry(
    "m5stack-cores3-k128",
    "M5Stack",
    "K128",
    "CoreS3",
    "controller",
    (
        _numeric(
            "envelope_x_mm",
            54.0,
            "mm",
            source_id="cores3-page",
            locator="Specifications > Product Size > Main unit width",
        ),
        _numeric(
            "envelope_y_mm",
            54.0,
            "mm",
            source_id="cores3-page",
            locator="Specifications > Product Size > Main unit depth",
        ),
        _numeric(
            "envelope_z_mm",
            15.5,
            "mm",
            source_id="cores3-page",
            locator="Specifications > Product Size > Main unit height",
        ),
        _numeric(
            "product_set_mass_g",
            72.7,
            "g",
            source_id="cores3-page",
            locator="Specifications > Product Weight > Whole set (CoreS3+DinBase)",
            scope="whole-set",
        ),
        _numeric(
            "capacity_mah",
            500.0,
            "mAh",
            source_id="cores3-page",
            locator="Description > internal 500mAh battery",
        ),
        _numeric(
            "operating_voltage_min_v",
            9.0,
            "V",
            source_id="cores3-page",
            locator="Description > external DC 12V (9 ~ 24V) supply lower bound",
        ),
        _numeric(
            "operating_voltage_max_v",
            24.0,
            "V",
            source_id="cores3-page",
            locator="Description > external DC 12V (9 ~ 24V) supply upper bound",
        ),
        _text(
            "connector_family",
            "HY2.0-4P",
            source_id="cores3-page",
            locator="PinMap > HY2.0-4P PORT.A/PORT.B/PORT.C",
        ),
        _unknown("mass_g", "MISSING_MASS", "cores3-page", "cores3-schematic"),
        _unknown(
            "current_limit_a",
            "MISSING_CURRENT_LIMIT",
            "cores3-page",
            "cores3-schematic",
        ),
        _unknown(
            "rail_current_limit_a",
            "MISSING_CURRENT_LIMIT",
            "cores3-page",
            "cores3-schematic",
        ),
        _unknown(
            "connector_mpn", "MISSING_CONNECTOR", "cores3-page", "cores3-schematic"
        ),
        _unknown(
            "battery_protection",
            "MISSING_PROTECTION",
            "cores3-page",
            "cores3-schematic",
        ),
        _unknown(
            "power_isolation",
            "MISSING_POWER_ISOLATION",
            "cores3-page",
            "cores3-schematic",
        ),
        _unknown("revision", "MISSING_REVISION", "cores3-page", "cores3-schematic"),
    ),
    (
        "camera",
        "display",
        "imu",
        "microphone",
        "speaker",
        "wifi",
        "usb-c",
    ),
)


GOPLUS2_M025_B = _entry(
    "m5stack-goplus2-m025-b",
    "M5Stack",
    "M025-B",
    "Module13.2 GoPlus2",
    "motor_driver",
    (
        _numeric(
            "envelope_x_mm",
            54.0,
            "mm",
            source_id="goplus2-page",
            locator="Specifications > Product Size > 54.0 x 54.0 x 13.0mm",
        ),
        _numeric(
            "envelope_y_mm",
            54.0,
            "mm",
            source_id="goplus2-page",
            locator="Specifications > Product Size > 54.0 x 54.0 x 13.0mm",
        ),
        _conflicting_numeric(
            "envelope_z_mm",
            13.0,
            "goplus2-page",
            "Specifications > Product Size > 13.0mm",
            13.2,
            "goplus2-pdf",
            "Model size drawing > thickness callout 13.2 mm",
            "mm",
        ),
        _numeric(
            "mass_g",
            38.0,
            "g",
            source_id="goplus2-page",
            locator="Specifications > Product Weight > 38.0g",
        ),
        _numeric(
            "capacity_mah",
            500.0,
            "mAh",
            source_id="goplus2-page",
            locator="Specifications > Battery > 500mAh",
        ),
        _text(
            "motor_driver_model",
            "DRV8833",
            source_id="goplus2-page",
            locator="Specifications > Motor Driver > DRV8833",
        ),
        _text(
            "communication_protocol",
            "I2C @ 0x38",
            source_id="goplus2-page",
            locator="Specifications > Communication > I2C Communication @0x38",
        ),
        _conflicting_text(
            "ir_pin_map",
            "IR_IN pin 2; IR_OUT pin 22",
            "goplus2-page",
            "PinMap > M5-Bus > IR_IN/IR_OUT",
            "IR_RX pin 2; IR_TX pin 20",
            "goplus2-compatibility",
            "Rendered table > Module13.2 GoPlus2 M1 > pin 2 IR_RX / pin 20 IR_TX",
        ),
        _unknown(
            "operating_voltage_min_v",
            "MISSING_OPERATING_VOLTAGE",
            "goplus2-page",
            "goplus2-pdf",
        ),
        _unknown(
            "operating_voltage_max_v",
            "MISSING_OPERATING_VOLTAGE",
            "goplus2-page",
            "goplus2-pdf",
        ),
        _unknown(
            "current_continuous_a",
            "MISSING_CURRENT_LIMIT",
            "goplus2-page",
            "goplus2-pdf",
        ),
        _unknown(
            "current_peak_a", "MISSING_CURRENT_LIMIT", "goplus2-page", "goplus2-pdf"
        ),
        _unknown(
            "thermal_limit_c", "MISSING_THERMAL_LIMIT", "goplus2-page", "goplus2-pdf"
        ),
        _unknown("connector_mpn", "MISSING_CONNECTOR", "goplus2-page", "goplus2-pdf"),
        _unknown("revision", "MISSING_REVISION", "goplus2-page", "goplus2-pdf"),
        _unknown(
            "power_isolation", "MISSING_POWER_ISOLATION", "goplus2-page", "goplus2-pdf"
        ),
    ),
    (
        "dc-motor-channels-2",
        "servo-channels-4",
        "infrared",
        "i2c",
    ),
)


POLOLU_WHEEL_1087 = _entry(
    "pololu-wheel-1087",
    "Pololu",
    "#1087",
    "Wheel 32x7mm Pair - Black",
    "wheel",
    (
        _numeric(
            "envelope_x_mm",
            32.0,
            "mm",
            source_id="wheel-1087-specs",
            locator="Dimensions > Size > 32 x 7 mm (diameter axis)",
        ),
        _numeric(
            "envelope_y_mm",
            32.0,
            "mm",
            source_id="wheel-1087-specs",
            locator="Dimensions > Size > 32 x 7 mm (diameter axis)",
        ),
        _numeric(
            "envelope_z_mm",
            7.0,
            "mm",
            source_id="wheel-1087-specs",
            locator="Dimensions > Size > 32 x 7 mm (width)",
        ),
        _numeric(
            "outer_diameter_mm",
            31.0,
            "mm",
            source_id="wheel-1087-drawing",
            locator="Front view with tire > diameter callout",
        ),
        _numeric(
            "part_width_mm",
            7.0,
            "mm",
            source_id="wheel-1087-drawing",
            locator="Profile view > wheel width",
        ),
        _numeric(
            "mass_g",
            0.11,
            "oz",
            source_id="wheel-1087-specs",
            locator="Dimensions > Weight > 0.11 oz per wheel including tire",
            scope="per-wheel",
            basis="converted",
            canonical_value=0.11 * 28.349523125,
            canonical_unit="g",
            conversion_rule="oz_to_g",
        ),
        _numeric(
            "shaft_diameter_mm",
            3.0,
            "mm",
            source_id="wheel-1087-specs",
            locator="Dimensions > Shaft diameter > 3 mm",
        ),
        _text(
            "shaft_profile",
            "3mm D-shaft press-fit",
            source_id="wheel-1087-specs",
            locator="Notes > D-shaped hole for press fit",
        ),
        _numeric(
            "quantity_per_pack",
            2.0,
            "none",
            source_id="wheel-1087-specs",
            locator="Description > This product is a pair of wheels",
        ),
        _text(
            "material",
            "ABS hub and silicone tire",
            source_id="wheel-1087-drawing",
            locator="Title block > Material: ABS & silicone",
        ),
    ),
    ("d-shaft-3mm", "press-fit", "differential-drive"),
)


POLOLU_CASTER_950 = _entry(
    "pololu-ball-caster-950",
    "Pololu",
    "#950",
    "Ball Caster with 3/8in Plastic Ball, body-only install",
    "caster",
    (
        _numeric(
            "envelope_x_mm",
            19.1,
            "mm",
            source_id="caster-950-drawing",
            locator="Dimension drawing > overall mounting length 19.1 mm",
        ),
        _numeric(
            "envelope_y_mm",
            12.1,
            "mm",
            source_id="caster-950-drawing",
            locator="Dimension drawing > overall body width 12.1 mm",
        ),
        _numeric(
            "envelope_z_mm",
            10.1,
            "mm",
            source_id="caster-950-drawing",
            locator="Dimension drawing > height without spacer 10.1 mm",
        ),
        _numeric(
            "ball_diameter_mm",
            0.375,
            "in",
            source_id="caster-950-specs",
            locator="Dimensions > Ball diameter > 0.375 in",
            basis="converted",
            canonical_value=0.375 * 25.4,
            canonical_unit="mm",
            conversion_rule="in_to_mm",
        ),
        _numeric(
            "mount_hole_spacing_mm",
            13.5,
            "mm",
            source_id="caster-950-drawing",
            locator="Top view > screw-hole center distance 13.5 mm",
        ),
        _numeric(
            "mount_hole_diameter_mm",
            2.3,
            "mm",
            source_id="caster-950-drawing",
            locator="Top view > 2x diameter 2.3 mm for #2/M2 screws",
        ),
        _numeric(
            "mass_g",
            0.8,
            "g",
            source_id="caster-950-specs",
            locator="Dimensions > Weight > 0.8 g without screws or spacers",
            scope="body-without-hardware",
        ),
        _text(
            "mount_pattern",
            "2x #2/M2 screw holes",
            source_id="caster-950-drawing",
            locator="Notes > Intended for #2 and M2 screws",
        ),
        _text(
            "material",
            "plastic ball",
            source_id="caster-950-specs",
            locator="General specifications > Ball material: plastic",
        ),
    ),
    ("ball-caster", "plastic-ball", "m2-mount"),
)


OFFICIAL_CATALOG_V2 = CatalogSnapshot(
    catalog_version="official-v2-20260904",
    sources=tuple(_SOURCES.values()),
    entries=(CORES3_K128, GOPLUS2_M025_B, POLOLU_WHEEL_1087, POLOLU_CASTER_950),
)


__all__ = [
    "AdvisoryData",
    "BooleanClaim",
    "CATALOG_SCHEMA_VERSION",
    "CatalogCategory",
    "CatalogEntry",
    "CatalogFact",
    "CatalogMatch",
    "CatalogQuery",
    "CatalogQueryResult",
    "CatalogSnapshot",
    "CatalogSource",
    "CatalogUse",
    "ConversionRule",
    "EligibilityAssessment",
    "EligibilityReason",
    "EvidenceRef",
    "FactBasis",
    "FactClaim",
    "FactKey",
    "FactKind",
    "FactState",
    "GOPLUS2_M025_B",
    "CORES3_K128",
    "NumericClaim",
    "OFFICIAL_CATALOG_V2",
    "POLOLU_CASTER_950",
    "POLOLU_WHEEL_1087",
    "TextClaim",
    "Unit",
    "assess_eligibility",
    "catalog_digest",
    "query_catalog",
]
