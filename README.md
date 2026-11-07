# weewx-ecowitt-multi – Ecowitt Multi-Gateway driver for WeeWX

<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<!-- Copyright (C) 2026 mKainzbauer -->

A WeeWX 5 driver that reads live data from **several Ecowitt gateways/consoles** at the same
time. It reads over the local HTTP API (`/get_livedata_info`) and, optionally, from MQTT.
For every field it merges the competing values by a configurable priority. Missing archive
records are filled through the WeeWX catchup mechanism (`genStartupRecords`), from the
gateway SD card or from Ecowitt.net.

Status: **0.1.0, alpha**. Tested with synthetic fixtures only (see "Limitations").

This driver is an adaptation of Ian Millard's
[weewx-EcowittGateway](https://github.com/Millardiang/weewx-ecowittGateway), which is itself
based on Gary Roderick's GW1000/`ecowitt_http` driver. See [CREDITS.md](CREDITS.md).

## Features

| # | Feature | Where |
|---|---------|-------|
| 1 | Any number of sources (`http`, `mqtt`), each with its own options | `sources/` |
| 2 | Priority merge per field (`source_priority`, `[[priority]]` with `prefix*`) | `merge.py` |
| 3 | Fallback: a field missing from source 1 is taken from source 2 | `merge.py` |
| 4 | Cumulative counters (rain) taken from **one** source per interval, no double counting | `cumulative.py` |
| 5 | Plausibility limits per unit group; values outside them are discarded | `catalog.py`, `merge.py` |
| 6 | Lower-priority sources are only polled when needed (`poll_mode = auto`) | `__init__.py` |
| 7 | Outage detection; engine restart to trigger the catchup (`gap_mode`) | `gap.py` |
| 8 | Catchup from SD card (GW2000/WS90 …) and Ecowitt.net, merged by priority | `history/` |
| 9 | Versioned sensor catalog (`ecowitt_catalog.json`, semver) | `catalog.py` |
| 10 | Unknown/new sensor keys: pass as `ecowitt_<key>`, log, or ignore | `mapping.py` |
| 11 | Optional Ecowitt-centric DB schema; columns added automatically or on request | `schema.py` |
| 12 | MQTT source: native gateway export and ecowitt2mqtt (single/multi topic) | `sources/mqtt_source.py` |
| 13 | Export of an MQTTSubscribe template (`--export-mqttsubscribe`) | `configurator.py` |

## Installation

```
weectl extension install weewx-ecowitt-multi-0.1.0.zip
weectl station reconfigure --driver=user.ecowitt_multi
```

For MQTT sources, install `paho-mqtt` (v1 or v2) in the Python environment used by WeeWX.

The driver sets `[StdArchive] record_generation = software`. With software record generation,
WeeWX creates no archive records while no loop packets arrive. The outage handling builds on
this.

## Configuration

```ini
[Station]
    station_type = EcowittMulti

[EcowittMulti]
    driver = user.ecowitt_multi
    source_priority = gw1, gw2, mq        # best first; default: order of [[sources]]
    poll_interval = 20                    # seconds
    poll_mode = auto                      # auto | all
    required_fields = auto                # auto = the fields a source supplied before
    rain_source = tipping                 # tipping | piezo  (feeds rain / rainRate)
    unknown_fields = log                  # pass | log | ignore
    cache_max_age = 300                   # s; default archive_interval
    diagnostic_fields = false             # adds per-source status fields to the packet
    gap_mode = restart                    # restart | none
    max_gap_wait = 3600                   # s; give up waiting for recovery
    min_restart_interval = 900            # s; between two engine restarts
    max_catchup_days = 7
    catchup_grace = 0                     # s; no catchup for gaps <= archive_interval + grace
    # catalog_file = /path/to/ecowitt_catalog.json   # newer downloaded catalog
    # auto_add_columns = false            # add missing catalog columns to the DB
    # auto_add_unknown_columns = false    # also add ecowitt_* columns
    # debug = rain, wind, catchup, ...    # S2 debug options

    [[sources]]
        [[[gw1]]]
            type = http
            host = 192.168.1.10           # host or host:port
            # port = 80
            history = auto                # auto | sd | cloud | none
            # sd_port = 81
            # sensors_interval = 600      # s between sensor-info requests
            # max_tries = 3
            # retry_wait = 2
            # url_timeout = 3
            # offline_retry = 60          # s between retries of an offline source
            # api_key = ... / app_key = ... / mac = ...   # Ecowitt.net history
        [[[gw2]]]
            type = http
            host = 192.168.1.11
        [[[mq]]]
            type = mqtt
            broker = localhost
            port = 1883
            topic = ecowitt/#
            format = native               # native | ecowitt2mqtt
            # e2m_units = imperial        # ecowitt2mqtt output units: imperial | metric
            # mqtt_use_calculated = false # use ecowitt2mqtt dew point etc.
            # mqtt_timeout = 180          # s without a message = offline
            # username / password / tls / client_id
            history = none

    [[priority]]                          # per field, overrides source_priority
        outTemp = gw2, gw1
        soil* = gw2

    [[field_map_extensions]]              # additional/overriding mappings
        extraTemp9 = ecowitt.some.key
```

A single gateway can be configured in short form: `host = 192.168.1.10` directly in
`[EcowittMulti]` creates the source `gw1`.

### How the merge works

- Each source keeps a cache entry, which is valid for `cache_max_age` seconds.
- For each field, the first fresh source in the field's priority list that has a
  plausible value wins.
- Cumulative fields (rain totals) are handled differently. Deltas are calculated per source,
  and a delta is only accepted from the source that currently "owns" the field. A
  higher-priority source or a stale owner hands ownership over without overlapping intervals.

### Outages and catchup

```
normal --(no data > archive_interval)--> outage --(data again)--> recovering
recovering: history capable source online -> raise WeeWxIOError -> engine restart
            -> StdArchive catchup -> driver.genStartupRecords(last_good_ts)
```

- History is read from all capable sources: the SD card (one request starting at the gap
  start) and Ecowitt.net (`start_ts` to `stop_ts`). It is aggregated per archive interval and
  merged by the same priorities as live data.
- Records are emitted in METRICWX.
- No catchup happens for an empty database or for gaps shorter than
  `archive_interval + catchup_grace`.

## Sensor catalog and Ecowitt schema

`ecowitt_catalog.json` is the single source of truth. It contains:

- a versioned list of all fields: WeeWX name, live key or counter keys, aggregation, unit
  group, SQL type and `min_schema_version`;
- the SD and cloud maps;
- the MQTT key tables.

Two DB schemas can be used:

- **wview_extended** (default): fields that are not in the schema are not stored unless
  columns are added.
- **Ecowitt schema**: `wview_extended` plus every catalog column (about 560). To use it, set
  `[DataBindings] [[wx_binding]] schema = user.ecowitt_multi.schema.schema`. The schema
  version is stored in the metadata table (`ecowitt_schema_version`).

Commands (`weectl device --help` lists all):

```
weectl device --sources              # status of all sources
weectl device --live-data            # merged live packet with provenance
weectl device --dump-api             # raw API data of the gateways (for new sensors)
weectl device --unknown-fields       # keys not in the catalog + draft catalog entries
weectl device --history --hours=6    # what the catchup would deliver
weectl device --sd-files             # files on the SD card
weectl device --check-schema         # missing DB columns
weectl device --add-columns          # add them (backup first!)
weectl device --export-mqttsubscribe # MQTTSubscribe [[topics]] template
```

## Alternative: MQTT + MQTTSubscribe

If you don't need backfill and want to be independent of Ecowitt API changes, you can let the
gateway publish MQTT and use Rich Bell's
[MQTTSubscribe](https://github.com/bellrichm/WeeWX-MQTTSubscribe) as the driver.
`--export-mqttsubscribe` generates the matching mapping from the catalog. This driver can
also consume the same MQTT topic as one source among others (with HTTP for backfill).

## Limitations

- Tests use synthetic fixtures. The MQTT key tables (native export and ecowitt2mqtt) are
  **not yet verified** against real payloads.
- In ecowitt2mqtt multi-topic mode, values accumulate; keys that are no longer sent stay
  until the source goes offline.
- When a counter is handed over between sources, or after a restart (re-baseline), the rain
  of one interval can be lost.
- The rejection of sensors with signal 0 (S2 option) is not implemented.
- `auto_add_columns` adds columns when the first record containing the field arrives
  (including catchup records). Before that, `--add-columns` can be used.

## Development

```
uv venv; uv pip install pytest configobj paho-mqtt
$env:PYTHONPATH = "<weewx>\src;bin"; .venv\Scripts\python -m pytest -q
python tools/build_catalog.py --check
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for adding new sensors. Plans and the gap analysis are
in [docs/plans](docs/plans) (German and English).

## Credits and licence

Copyright (C) 2026 mKainzbauer. Licensed under the **GNU General Public License v3.0 or later**
(see [LICENSE](LICENSE)).

Parts of the code are adapted from **weewx-EcowittGateway** by Ian Millard (GPL-3.0-or-later),
which is based on the work of **Gary Roderick** (GW1000 / `ecowitt_http.py`). The driver API
and catchup behaviour come from **WeeWX** (Tom Keffer, Matthew Wall et al.). Every adapted
code block carries a `# Source:` reference, and every modified file carries a modification
notice. Details and the full source list are in [CREDITS.md](CREDITS.md); changes are listed
in [CHANGELOG.md](CHANGELOG.md).
