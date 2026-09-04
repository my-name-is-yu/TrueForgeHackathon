# Character Robot Studio V2 component catalog

This document describes the first V2 catalog snapshot. It is a datasheet
provenance catalog, not a physical qualification record. A catalog entry does
not make an assembled robot safe, build-ready, or replication-ready.

## Contract

Every engineering fact keeps its original value and unit, canonical value and
unit, evidence locator, document SHA-256, and evidence date. Manufacturer
claims, exact unit conversions, derived values, assumptions, unknowns, and
conflicts are distinct. Eligibility is derived for a concrete use from the
facts; no entry accepts a caller-supplied `eligible` flag.
Identity claims keep `original_value == canonical_value`; non-identity numeric
conversions are recomputed and accept only a one-ULP floating-point difference.
Required physical capability and rating values are strictly positive; domain
specific exceptions such as signed thermal limits and AWG 0 retain their valid
zero or signed representations.
Source and evidence URLs require HTTPS with a non-empty authority.
Each source declares one or more exact covered SKU/variant identities. Every
claim or unknown-evidence reference must cite a source that covers the entry's
exact identity; matching only the manufacturer is insufficient. Coverage keeps
the source's labels verbatim (no case or whitespace normalization).
Voltage queries check requested bounds against the documented operating interval,
not a preferred nominal scalar.
Generic current, torque, and speed bounds are conservative: every known rating
in the family (continuous/peak/stall, continuous/stall, or nominal/no-load/max)
must satisfy the requested bounds; no rating is selected as a preferred scalar.
Required facts must also use a scope compatible with the requested use. Component
is the default scope; `wheel_drive` explicitly permits `per-wheel`, and `caster`
explicitly permits `body-without-hardware`. A `whole-set` claim never satisfies a
component-level requirement.
The `e_stop` use admits only entries categorized as `e_stop`; a generic `switch`
does not establish emergency-stop semantics.

Price and availability are timestamped advisory fields. They are intentionally
excluded from the immutable engineering digest.

## Official sources captured on 2026-09-04

| Source | URL | Covered exact identity (SKU / variant) | Document SHA-256 |
| --- | --- | --- | --- |
| CoreS3 product page | <https://docs.m5stack.com/en/core/CoreS3> | `K128 / CoreS3` | `9a24d4201e8e04bb384ccea8dbc6a232613579f4efbf935f130d9323d78500b5` |
| CoreS3 schematic v1.0 | <https://m5stack-doc.oss-cn-shenzhen.aliyuncs.com/490/Sch_M5_CoreS3_v1.0.pdf> | `K128 / CoreS3` | `58a15454eccb11d2668e1a9a3ad85943b9a58b104c5f1ed137b790192ec27c04` |
| GoPlus2 product page | <https://docs.m5stack.com/en/module/goplus2> | `M025-B / Module13.2 GoPlus2` | `5eea0ec9899b7a18c054bda329c12e0154810d97c24beb3d757d5b424f48c600` |
| GoPlus2 official model document | <https://m5stack-doc.oss-cn-shenzhen.aliyuncs.com/977/goplus2.pdf> | `M025-B / Module13.2 GoPlus2` | `b229966cc6d1fc58505df822efd6595f5e33c354b2277efd26debe5bafc3d99c` |
| GoPlus2 Stack Compatibility table | <https://docs.m5stack.com/en/compatible_stack?host=K128&module=M025-B> | `K128 / CoreS3`; `M025-B / Module13.2 GoPlus2` | `d1485770966f92bc869f14f37013ca29dd670a10fd47ea89e204fd4da7ef4cb3` |
| Pololu #1087 specifications | <https://www.pololu.com/product/1087/specs> | `#1087 / Wheel 32x7mm Pair - Black` | `c58d8a1b332f1993e1293940e4fccae526341e9e737f3af79b5365dc52c91077` |
| Pololu wheel dimension drawing | <https://www.pololu.com/file/0J1708/pololu-wheel-dimensions.pdf> | `#1087 / Wheel 32x7mm Pair - Black` | `08eb3e501bdc41329dc299361dba27a9835f1fb9dbdd0f806ba637fcecc7f9cd` |
| Pololu #950 specifications | <https://www.pololu.com/product/950/specs> | `#950 / Ball Caster with 3/8in Plastic Ball, body-only install` | `d703fbd187a1813d530d98cda8bc9c396eb2b455f850854d42c398c7b8f7a363` |
| Pololu #950 dimension drawing | <https://www.pololu.com/file/0J1636/pololu-ball-caster-with-0.375in-ball.pdf> | `#950 / Ball Caster with 3/8in Plastic Ball, body-only install` | `8c2b8159fbc16246b6469c83d89d31eb6aa87890ee4794c3a6100273ee02a238` |

