# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 mKainzbauer
# Portions Copyright (C) 2026 Ian Millard (weewx-EcowittGateway)
# Portions Copyright (C) 2024-25 Gary Roderick (ecowitt_http.py / GW1000 driver)
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version. See the LICENSE file or https://www.gnu.org/licenses/.
"""Generate the initial sensor catalog bin/user/ecowitt_multi/ecowitt_catalog.json.

The catalog is derived from the field maps of weewx-EcowittGateway (S2, adapted in
mapping.py, history/sd.py and history/cloud.py) plus the key tables of the Ecowitt
custom upload protocol (S8) and ecowitt2mqtt (S9) below. After the first release the
JSON file is maintained directly (see CONTRIBUTING.md); this script documents its
origin and can regenerate it for comparison:

    python tools/build_catalog.py [--check]
"""

import json
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / 'bin'))
sys.path.insert(0, os.environ.get('WEEWX_SRC', str(ROOT.parent / 'weewx' / 'src')))

from user.ecowitt_multi import mapping  # noqa: E402
from user.ecowitt_multi.history.cloud import EcowittNetCatchup  # noqa: E402
from user.ecowitt_multi.util import rng  # noqa: E402

CATALOG_VERSION = '1.0.0'
SCHEMA_VERSION = '1.0.0'
OUT = ROOT / 'bin' / 'user' / 'ecowitt_multi' / 'ecowitt_catalog.json'

# Fields that are not in the S2 maps (new sensors). Add entries here, see CONTRIBUTING.md, e.g.
# {'weewx': 'newSensor1', 'key': 'ch_new.1.val', 'group': 'group_percent', 'agg': 'avg',
#  'sql': 'REAL', 'min_schema_version': '1.1.0', 'origin': 'contributor name / issue'}
EXTRA_FIELDS = []

# Source: S2 weewx-EcowittGateway @5cbe533, bin/user/weewx-EcowittGateway.py, _EXTRACTORS.
# Adapted: used as aggregation hints of the catalog instead of [Accumulator] settings only.
_EXTRACTORS = {
    'sum': ('lightning_strike_count', 'lightning_noise_count', 't_rain', 'p_rain', 'hail', 'rain'),
    'max': ('rainRate', 'rrain_piezo', 'p_rainrate', 't_rainRate', 'hailRate', 'windGust'),
    'last': ('model', 'stationtype', 'apName', *rng('gain{}', 5, 0), 'lightning_distance',
             'lightning_last_det_time', 'lightningcount', 'maxdailygust', 'daymaxwind', 'windspdmph_avg10m',
             'winddir_avg10m', 'stormRain', 'hourRain', 'dayRain', 'weekRain', 'monthRain', 'yearRain', 'totalRain',
             'erain_piezo', 'hrain_piezo', 'drain_piezo', 'wrain_piezo', 'mrain_piezo', 'yrain_piezo',
             'totalRain_piezo', 'p_eventrain', 'p_hourrain', 'p_dayrain', 'p_weekrain', 'p_monthrain', 'p_yearrain',
             'dayHail', 'vpd', *rng('depth_ch{}', 4), *rng('pm2_5{}_24hav', 4), '24havpm255',
             *rng('pm2_5{}_24h_avg', 5), 'pm10_24h_avg', 'co2_24h_avg', 'wh25_batt', 'wh26_batt',
             *rng('wh31_ch{}_batt', 8), *rng('wn35_ch{}_batt', 8), 'wh40_batt', 'wn20_batt', 'wn38_batt',
             *rng('wh41_ch{}_batt', 4), 'wh45_batt', *rng('wh51_ch{}_batt', 16), *rng('wh55_ch{}_batt', 4),
             'wh57_batt', 'wh65_batt', 'wh68_batt', 'wh69_batt', 'ws68_batt', 'ws80_batt', 'ws85_batt',
             'ws90_batt', 'ws85cap_volt', 'ws90cap_volt', 'ws1900batt', 'console_batt', 'consoleext_batt',
             *rng('ldsbatt{}', 4), 'eventRain', 'rain24', 't_rainyear', 'p_rainyear', 'lightning_num',
             'lightning_dist', 'lightning_disturber_count', 'runtime', 'heap'),
}
_LAST_GROUPS = ('group_count', 'group_volt', 'group_boolean', 'group_time', 'group_deltatime', 'group_data',
                'group_dbm', 'group_string')

