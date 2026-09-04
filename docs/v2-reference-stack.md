# V2 reference power and drive stack

`src/character_robot/v2/reference_stack.py` contains the machine-readable
snapshot for Issue #102. It composes the provenance contract from Issue #89;
it does not add a caller-controlled eligibility flag and it does not claim a
physical build or safety qualification.

## Status contract

`ReferenceStackSnapshot.readiness` is derived from the selected catalog entries
and the explicit gates:

| Field | Meaning for this snapshot |
| --- | --- |
| `stack_definition_complete` | The digital stack package is structurally complete: every role, source observation, topology, calculation, assumption, and gate is present. It is stack-scoped and does not mean the whole-robot design is complete, the stack is eligible, or the robot is physically built. |
| `datasheet_candidate` | The package is complete but one or more catalog/stack gates block promotion. |
| `datasheet_eligible` | Every selected entry (including off-topology charger/fallback records) passes the Issue #89 use-specific eligibility function and no digital stack gate blocks. |
| `datasheet_checked` | Derived from `datasheet_eligible`; it is `false` for this candidate. |
| `physically_qualified` | Always `false` in this issue. |
| `physical_verification_pending` | Always `true`; fit, load, inrush, thermal, duty, and E-stop interruption remain physical gates. |

The current snapshot deliberately reports:

```text
stack_definition_complete=true
datasheet_candidate=true
datasheet_eligible=false
datasheet_checked=false
physically_qualified=false
physical_verification_pending=true
```

Unknown, conflict, assumption, and derived-only facts remain blockers. A
planning assumption is never copied into a `CatalogFact` as a manufacturer
claim. `active=false` is reserved for the off-robot charger and unused
regulator fallback; every controller or actuator-topology selection must stay
active and the snapshot validator enforces that boundary.

## Selected identities

The snapshot selects the smallest reviewed mixed COTS candidate that keeps the
controller separate and uses a native DYNAMIXEL head branch. The wheel branch
uses an official Pololu motor because the current #89 taxonomy does not allow
one exact DYNAMIXEL servo identity to be silently reclassified as both a
`motor` and a `servo`.

| Role | Manufacturer / exact SKU | Qty | Catalog use | Package scope |
| --- | --- | ---: | --- | --- |
| controller | M5Stack `K128`, CoreS3 | 1 | `controller_isolated` | CoreS3 controller; DinBase is whole-set context |
| drive motor | Pololu `#4869` | 2 | `motor_drive` | One 227:1 25Dx71L MP 12 V encoder gearmotor per side |
| wheel/hub | Pololu `#1087` | 1 pair | `wheel_drive` | 32×7 mm black wheel pair |
| head actuator | ROBOTIS `902-0135-000`, XL430-W250-T | 2 | `head_servo` | One per head/flipper side |
| head horn | ROBOTIS `HN11-N101` | 2 | `head_horn` | One assembled horn per XL430 |
| caster | Pololu `#950` | 1 | `caster` | Body-only geometry; included hardware is separate |
| battery | Bioenno Power `BLF-1206A` | 1 | `battery` | 12 V 6 Ah PCM-protected LiFePO4 |
| charger | Bioenno Power `BPC-1502DC` | 1 | `charger` | 14.6 V 2 A LiFePO4 charger, off-robot |
| regulator | Pololu `#2851` D24V50F5 | 1 | `regulator` | Fallback only; not energized in the internal-battery plan |
| DYNAMIXEL interface | ROBOTIS `902-0146-001` | 1 | `motor_driver` | MKR-series TTL interface |
| wheel motor driver | Pololu `#2520` | 1 | `motor_driver` | Dual TB9051FTG shield |
| fuse | Littelfuse `0287020.U` | 1 | `protection` | ATOF 20 A, 32 VDC |
| fuse holder | Littelfuse `0FHA0002ZXJA` | 1 | `protection` | ATO FHA inline holder, 12 AWG leads |
| main switch | Blue Sea Systems `6006` | 1 | `main_switch` | Manual battery isolation; not the E-stop contact |
| force-guided relay | TE Connectivity `1393260-4` / SR6B4012 | 2 | `protection` | One independent NO contact set per actuator branch |
| physical E-stop | Schneider Electric `XB5AS8442` | 1 | `e_stop` | Red 40 mm turn-to-release mushroom, 1NC |
| battery housing | Anderson Power Products `1327` | 1 | `connector` | PP30 red housing candidate |
| battery contacts | Anderson Power Products `1331-BK` | 2 | `connector` | PP30 15–45 A, 16–12 AWG contacts |
| actuator cable | ROBOTIS `Robot Cable-X3P` | 4 | `connector` | 180 mm TTL cable, one per XL430 |
| high-current wire | Alpha Wire `461219` | 1 | `wire` | 12 AWG, 600 V hook-up wire; cut lengths unresolved |
| caster fastener scope | Pololu `#950` package | 1 | `fastener` | Included #2 screws/nuts; separate MPN unpublished |
| chassis insert | SPIROL `151332` | 4 | `insert` | M3×0.5 Series 10 insert |
| chassis spacer | Essentra Components `13RS018725` | 4 | `spacer` | M3, 7.9 mm PA spacer |

