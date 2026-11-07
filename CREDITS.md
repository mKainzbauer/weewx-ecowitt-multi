# Credits

<!-- SPDX-License-Identifier: GPL-3.0-or-later -->

weewx-ecowitt-multi builds on other people's work. Thank you to:

- **Ian Millard**: [weewx-EcowittGateway](https://github.com/Millardiang/weewx-ecowittGateway).
  Large parts of the parser, field mapping, SD and Ecowitt.net catchup, and helpers are adapted
  from his driver.
- **Gary Roderick (gjr80)**: the GW1000 / `ecowitt_http.py` drivers, which are the basis of
  weewx-EcowittGateway. Field names, the command-line tools and many sensor details come from
  his long-term work.
- **Tom Keffer, Matthew Wall and the WeeWX team**: [WeeWX](https://github.com/weewx/weewx).
  The driver API, the catchup mechanism and the wview_extended schema.
- **Rich Bell**: [WeeWX-MQTTSubscribe](https://github.com/bellrichm/WeeWX-MQTTSubscribe).
  The alternative architecture and the target format of `--export-mqttsubscribe`
  (reference only, no code).
- **Aaron Bach**: [ecowitt2mqtt](https://github.com/bachya/ecowitt2mqtt). Topic modes, keys
  and units (reference only, no code).
- The users of the existing drivers whose feedback shaped the sensor catalog, the Ecowitt
  schema and the MQTT source.

## Sources

| ID | Source | Author(s) | Licence | Version used | Used for |
|----|--------|-----------|---------|--------------|----------|
| S1 | WeeWX – https://github.com/weewx/weewx | Tom Keffer, Matthew Wall et al. | GPL-3.0 | 5.5.x, commit `5eb3ac9` | Driver API, `StdArchive` catchup, `wview_extended`, `DaySummaryManager` |
| S2 | weewx-EcowittGateway – https://github.com/Millardiang/weewx-ecowittGateway | Ian Millard | GPL-3.0-or-later | commit `5cbe533`, file version 1.2.0 | Code adapted (see provenance table) |
| S3 | GW1000 / `ecowitt_http.py` driver | Gary Roderick | GPL-3.0-or-later | via S2 | Indirectly via S2 |
| S4 | Ecowitt local HTTP API (`/get_livedata_info`, `/get_sdmmc_info`, CSV on port 81) | Ecowitt / community | facts, no official docs | via S2/S3 | Endpoints, formats |
| S5 | Ecowitt.net API v3 – https://doc.ecowitt.net | Ecowitt | API docs | v3 | `device/history` |
| S6 | WeeWX-MQTTSubscribe | Rich Bell | GPL-3.0 | reference only | Export format |
| S7 | User feedback (2026-11) | anonymous | – | ideas | Catalog, schema, MQTT, versioning |
| S8 | Description of the native Ecowitt MQTT format and the ecowitt2mqtt format (project owner, 2026-11) | – | – | **unverified** | MQTT key tables |
| S9 | ecowitt2mqtt | Aaron Bach | MIT | reference only | Topic modes, keys, calculated values |

## Provenance of the code

All references point to `bin/user/weewx-EcowittGateway.py` in S2 at commit `5cbe533`. Each
block also carries a `# Source:` comment in the code.

| Module | Upstream (S2) | Changes |
|--------|---------------|---------|
| `util.py` | Exception classes, `InvertibleMap`, `DebugOptions`, `OnceLogger`, `timestamp_to_string`, `_rng/_chmap`, `_try`, natural sort, `obfuscate/mask_secrets`, `flatten` | `OnceLogger` prefix; `passkey` masked |
| `units.py` | Module-level unit group registration | Moved into a function (no import side effects) |
| `sources/parser.py` | L118-124 device lists; L2283-2869 helpers and `EcowittHttpParser` | Unit registration in the constructor |
| `sources/http_api.py` | L2207-2277 `EcowittHttpApi`; L2944-3096 `EcowittDevice` | `host:port`; `EcowittSensors` removed |
| `sources/http_source.py` | L2154-2175 `EcowittHttpCollector.get_current_data` | One source per gateway instead of a collector thread; sensor info cached |
| `mapping.py` | L287-391 sensor tables/wind; L490-710 `FieldMapper`, `HttpMapper`, `SdMapper`; L716-756 `DeltaTracker`, `calc_twb`; L795-934 `SensorMapper`; derivations from `process_live_data` | `CatalogMapper` and `derive_fields` new |
| `cumulative.py` | L716-756 `DeltaTracker.calc` semantics | New: arbitration across several sources |
| `history/cloud.py` | L1727-1879 `EcowittNetCatchup` | Timeout, injectable `urlopen` |
| `history/sd.py` | L1881-2069 `EcowittDeviceCatchup` | `sd_port`, `host:port`, injectable; non-`YYYYMM` files skipped |
| `configurator.py` | L1468-1640 ConfEditor structure | Options new |
| `catalog.py`, `merge.py`, `gap.py`, `schema.py`, `history/aggregate.py`, `sources/base.py`, `sources/mqtt_source.py`, `__init__.py`, `tools/build_catalog.py` | – | New (mKainzbauer). `build_catalog.py` reads the S2 maps to build the catalog |
| `ecowitt_catalog.json` | Generated from the S2 field maps (S2 data) and the S8 MQTT tables | Generated; S2 data is GPL-3.0-or-later |

## Licence

The combined work is licensed under **GPL-3.0-or-later**, the same licence as S2/S3. Adapted
files keep the upstream copyright notices (`Portions Copyright …`) and carry a dated
modification notice (GPLv3 §5a). See [LICENSE](LICENSE) and [CHANGELOG.md](CHANGELOG.md).