# Ref: S8 Ecowitt "customized upload" (Ecowitt protocol) key names, as sent by the gateway via HTTP POST
# and native MQTT. Units are the imperial protocol units. UNVERIFIED against real payloads.
_PROTO = {
    'tempinf': ('wh25.intemp', 'degree_F'), 'humidityin': ('wh25.inhumi', 'percent'),
    'baromrelin': ('wh25.rel', 'inHg'), 'baromabsin': ('wh25.abs', 'inHg'),
    'tempf': ('common_list.0x02.val', 'degree_F'), 'humidity': ('common_list.0x07.val', 'percent'),
    'winddir': ('common_list.0x0A.val', 'degree_compass'), 'winddir_avg10m': ('common_list.0x6D.val', 'degree_compass'),
    'windspeedmph': ('common_list.0x0B.val', 'mile_per_hour'), 'windgustmph': ('common_list.0x0C.val', 'mile_per_hour'),
    'maxdailygust': ('common_list.0x19.val', 'mile_per_hour'),
    'solarradiation': ('common_list.0x15.val', 'watt_per_meter_squared'), 'uv': ('common_list.0x17.val', 'uv_index'),
    'vpd': ('common_list.5.val', 'inHg'),
    'rainratein': ('rain.0x0E.val', 'inch_per_hour'), 'eventrainin': ('rain.0x0D.val', 'inch'),
    'hourlyrainin': ('rain.0x7D.val', 'inch'), 'last24hrainin': ('rain.0x7C.val', 'inch'),
    'dailyrainin': ('rain.0x10.val', 'inch'), 'weeklyrainin': ('rain.0x11.val', 'inch'),
    'monthlyrainin': ('rain.0x12.val', 'inch'), 'yearlyrainin': ('rain.0x13.val', 'inch'),
    'totalrainin': ('rain.0x14.val', 'inch'),
    'rrain_piezo': ('piezoRain.0x0E.val', 'inch_per_hour'), 'erain_piezo': ('piezoRain.0x0D.val', 'inch'),
    'hrain_piezo': ('piezoRain.0x7D.val', 'inch'), 'last24hrain_piezo': ('piezoRain.0x7C.val', 'inch'),
    'drain_piezo': ('piezoRain.0x10.val', 'inch'), 'wrain_piezo': ('piezoRain.0x11.val', 'inch'),
    'mrain_piezo': ('piezoRain.0x12.val', 'inch'), 'yrain_piezo': ('piezoRain.0x13.val', 'inch'),
    'lightning': ('lightning.distance', 'km'), 'lightning_num': ('lightning.count', 'count'),
    'lightning_time': ('lightning.timestamp', 'unix_epoch'),
    'tf_co2': ('co2.temp', 'degree_F'), 'humi_co2': ('co2.humidity', 'percent'),
    'pm25_co2': ('co2.PM25', 'microgram_per_meter_cubed'), 'pm25_24h_co2': ('co2.PM25_24H', 'microgram_per_meter_cubed'),
    'pm10_co2': ('co2.PM10', 'microgram_per_meter_cubed'), 'pm10_24h_co2': ('co2.PM10_24H', 'microgram_per_meter_cubed'),
    'pm1_co2': ('co2.PM1', 'microgram_per_meter_cubed'), 'pm4_co2': ('co2.PM4', 'microgram_per_meter_cubed'),
    'co2': ('co2.CO2', 'ppm'), 'co2_24h': ('co2.CO2_24H', 'ppm'),
    'co2in': ('wh25.CO2', 'ppm'), 'co2in_24h': ('wh25.CO2_24H', 'ppm'),
    **{f'temp{i}f': (f'ch_aisle.{i}.temp', 'degree_F') for i in range(1, 9)},
    **{f'humidity{i}': (f'ch_aisle.{i}.humidity', 'percent') for i in range(1, 9)},
    **{f'tf_ch{i}': (f'ch_temp.{i}.temp', 'degree_F') for i in range(1, 9)},
    **{f'leafwetness_ch{i}': (f'ch_leaf.{i}.humidity', 'percent') for i in range(1, 9)},
    **{f'soilmoisture{i}': (f'ch_soil.{i}.humidity', 'percent') for i in range(1, 17)},
    **{f'soilad{i}': (f'ch_soil{i}nowAd', 'count') for i in range(1, 17)},
    **{f'pm25_ch{i}': (f'ch_pm25.{i}.PM25', 'microgram_per_meter_cubed') for i in range(1, 5)},
    **{f'pm25_avg_24h_ch{i}': (f'ch_pm25.{i}.PM25_24H', 'microgram_per_meter_cubed') for i in range(1, 5)},
    **{f'leak_ch{i}': (f'ch_leak.{i}.status', 'count') for i in range(1, 5)},
}