The catalog also retains ROBOTIS TB3 Wheel/Tire Set-ISW-01 (`903-0260-000`)
as an audited, unselected alternative. Its official page/drawing index did
not provide machine-readable wheel mass, envelope, shaft, or horn-hole facts,
so it is not promoted as the active wheel identity.

## Typed power topology

```text
CoreS3 internal 500 mAh battery (controller branch)
  -> CoreS3 only
  -> no actuator VDD or signal backfeed is assumed

BLF-1206A PP30 (+)
  -> TBD ampacity-qualified conductor
  -> ATOF 0287020.U + 0FHA0002ZXJA
  -> Blue Sea 6006 manual switch
  -> SR6 NO-A -> Pololu #2520 -> Pololu #4869 x2
  -> SR6 NO-B -> ROBOTIS MKR Shield -> XL430-W250-T x2

XB5AS8442 1NC
  -> SR6 12 V coil/control path
  -> both NO contacts open on E-stop
  -> controller branch remains powered for fault reporting
```

The two SR6 NO contacts are independent and explicitly not paralleled. The
Schneider NC contact is not placed in the motor/servo current path. TE's
purpose-qualified evidence records 8 A at 250 VAC for the SR6 contact and a
12 VDC coil; it does not publish the intended 12 VDC electronic actuator-load
or inrush class, so that applicability remains an explicit blocker. The
controller-branch separation and actuator de-energization are design intent
backed by planning-assumption IDs, not a claim that the assembly has passed a
physical test.

## Published-value calculations

The snapshot stores calculations separately from source facts:

| Calculation | Result | Interpretation |
| --- | ---: | --- |
| XL430 head stall | `2 × 1.4 A = 2.8 A` | Stall bound only; continuous duty is unknown |
| Pololu wheel-motor stall | `2 × 1.8 A = 3.6 A` | Product page labels current extrapolated |
| Composed stall bound | `2.8 + 3.6 = 6.4 A` | Not a claim of simultaneous physical stall |
| Bioenno continuous-current ratio | `12 / 6.4 = 1.875` | Preliminary source-current ratio only |
| Indicative no-load wheel speed | `π × 0.032 × 35 / 60 = 0.0586 m/s` | No-load indicator, not loaded nominal speed |

Each published-value row carries a typed operation, selected-entry IDs aligned
with each input fact, and the stored result. Snapshot validation recomputes the
finite result from those selected quantities and known catalog values; changing
the number without changing the inputs is rejected.

No nominal current, wire ampacity, fuse clearing, duty value, or 12 VDC
electronic relay-contact rating is invented from a product family or from the
DRV8833 silicon datasheet. TE's purpose-qualified relay evidence records the
published 8 A / 250 VAC contact rating and 12 VDC coil rating separately; it
does not make those ratings a 12 VDC electronic-load qualification.

## Official source evidence

Every claim in the catalog points to an `EvidenceRef` containing the exact URL,
locator, source digest, and evidence date. The source table below is the
machine-readable snapshot's source inventory. HTML digests are explicitly
marked as dynamic observations: they hash the response bytes observed on
2026-09-04 and are not treated as permanent document identities. PDF rows hash
the retrieved response bytes.