The digests identify the exact bytes retrieved on the evidence date. A future
source change must create a new catalog snapshot rather than silently changing
the current one. The Stack Compatibility and Pololu specification pages are
dynamically rendered; their digests are observations of the captured HTTP
responses, and the raw responses/rendered tables are not stored in this PR.
Consequently, the digest alone does not make those page claims independently
reproducible after the live pages change.

## Seed entries and eligibility

### M5Stack CoreS3, SKU `K128`

The catalog records the documented 54 × 54 × 15.5 mm main-unit envelope,
500 mAh internal battery, and 9–24 V external supply range. The documented
72.7 g weight is scoped to the whole CoreS3 plus DinBase set, so it is not
silently used as the bare-controller mass.

The exact bare-controller mass, current budget and rail limits, exact mating
connector MPNs, battery protection details, power isolation, and published
revision are explicit unknowns. CoreS3 is eligible only for the narrow
`controller_isolated` catalog use, which does not claim a motor/servo power
path. It remains blocked for motor-power-stage use and downstream design work
must carry the unknown mass and isolation facts forward.

### M5Stack GoPlus2, SKU `M025-B`

The catalog records the documented 54 × 54 mm footprint, 38 g product weight,
500 mAh battery, two DC motor channels, four servo channels, DRV8833 model
label, and I²C address. It preserves the 13.0 mm product-page dimension versus
the 13.2 mm model/title dimension as a conflict, and preserves the conflicting
IR pin-map claims from the product page (`IR_IN`/`IR_OUT` on pins 2/22) and the
Stack Compatibility table (`IR_RX`/`IR_TX` on pins 2/20).

Board-level current and thermal limits, exact connector MPNs, module power
isolation, and a current-sold revision are unknown. DRV8833 silicon ratings are
not treated as GoPlus2 module ratings. GoPlus2 is therefore ineligible for
`board_motor_stage` and is never a default motor/servo power stage.

### Pololu wheel, item `#1087`

The catalog records the 32 × 7 mm black wheel pair, 3 mm D-shaft press-fit
interface, and ABS/silicone materials. The source's 0.11 oz weight is kept as
the original per-wheel value and converted exactly to grams. The pair quantity
is recorded separately from the per-wheel mass scope. The entry is eligible
for `wheel_drive`.

### Pololu ball caster, item `#950`

The catalog records the body-only, no-spacer installation: 19.1 × 12.1 × 10.1
mm envelope, 3/8-inch plastic ball converted to 9.525 mm, 13.5 mm screw-hole
spacing, 2.3 mm hole diameter, and 0.8 g body mass. The manufacturer notes
that the mass excludes screws and spacers; those hardware masses are not
invented. The entry is eligible for the body-only `caster` geometry use, while
the complete installed assembly remains a downstream unknown until its
fasteners and spacers are selected.

## Scope boundary

This PR does not select the whole-robot reference stack, alter the V2 project
store, add architecture/CAD/solver/electrical/runtime behavior, or claim
physical qualification. Reference-stack promotion belongs to Issue #102.
