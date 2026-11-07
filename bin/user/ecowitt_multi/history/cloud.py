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
# Modified by mKainzbauer, 2026-11: extracted from the single-file upstream driver; request timeout
# and an injectable opener added; key names follow the plan (application_key).
"""History records from Ecowitt.net (API v3), used when a gateway has no SD card."""

import collections
import datetime
import json
import logging
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

import weeutil.weeutil
import weewx

from ..util import ApiResponseError, InvalidApiResponseError, chmap as _chmap, obfuscate, try_conv as _try

log = logging.getLogger(__name__)


# Source: S2 weewx-EcowittGateway @5cbe533, bin/user/weewx-EcowittGateway.py, lines 1727-1879: class EcowittNetCatchup. Adapted: timeout, urlopen injectable.
class EcowittNetCatchup:
    """Obtains history records from Ecowitt.net (API v3)."""

    endpoint = 'https://api.ecowitt.net/api/v3/device'
    commands = ('real_time', 'history', 'list', 'info')
    api_result_codes = {
        -1: 'System is busy', 0: 'success result', 40000: 'Illegal parameter',
        40010: 'Illegal Application_Key Parameter', 40011: 'Illegal Api_Key Parameter',
        40012: 'Illegal MAC/IMEI Parameter', 40013: 'Illegal start_date Parameter',
        40014: 'Illegal end_date Parameter', 40015: 'Illegal cycle_type Parameter',
        40016: 'Illegal call_back Parameter', 40017: 'Missing Application_Key Parameter',
        40018: 'Missing Api_Key Parameter', 40019: 'Missing MAC Parameter', 40020: 'Missing start_date Parameter',
        40021: 'Missing end_date Parameter', 40022: 'Illegal Voucher type', 43001: 'Needs other service support',
        44001: 'Media file or data packet is null', 45001: 'Over the limit or other error',
        46001: 'No existing request', 47001: 'Parse JSON/XML contents error', 48001: 'Privilege Problem'}
    _rain_obs = (('rain_rate', '0x0E'), ('event', '0x0D'), ('hourly', '0x7D'), ('daily', '0x10'),
                 ('24_hours', '0x7C'), ('weekly', '0x11'), ('monthly', '0x12'), ('yearly', '0x13'))
    net_to_driver_map = {
        'outdoor': {'temperature': 'common_list.0x02.val', 'humidity': 'common_list.0x07.val'},
        'indoor': {'temperature': 'wh25.intemp', 'humidity': 'wh25.inhumi'},
        'solar_and_uvi': {'solar': 'common_list.0x15.val', 'uvi': 'common_list.0x17.val'},
        'rainfall': {k: f'rain.{v}.val' for k, v in _rain_obs},
        'rainfall_piezo': {k: f'piezoRain.{v}.val' for k, v in _rain_obs},
        'wind': {'wind_speed': 'common_list.0x0B.val', 'wind_gust': 'common_list.0x0C.val',
                 'wind_direction': 'common_list.0x0A.val',
                 '10_minute_average_wind_direction': 'common_list.0x6D.val'},
        'pressure': {'absolute': 'wh25.abs', 'relative': 'wh25.rel'},
        'lightning': {'distance': 'lightning.distance', 'count': 'lightning.count'},
        'indoor_co2': {'co2': 'wh25.CO2', '24_hours_average': 'wh25.CO2_24H'},
        'co2_aqi_combo': {'co2': 'co2.CO2', '24_hours_average': 'co2.CO2_24H'},
        **{f'pm{p}_aqi_combo': {f'pm{p}': f'co2.PM{p}', 'real_time_aqi': f'co2.PM{p}_RealAQI',
                                '24_hours_aqi': f'co2.PM{p}_24HAQI'} for p in ('25', '10', '1', '4')},
        **{f'pm25_ch{i}': {'pm25': f'ch_pm25.{i}.PM25'} for i in range(1, 5)},
        't_rh_aqi_combo': {'temperature': 'co2.temp', 'humidity': 'co2.humidity'},
        **{f'temp_and_humidity_ch{i}': {'temperature': f'ch_aisle.{i}.temp', 'humidity': f'ch_aisle.{i}.humidity'}
           for i in range(1, 9)},
        **{f'soil_ch{i}': {'soilmoisture': f'ch_soil.{i}.humidity', 'ad': f'ch_soil{i}nowAd'} for i in range(1, 17)},
        **{f'temp_ch{i}': {'temperature': f'ch_temp.{i}.temp'} for i in range(1, 9)},
        **{f'leaf_ch{i}': {'leaf_wetness': f'ch_leaf.{i}.humidity'} for i in range(1, 9)},
        'battery': {
            'ws1900_console': 'wh25.ws1900_batt', 'ws1800_console': 'wh25.ws1800_batt',
            'ws6006_console': 'console.console_ext_volt', 'console': 'console.console_ext_volt',
            'wind_sensor': 'common_list.0x0A.voltage', 'haptic_array_battery': 'piezoRain.0x13.voltage',
            'haptic_array_capacitor': 'piezocap_volt', 'sonic_array': 'ws80.battery',
            'rainfall_sensor': 'wh40.voltage',
            **_chmap((('soilmoisture_sensor_ch{}', 'ch_soil.{}.voltage'),), 16),
            **_chmap((('temperature_sensor_ch{}', 'ch_temp.{}.voltage'),
                      ('leaf_wetness_sensor_ch{}', 'ch_leaf.{}.voltage')), 8),
            **_chmap((('ldsbatt_{}', 'ch_lds.{}.voltage'),), 4),
            'bgt_sensor': 'common_list.0xA1.voltage',
            **_chmap((('soilmoisture_ec_sensor_ch{}', 'ch_soil.{}.voltage'),), 16)},
        **{f'ch_lds{i}': {f'air_ch{i}': f'ch_lds.{i}.air', f'depth_ch{i}': f'ch_lds.{i}.depth',
                          f'lds_heat_ch{i}': f'ch_lds.{i}.total_heat'} for i in range(1, 5)},
        'black_globe_temperature': {'bgt': 'common_list.0xA1.val', 'wbgt': 'common_list.0xA2.val'},
        **{f'ch_soil_ec_temp_hum{i}': {'soilmoisture': f'ch_soil.{i}.humidity', 'ad': f'ch_soil{i}nowAd',
                                       'temperature': f'ch_ec.{i}.temp', 'ec': f'ch_ec.{i}.ec',
                                       'ec_ad': f'ch_ec{i}nowAd'} for i in range(1, 17)},
    }
    default_call_back = tuple(net_to_driver_map)

    def __init__(self, **options):
        self.api_key, self.app_key, self.mac = options.get('api_key'), options.get('app_key'), options.get('mac')
        self.timeout = options.get('timeout', 30)
        self.urlopen = options.get('urlopen') or urllib.request.urlopen
        for value, name in ((self.api_key, 'API key'), (self.app_key, 'Application key'),
                            (self.mac, 'Device MAC address')):
            if value is None:
                log.info('%s not specified', name)
        if self.mac is not None:
            log.info('EcowittNetCatchup using MAC: %s', self.mac)
        if None in (self.api_key, self.app_key, self.mac):
            log.info('Missing Data for Ecowitt.net - so do not try to get data')

    name = 'Ecowitt.net Catchup'
    source = 'Ecowitt.net'

    def do_debug_logging(self):
        log.info('EcowittNetCatchup: API key: %s Application key: %s', obfuscate(self.api_key), obfuscate(self.app_key))

    def gen_history_records(self, start_ts=None, stop_ts=None, **kwargs):
        if None in (self.api_key, self.app_key, self.mac):
            return
        start_90_dt = datetime.datetime.combine(datetime.date.today() - datetime.timedelta(days=90),
                                                datetime.time())
        start_ts = max(start_ts or 0, time.mktime(start_90_dt.timetuple()))
        stop_ts = int(time.time()) if stop_ts is None else stop_ts
        call_back = ','.join(kwargs.get('call_back', self.default_call_back))
        fmt = '%Y-%m-%d %H:%M:%S'
        for span in weeutil.weeutil.genDaySpans(start_ts, stop_ts):
            data = {'application_key': self.app_key, 'api_key': self.api_key, 'mac': self.mac,
                    'start_date': datetime.datetime.fromtimestamp(span.start + 1).strftime(fmt),
                    'end_date': datetime.datetime.fromtimestamp(span.stop).strftime(fmt),
                    'call_back': call_back, 'cycle_type': '5min', 'temp_unitid': 1, 'pressure_unitid': 3,
                    'wind_speed_unitid': 6, 'rainfall_unitid': 12, 'solar_irradiance_unitid': 16}
            day_data = self.parse_history(self.request('history', data=data).get('data', {}))
            for ts in sorted(day_data):
                if start_ts <= ts <= stop_ts:
                    yield {'datetime': ts, 'interval': 5, **day_data[ts]}

    def request(self, command_str, data=None, headers=None, max_tries=3):
        if command_str not in self.commands:
            return None
        url = f"{self.endpoint}/{command_str}?{urllib.parse.urlencode(data or {})}"
        if weewx.debug >= 2:
            log.info('url: %s', url)
        req = urllib.request.Request(url=url, headers=headers or {})
        for attempt in range(1, max_tries + 1):
            try:
                with self.urlopen(req, timeout=self.timeout) as w:
                    response = w.read().decode(w.headers.get_content_charset() or 'utf-8')
                return self.check_response(response)
            except (socket.timeout, urllib.error.URLError, InvalidApiResponseError, ApiResponseError) as e:
                log.error('Failed to obtain valid data from Ecowitt.net on attempt %d', attempt)
                log.error('   **** %s', e)
                if attempt == max_tries:
                    if isinstance(e, ApiResponseError):
                        raise InvalidApiResponseError(e) from e
                    raise

    def check_response(self, response):
        if not response:
            raise InvalidApiResponseError('Invalid API response received')
        try:
            json_resp = json.loads(response)
        except json.JSONDecodeError as e:
            raise InvalidApiResponseError(e)
        code = json_resp.get('code', 'no code')
        if code != 0:
            raise ApiResponseError(f"Received API response error code '{code}': {self.api_result_codes.get(code)}")
        return json_resp

    def parse_history(self, history_data):
        if not history_data:
            log.info('No history data to parse')
            return {}
        result = collections.defaultdict(dict)
        for set_name, set_data in history_data.items():
            for obs, obs_data in set_data.items():
                field = self.net_to_driver_map.get(set_name, {}).get(obs)
                if field is not None:
                    for ts, value in self.parse_float(obs_data).items():
                        result[ts][field] = value
        return dict(result)

    @staticmethod
    def parse_float(data):
        result = {}
        for ts_string, value_str in data['list'].items():
            try:
                ts = int(ts_string)
            except ValueError:
                continue
            result[ts] = _try(float, value_str)
        return result