# Ref: S9 ecowitt2mqtt (MIT) - key names after unit suffix removal and metric units when its output
# unit system is metric. UNVERIFIED; derived from the protocol keys above.
_E2M_METRIC = {'degree_F': 'degree_C', 'inHg': 'hPa', 'mile_per_hour': 'km_per_hour', 'inch': 'mm',
               'inch_per_hour': 'mm_per_hour'}


def _e2m_key(proto_key):
    if proto_key.startswith('temp') and proto_key.endswith('f'):
        return proto_key[:-1]
    if (proto_key.startswith('barom') or 'rain' in proto_key) and proto_key.endswith('in'):
        return proto_key[:-2]
    if proto_key.endswith('mph'):
        return proto_key[:-3]
    return proto_key


def _agg(weewx_name, group):
    for agg, names in _EXTRACTORS.items():
        if weewx_name in names:
            return agg
    if weewx_name in ('windDir', 'windDir10'):
        return 'vecdir'
    if group in _LAST_GROUPS or weewx_name.endswith(('_batt', 'Batt', '_sig', '_rssi')) or 'Batt' in weewx_name:
        return 'last'
    return 'avg'


def build():
    groups = dict(mapping.DEFAULT_GROUPS)
    default_map = dict(mapping.HttpMapper.default_map)
    default_map['lightning_num'] = 'lightning.count'
    fields = []
    counters = {'t_rain': ['rain.0x13.val', 'rain.0x12.val'],
                'p_rain': ['piezoRain.0x13.val', 'piezoRain.0x12.val'],
                'lightning_strike_count': ['lightning.count']}
    aliases = {'rain': 't_rain', 'hail': 'p_rain', 'lightningcount': 'lightning_strike_count'}
    for weewx_name in sorted(default_map, key=str.lower):
        key = default_map[weewx_name]
        entry = {'weewx': weewx_name}
        if weewx_name in counters:
            entry['counter'] = counters[weewx_name]
            group = 'group_rain' if 'rain' in weewx_name else 'group_count'
        elif weewx_name in aliases:
            entry['alias_of'] = aliases[weewx_name]
            group = 'group_rain' if weewx_name in ('rain', 'hail') else 'group_count'
        else:
            entry['key'] = key
            group = groups.get(key) or 'group_count'
        entry['group'] = group
        entry['agg'] = _agg(weewx_name, group)
        if group != 'group_string':
            entry['sql'] = 'REAL'
        entry['min_schema_version'] = '1.0.0'
        entry['origin'] = 'S2'
        fields.append(entry)
    known = {f['weewx'] for f in fields}
    for extra in EXTRA_FIELDS:
        if extra['weewx'] in known:
            raise ValueError(f"EXTRA_FIELDS: {extra['weewx']} already exists")
        fields.append(dict(extra))
    proto = {k: [key, unit, groups.get(key, 'group_count')] for k, (key, unit) in sorted(_PROTO.items())}
    e2m = {}
    for k, (key, unit, group) in proto.items():
        e2k = _e2m_key(k)
        if e2k in e2m:
            raise ValueError(f'ecowitt2mqtt key collision: {e2k}')
        e2m[e2k] = [key, unit, _E2M_METRIC.get(unit, unit), group]
    return {
        'catalog_version': CATALOG_VERSION,
        'schema_version': SCHEMA_VERSION,
        'comment': 'Ecowitt sensor catalog of weewx-ecowitt-multi. Generated by tools/build_catalog.py from '
                   'weewx-EcowittGateway (S2) maps; protocol_map (S8) and ecowitt2mqtt_map (S9) are unverified.',
        'groups': dict(sorted(groups.items())),
        'fields': fields,
        'sd_map': dict(sorted(mapping.SdMapper.default_map.items())),
        'cloud_map': EcowittNetCatchup.net_to_driver_map,
        'protocol_map': proto,
        'ecowitt2mqtt_map': dict(sorted(e2m.items())),
    }


def main():
    text = json.dumps(build(), indent=1, ensure_ascii=False) + '\n'
    if '--check' in sys.argv:
        same = OUT.exists() and OUT.read_text(encoding='utf-8') == text
        print('catalog is up to date' if same else 'catalog differs from the generated one')
        return 0 if same else 1
    OUT.write_text(text, encoding='utf-8')
    print(f'wrote {OUT}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
