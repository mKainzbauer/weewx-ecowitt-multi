# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 mKainzbauer
# Portions Copyright (C) 2026 Ian Millard (weewx-EcowittGateway)
# Portions Copyright (C) 2024-25 Gary Roderick (ecowitt_http.py / GW1000 driver)
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version. See the LICENSE file or https://www.gnu.org/licenses/.
#
# Modified by mKainzbauer, 2026-11: extracted from the single-file upstream driver; SD file port
# configurable (sd_port), host:port accepted, file opener injectable; files whose names do not
# start with YYYYMM (e.g. 'log') are skipped in get_file_list.
"""History records from the SD card of a gateway (GW2000/GW3000, WS39xx, ...)."""

import collections
import csv
import datetime
import logging
import time
import urllib.error
import urllib.request
from operator import itemgetter

import weeutil.weeutil
import weewx
import weewx.units

from ..mapping import SdMapper
from ..sources.http_api import DEFAULT_URL_TIMEOUT, EcowittDevice
from ..util import CatchupObjectError, DebugOptions, DeviceIOError, ParseError, rng as _rng

log = logging.getLogger(__name__)

DEFAULT_UNIT_SYSTEM = weewx.METRICWX
DEFAULT_CATCHUP_GRACE = 0
DEFAULT_CATCHUP_RETRIES = 3
DEFAULT_SD_PORT = 81