| Source | URL | Digest |
| --- | --- | --- |
| CoreS3 docs | <https://docs.m5stack.com/en/core/CoreS3> | `9a24d4201e8e04bb384ccea8dbc6a232613579f4efbf935f130d9323d78500b5` |
| CoreS3 schematic | <https://m5stack-doc.oss-cn-shenzhen.aliyuncs.com/490/Sch_M5_CoreS3_v1.0.pdf> | `58a15454eccb11d2668e1a9a3ad85943b9a58b104c5f1ed137b790192ec27c04` |
| Pololu #1087 specs | <https://www.pololu.com/product/1087/specs> | `c58d8a1b332f1993e1293940e4fccae526341e9e737f3af79b5365dc52c91077` |
| Pololu wheel drawing | <https://www.pololu.com/file/0J1708/pololu-wheel-dimensions.pdf> | `08eb3e501bdc41329dc299361dba27a9835f1fb9dbdd0f806ba637fcecc7f9cd` |
| Pololu #950 specs | <https://www.pololu.com/product/950/specs> | `d703fbd187a1813d530d98cda8bc9c396eb2b455f850854d42c398c7b8f7a363` |
| Pololu #950 drawing | <https://www.pololu.com/file/0J1636/pololu-ball-caster-with-0.375in-ball.pdf> | `8c2b8159fbc16246b6469c83d89d31eb6aa87890ee4794c3a6100273ee02a238` |
| ROBOTIS XL430 product (generic response; unavailable for claims) | <https://en.robotis.com/shop_en/item.php?it_id=902-0135-000> | `010a48a8ebe76af206ed505ea0b050b46b2812ed94544e43a3a151f91cdc8958` |
| ROBOTIS XL430 e-Manual | <https://emanual.robotis.com/docs/en/dxl/x/xl430-w250/> | `49fed82a3b39539c8c65ee8be23013f06541a209adbeda28612cf269a6d4d52a` |
| ROBOTIS TB3 wheel product (generic response; unavailable for claims) | <https://en.robotis.com/shop_en/item.php?it_id=903-0260-000> | `010a48a8ebe76af206ed505ea0b050b46b2812ed94544e43a3a151f91cdc8958` |
| ROBOTIS TB3 drawing index (generic response; unavailable for claims) | <https://en.robotis.com/service/downloadpage.php?ca_id=70> | `010a48a8ebe76af206ed505ea0b050b46b2812ed94544e43a3a151f91cdc8958` |
| ROBOTIS MKR product (generic response; unavailable for claims) | <https://en.robotis.com/shop_en/item.php?it_id=902-0146-001> | `010a48a8ebe76af206ed505ea0b050b46b2812ed94544e43a3a151f91cdc8958` |
| ROBOTIS MKR docs | <https://docs.robotis.com/docs/parts/interface/mkr_shield/> | `fbe6de5a399eb0fb1f2df17128177d00700df8bb850ec2c007ecff9d83af1805` |
| ROBOTIS MKR e-Manual | <https://emanual.robotis.com/docs/en/parts/interface/mkr_shield/> | `f7a9f10afd838727277c71b3c5b3965518140aab2fb980e54d2b6dc2274e4745` |
| Pololu #4869 specs | <https://www.pololu.com/product/4869/specs> | `d9422600dd9e57ef2ae09d56dc27ff66bd6d32b41ebb6a6bd7ab6aad8e46ca74` |
| Pololu 25D datasheet | <https://www.pololu.com/file/0J1829/pololu-25d-metal-gearmotors.pdf> | `a2db2ebd88546f6bdbf0a3e2ee9a45e211151abed10f6748a8839be30a1d4f10` |
| Pololu #2520 specs | <https://www.pololu.com/product/2520/specs> | `b6f5f06a981d7f029cf3add2e52c041c731c49dd8548671e9e1f0d0daa66e796` |
| Pololu #2851 specs | <https://www.pololu.com/product/2851/specs> | `014da936e009686caa0c06fbaf935a036100e2622d04b9ab5bbfd96491483fa4` |
| Bioenno BLF-1206A | <https://www.bioennopower.com/en-gb/products/12v-6ah-lifepo4-battery-pvc> | `a0bc0a394f3f2f7ae92b6321cda0cc2b4cb9c9bd39e52cf5dd0d9857bdc5b02b` |
| Bioenno BPC-1502DC | <https://www.bioennopower.com/en-gb/products/lithium-12v-2a-amp-lifepo4-battery-charger> | `5c1e10c88c444ca603a1e9449f3ca31d9b4591716e45b98eb38641c624290a7d` |
| Bioenno BPC-1502DC US page | <https://www.bioennopower.com/products/lithium-12v-2a-amp-lifepo4-battery-charger> | `0e30361cf45376fb08d1c478de2ce4e1f332bbd1d2ac751fc15ab06708197f84` |
| Littelfuse ATOF | <https://www.littelfuse.com/de/products/fuses-overcurrent-protection/fuses/automotive-fuses/blade-fuses-shunt/atof/287/0287020-u> | `d473a45e309fd67868a0535aacc3f560ce57f14abb4550d6862dcadcc96ff380` |
| Littelfuse holder | <https://www.littelfuse.com/assetdocs/littelfuse-fuse-holder-ato-fha-datasheet?assetguid=988addec-bfe3-4ea2-9204-e2982cbb488e> | `05b49feda42c6acf013d9246ce94a95c3add9a3d63d2b5c88dead6ab55b14a6a` |
| Blue Sea 6006 | <https://www.bluesea.com/products/6006/m-Series_Battery_Switch_-_On-Off> | `df6001c815a084467d79d4b499a5dc31562034e077a6da28fee920745bf6c923` |
| Blue Sea 6006 drawing | <https://d2pyqm2yd3fw2i.cloudfront.net/files/resources/dimensioned_drawing/M_Switch_Knob.pdf> | `7cc74f7fbfefeb04505edb6118d069b5733d724e912a46875f926bb305f3f980` |
| TE SR6 product | <https://www.te.com/en/product-1393260-4.html> | `7ae9f7546a316882223f3c04c2ccbc917a260486780c1628fe6b67a2a934efd3` |
| TE SR6 datasheet | <https://www.te.com/commerce/DocumentDelivery/DDEController?Action=srchrtrv&DocFormat=pdf&DocLang=English&DocNm=SR6&DocType=Data+Sheet&PartCntxt=1393260-4> | `7054dbcde9e6e2573020563cca0e487533c2c60764c00b7cf2898c2edc6aaa70` |
| TE SR6 brochure | <https://www.te.com/content/dam/te-com/documents/industrial/global/schrack-force-guided-relays.pdf> | `749d4bd0cc995f0048c623a8a8e819520e933ad61d4a279b25be69bd1628ad57` |
| Schneider XB5AS8442 | <https://iportal.se.com/Contents/docs/SQD-XB5AS8442_DATA%20SHEET.PDF> | `2bf2454adb85e792c717537c71d32394b8b7e7535157648c45c56b73c1b48b8f` |
| Anderson PP30 datasheet | <https://www.andersonpower.com/content/dam/app/ecommerce/product-pdfs/PP30-P-S/ds-pp30ps.pdf> | `ece5128dad70938d13b63f8cb08a069e9e1e6e079ff0e0d218000cde1f19f6aa` |
| Anderson 1327 housing | <https://www.andersonpower.com/product/powerpole-connector-housing-red/> | `b45087cb522410e70da5ebf79646d3af85fafc34fd0d57e4a6bd748fdf93796d` |
| Anderson 1331-BK contact | <https://www.andersonpower.com/product/powerpole-15-45-silver-plated-power-contacts-16-12-awg-bk/> | `c1c16afa7c698dea6f121c7750fd808834f34a8130dd170e6f1bf33188f9f89e` |
| Alpha Wire 461219 | <https://www.alphawire.com/products/wire/hook-up-wire/premium/461219> | `1e72466b3bb3ef9b7e64e712c596c7ead7ed880d1bda100d52086b9b8e37ba17` |
| SPIROL 151332 | <https://shop.spirol.com/item/self-tapping-inserts/series-10-thread-form-self-tapping-insert-metric/151332> | `a88bc9620b921ed0a68c3dc6aa41c8df1fab1f91ee547017164811ab6f23c06c` |
| Essentra 13RS018725 | <https://www.essentracomponents.com/en-gb/p/round-unthreaded-pa-spacers/13rs018725> | `9bc4eab6eac0c9f7fe76ca67abafe789d46dc1c5b3f2228bbce01872d58ab45b` |