# Source: S2 weewx-EcowittGateway @5cbe533, bin/user/weewx-EcowittGateway.py, lines 1881-2069: class EcowittDeviceCatchup. Adapted: sd_port, host:port, injectable device/opener.
class EcowittDeviceCatchup:
    """Obtains history records from the device SD card."""

    unit_groups_by_field = {
        'group_temperature': ('wh25.intemp', 'common_list.0x02.val', 'common_list.0x03.val', 'feelslike',
                              *_rng('ch_aisle.{}.temp', 8), *_rng('dewpoint{}', 8), *_rng('heatindex{}', 8),
                              *_rng('ch_temp.{}.temp', 8), 'co2.temperature', 'co2.temp', *_rng('ch_ec.{}.temp', 16),
                              'common_list.0xA1.val', 'common_list.0xA2.val'),
        'group_speed': ('common_list.0x0B.val', 'common_list.0x0C.val'),
        'group_pressure': ('wh25.abs', 'wh25.rel', 'common_list.5.val'),
        'group_rain': ('rain.0x0D.val', 'rain.0x10.val', 'rain.0x11.val', 'rain.0x12.val', 'rain.0x7D.val',
                       'piezoRain.0x7D.val', 'rain.0x13.val', 'rain.0x14.val', 'rain.0x0E.val', 'piezoRain.0x0D.val',
                       'piezoRain.0x10.val', 'piezoRain.0x11.val', 'piezoRain.0x12.val', 'piezoRain.0x13.val',
                       'piezoRain.0x14.val', 'piezoRain.0x0F.val'),
        'group_rainrate': ('rain.0x0E.val', 'piezoRain.0x0E.val'),
        'group_radiation': ('common_list.0x15.val',),
        'group_distance': ('lightning.distance',),
        'group_depth': tuple(f'ch_lds.{i}.{k}' for k in ('air', 'depth', 'total_height') for i in range(1, 5)),
    }
    fixed_units = {
        'group_altitude': 'meter', 'group_amp': 'amp', 'group_angle': 'degree_angle', 'group_boolean': 'boolean',
        'group_concentration': 'microgram_per_meter_cubed', 'group_count': 'count', 'group_data': 'byte',
        'group_db': 'dB', 'group_degree_day': 'degree_C_day', 'group_deltatime': 'second',
        'group_direction': 'degree_compass', 'group_elapsed': 'second', 'group_energy': 'watt_hour',
        'group_energy2': 'watt_second', 'group_fraction': 'ppm', 'group_frequency': 'hertz',
        'group_interval': 'minute', 'group_length': 'cm', 'group_moisture': 'centibar', 'group_percent': 'percent',
        'group_power': 'watt', 'group_pressurerate': 'mbar_per_hour', 'group_time': 'unix_epoch',
        'group_uv': 'uv_index', 'group_volt': 'volt', 'group_volume': 'liter',
        'group_usiecm': 'micro_siemens_per_centimeter', 'group_ntu': 'nephelometric_turbidity_unit'}
    # (column keyword, unit group checked, ((unit marker, {group: unit}), ...))
    _unit_rules = (
        ('temperature', 'group_temperature', (('c)', {'group_temperature': 'degree_C'}),
                                              ('℃', {'group_temperature': 'degree_C'}),
                                              ('f)', {'group_temperature': 'degree_F'}),
                                              ('℉', {'group_temperature': 'degree_F'}))),
        ('pressure', 'group_pressure', (('(hpa)', {'group_pressure': 'hPa'}), ('(inhg)', {'group_pressure': 'inHg'}),
                                        ('(mmhg)', {'group_pressure': 'mmHg'}))),
        ('gust', 'group_speed', tuple((m, {'group_speed': u, 'group_speed2': u}) for m, u in (
            ('(km/h)', 'km_per_hour'), ('(mph)', 'mile_per_hour'), ('(m/s)', 'meter_per_second'),
            ('(knots)', 'knot'), ('(nhg)', 'knot')))),
        ('rain', 'group_rain', (('(mm)', {'group_rain': 'mm', 'group_rainrate': 'mm_per_hour'}),
                                ('(in)', {'group_rain': 'inch', 'group_rainrate': 'inch_per_hour'}))),
        ('solar rad', 'group_radiation', (('(w/m2)', {'group_radiation': 'watt_per_meter_squared'}),
                                          ('(klux)', {'group_illuminance': 'kilolux'}),
                                          ('(kfc)', {'group_illuminance': 'kfc'}))),
        ('distance', 'group_distance', (('(km)', {'group_distance': 'km'}), ('(mi)', {'group_distance': 'mile'}),
                                        ('(nmi)', {'group_distance': 'nautical_mile'}))),
        ('lds', 'group_depth', (('(mm)', {'group_depth': 'mm2'}), ('cm)', {'group_depth': 'cm2'}),
                                ('in)', {'group_depth': 'inch2'}), ('(ft)', {'group_depth': 'foot2'}))),
    )

    def __init__(self, **options):
        if 'ip_address' not in options:
            raise CatchupObjectError('Device IP address not found.')
        self.ip_address = options['ip_address']
        to_int = weeutil.weeutil.to_int
        self.url_timeout = to_int(options.get('url_timeout', DEFAULT_URL_TIMEOUT))
        self.device = options.get('device') or EcowittDevice(ip_address=self.ip_address,
                                                              url_timeout=self.url_timeout)
        self.sd_host = str(self.ip_address).split(':')[0]
        self.sd_port = to_int(options.get('sd_port', DEFAULT_SD_PORT))
        self.urlopen = options.get('urlopen') or urllib.request.urlopen
        self.unit_system = to_int(options.get('unit_system', DEFAULT_UNIT_SYSTEM))
        self.catchup_grace = to_int(options.get('catchup_grace', DEFAULT_CATCHUP_GRACE))
        self.max_catchup_retries = to_int(options.get('catchup_retries', DEFAULT_CATCHUP_RETRIES))
        self.driver_debug = options.get('driver_debug') or DebugOptions()
        self.sdmmc_info()
        self.mapper = SdMapper(driver_debug=self.driver_debug)

    def sdmmc_info(self):
        try:
            return self.device.get_sdmmc_info_data()
        except (DeviceIOError, ParseError):
            raise CatchupObjectError(f"{self.device.model} at '{self.ip_address}' "
                                     f"does not appear to support HTTP API based catchup")

    def gen_history_records(self, start_ts=None):
        sdmmc_info = self.sdmmc_info()
        files = self.get_file_list(sdmmc_info, start_ts)
        try:
            interval = int(sdmmc_info['info']['Interval'])
        except (KeyError, ValueError, TypeError) as e:
            raise CatchupObjectError(f'Unable to determine history file record interval: {e}')
        model = self.device.model
        for ym in sorted(files):
            recs = collections.defaultdict(dict)
            for file in files[ym]:
                if file == 'log':
                    continue
                if weewx.debug >= 2 or self.driver_debug.archive:
                    log.info("Processing history file '%s' from %s at %s", file, model, self.ip_address)
                try:
                    lines = [line.decode('utf-8') for line in self.get_file(file).readlines()]
                except DeviceIOError:
                    log.error("Unable to download history file '%s' from %s at %s", file, model, self.ip_address)
                    continue
                except UnicodeDecodeError:
                    log.error("Unable to decode file '%s' from %s at %s", file, model, self.ip_address)
                    continue
                try:
                    file_recs = self.process_raw_csv_data(csv.DictReader(self.clean_data(lines)), start_ts, interval)
                except csv.Error:
                    log.error("Unable to parse CSV file '%s' from %s at %s", file, model, self.ip_address)
                    continue
                for rec in file_recs:
                    recs[rec['datetime']].update(rec)
            yield from sorted(recs.values(), key=itemgetter('datetime'))

    def clean_data(self, raw_data):
        clean = []
        for row in raw_data:
            if '\x00' in row:
                row = row.replace('\x00', '')
                if weewx.debug >= 2 or self.driver_debug.catchup:
                    log.info('One or more null bytes found in and removed')
            if row != '\n':
                clean.append(row)
        return clean

    def process_raw_csv_data(self, data_reader, start_ts, interval):
        result, units = [], {}
        for row in data_reader:
            try:
                ts = datetime.datetime.strptime(row['Time'], '%Y-%m-%d %H:%M').timestamp()
            except ValueError:
                continue
            if start_ts is None or ts > start_ts + self.catchup_grace:
                units = units or self.get_units(row.keys())
                rec = self.convert_history_rec(self.mapper.map_data(rec=row), units)
                rec.update(datetime=ts, interval=interval)
                result.append(rec)
        return result

    @classmethod
    def get_units(cls, keys):
        units = dict(cls.fixed_units)
        for key in (k.lower() for k in keys):
            for keyword, group, markers in cls._unit_rules:
                if keyword in key and group not in units:
                    for marker, found in markers:
                        if marker in key:
                            units.update(found)
                            break
                    break
        return units

    def convert_history_rec(self, rec, units):
        converted = dict(rec)
        dbg = weewx.debug >= 3 or self.driver_debug.catchup
        for group, fields in self.unit_groups_by_field.items():
            for field in fields:
                if field not in rec:
                    continue
                try:
                    value = rec[field] * 10 if field == 'common_list.5.val' and units.get(group) == 'hPa' \
                        else rec[field]
                    converted[field] = weewx.units.convertStd(weewx.units.ValueTuple(value, units.get(group), group),
                                                              self.unit_system).value
                except Exception as e:
                    if dbg:
                        log.info("Could not convert field '%s' with unit group '%s': %s", field, group, e)
        return converted

    def get_file(self, file_name):
        url = f'http://{self.sd_host}:{self.sd_port}/{file_name}'
        for attempt in range(1, self.max_catchup_retries + 1):
            try:
                return self.urlopen(url, timeout=self.url_timeout)
            except urllib.error.URLError as e:
                if attempt < self.max_catchup_retries:
                    log.debug("Failed to obtain file '%s' after %d attempts: %s", file_name, attempt, e)
                    time.sleep(1)
                else:
                    msg = f"Failed to obtain file '{file_name}' after {attempt} attempts: {e}"
                    log.error(msg)
                    raise DeviceIOError(msg)
        raise DeviceIOError(f"Failed to obtain file '{file_name}'")

    @staticmethod
    def get_file_list(sd_info, start_ts=None):
        if start_ts is not None:
            tt = datetime.datetime.fromtimestamp(start_ts).timetuple()
            ts_index = tt.tm_year * 100 + tt.tm_mon
        else:
            ts_index = 0
        indexed = {}
        for f in sd_info['file_list']:
            name = str(f.get('name', ''))
            if f['type'] in ('file', '1') and name[0:6].isdigit():
                index = int(name[0:4]) * 100 + int(name[4:6])
                if index >= ts_index:
                    indexed.setdefault(index, []).append(name)
        return indexed