The module also records the ROBOTIS shop/e-Manual variants, TB3 wheel index,
Anderson housing/contact pages, Blue Sea dimension drawing, and localized
Bioenno charger page as additional source observations. The four observed
ROBOTIS shop/download responses with the repeated generic digest are marked
unavailable for affirmative claims; they may only document why a fact remains
unknown. Their exact locators and digests are kept next to the facts rather
than inferred from distributor listings.

## Stable blockers

`readiness.blocking_codes` contains the Issue #89 reason codes and the digital
stack gate IDs below. The physical gate IDs are listed as pending qualification
records as well, but they are intentionally not promoted to datasheet
eligibility blockers by the derived digital status.

```text
missing-cores3-power-endpoint
missing-xl430-continuous-duty
missing-xl430-horn-geometry
missing-mkr-rating-and-endpoint
missing-robotis-commercial-mpn-evidence
missing-bioenno-pp30-mating-mpn
missing-fuse-inrush-coordination
missing-wire-ampacity
missing-sr6-electronic-load-class
missing-fastener-identity
wheel-shaft-adapter-unresolved
physical-180mm-envelope
physical-estop-interruption
physical-inrush-thermal-duty
physical-chassis-strength
```

## Boundary for Issue #90

Issue #90 may consume `REFERENCE_STACK_CATALOG` only after selecting catalog
entries that pass `assess_eligibility` for their exact use and after the stack
digital gates are closed. A candidate snapshot may be persisted as partial
planning data with explicit blockers, but it must not be bound as an eligible
architecture. If a future architecture record has a `design_complete` field,
that field must not be inferred from this candidate; in this module the scoped
signal is named `stack_definition_complete`. The project-level
`design_complete`, project readiness, or physical verification `checked` state
remain separate and unproven.

GoPlus2 is absent from the active topology. Its DRV8833 label is not used as a
module current or thermal rating.
