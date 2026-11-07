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
# Modified by mKainzbauer, 2026-11: extracted from the single-file upstream driver; the host may
# include a port (host:port); EcowittSensors and the TCP API are not used.
"""Client of the Ecowitt local HTTP API (GW1100/GW1200/GW2000/GW3000, WN19xx/WS39xx consoles, ...)."""

import json
import logging
import socket
import time
import urllib.parse
import urllib.request

import weewx

from ..util import DeviceIOError, UnknownApiCommand
from .parser import (DEFAULT_FILTER_BATTERY, DEFAULT_GET_SOILAD, DEFAULT_ONLY_REGISTERED_SENSORS,
                     DEFAULT_UNIT_SYSTEM, EcowittHttpParser)

log = logging.getLogger(__name__)

DEFAULT_MAX_TRIES = 3
DEFAULT_RETRY_WAIT = 2
DEFAULT_URL_TIMEOUT = 10


# Source: S2 weewx-EcowittGateway @5cbe533, bin/user/weewx-EcowittGateway.py, lines 2207-2277: class EcowittHttpApi. Unchanged (ip_address may be "host:port").
class EcowittHttpApi:
    """Thin client for the Ecowitt local HTTP API."""

    commands = ('get_version', 'get_livedata_info', 'get_ws_settings', 'get_calibration_data', 'get_rain_totals',
                'get_device_info', 'get_sensors_info', 'get_network_info', 'get_units_info', 'get_cli_soilad',
                'get_cli_multiCh', 'get_cli_pm25', 'get_cli_co2', 'get_piezo_rain', 'get_cli_wh34', 'get_cli_lds',
                'get_sdmmc_info')
    sensor_rename_map = {'wh30': 'wn30', 'wh31': 'wn31', 'wh32': 'wn32', 'wh34': 'wn34', 'wh35': 'wn35',
                         'wh36': 'wn36', 'wh80': 'ws80', 'wh85': 'ws85', 'wh90': 'ws90'}

    def __init__(self, ip_address, max_tries=DEFAULT_MAX_TRIES, retry_wait=DEFAULT_RETRY_WAIT,
                 timeout=DEFAULT_URL_TIMEOUT):
        self.ip_address = ip_address
        self.max_tries = max_tries or DEFAULT_MAX_TRIES
        self.retry_wait = DEFAULT_RETRY_WAIT if retry_wait is None else retry_wait
        self.timeout = timeout or DEFAULT_URL_TIMEOUT

    def request(self, command_str, data=None, headers=None, rename=True):
        """Send an API command and return the deserialised JSON (None if undecodable)."""
        if command_str not in self.commands:
            raise UnknownApiCommand(f"Unknown HTTP API command '{command_str}'")
        url = f'http://{self.ip_address}/{command_str}?{urllib.parse.urlencode(data or {})}'
        req = urllib.request.Request(url=url, headers=headers or {})
        for attempt in range(1, self.max_tries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as w:
                    resp = w.read().decode(w.headers.get_content_charset() or 'utf-8')
                break
            except socket.timeout:
                if weewx.debug >= 2:
                    log.debug('Socket - Failed to get device data on attempt %d of %d', attempt, self.max_tries)
                if attempt == self.max_tries:
                    raise
                time.sleep(self.retry_wait)
            except OSError as e:
                log.error('URL - Failed to get device data on attempt %d of %d', attempt, self.max_tries)
                log.error('   **** %s', e)
                raise
        for old, new in self.sensor_rename_map.items() if rename else ():
            resp = resp.replace(old, new)
        try:
            resp_json = json.loads(resp)
        except json.JSONDecodeError as e:
            log.error('Cannot deserialize device response')
            log.error('   **** %s', e)
            return None
        if weewx.debug >= 3:
            log.debug('Deserialized HTTP response: %s', json.dumps(resp_json))
        return resp_json

    def call(self, command, **data):
        """request() with network errors raised as DeviceIOError."""
        try:
            return self.request(command, data=data or None)
        except OSError as e:
            raise DeviceIOError(f"Failed to obtain '{command}' data: {e}") from e

    def get_sensors_info(self):
        """Return all pages of sensor info concatenated."""
        pages = [self.call('get_sensors_info', page=n) for n in (1, 2, 3)]
        while len(pages) < 5 and pages[-1] is not None:
            pages.append(self.call('get_sensors_info', page=len(pages) + 1))
        if pages[0] is None:
            return pages[1]
        result = []
        for page in pages:
            if page is None:
                break
            result += page
        return result


# Source: S2 weewx-EcowittGateway @5cbe533, bin/user/weewx-EcowittGateway.py, lines 2944-3096: class EcowittDevice. Adapted: EcowittSensors removed.
class EcowittDevice:
    """An Ecowitt device accessed via the local HTTP API."""

    tcp = False
    detected = True
    unit_code_to_string = {
        'temperature': ('group_temperature', ('degree_C', 'degree_F')),
        'pressure': ('group_pressure', ('hPa', 'inHg', 'mmHg')),
        'wind': ('group_speed', ('meter_per_second', 'km_per_hour', 'mile_per_hour', 'knot')),
        'rain': ('group_rain', ('mm', 'inch')),
        'light': ('group_illuminance', ('klux', 'watt_per_meter_squared', 'kfc')),
        'deltat': ('group_deltat', ('degree_C2', 'degree_F2')),
        'rain_rate': ('group_rainrate', ('mm_per_hour', 'inch_per_hour')),
        'depth': ('group_depth', ('mm2', 'foot2')),
        'altitude': ('group_altitude', ('meter', 'foot'))}
    sensors_with_firmware = ('ws80', 'ws85', 'ws90')

    def __init__(self, ip_address, unit_system=DEFAULT_UNIT_SYSTEM, max_tries=DEFAULT_MAX_TRIES,
                 retry_wait=DEFAULT_RETRY_WAIT, url_timeout=DEFAULT_URL_TIMEOUT, show_battery=DEFAULT_FILTER_BATTERY,
                 log_unknown_fields=False, get_soilad=DEFAULT_GET_SOILAD, debug=None):
        if ip_address is None:
            raise weewx.ViolatedPrecondition('device IP address cannot be None')
        self.api = EcowittHttpApi(ip_address=ip_address, max_tries=max_tries, retry_wait=retry_wait,
                                  timeout=url_timeout)
        self.parser = EcowittHttpParser(unit_system=unit_system, show_battery=show_battery, get_soilad=get_soilad,
                                        log_unknown_fields=log_unknown_fields, debug=debug)
        self.log_failures = True
        self._model = None

    def _get(self, command, parse, *args, **kwargs):
        return parse(self.api.call(command), *args, **kwargs)

    def get_live_data(self, flatten_data=True):
        return self._get('get_livedata_info', self.parser.parse_get_livedata_info, flatten_data=flatten_data)

    def get_sensors_data(self, connected_only=DEFAULT_ONLY_REGISTERED_SENSORS, flatten_data=True):
        return self.parser.parse_get_sensors_info(self.api.get_sensors_info(), connected_only=connected_only,
                                                  flatten_data=flatten_data)

    def get_rain_totals(self):
        return self._get('get_rain_totals', self.parser.parse_get_rain_totals, self.get_device_units())

    def get_rain_totalspart(self):
        return self._get('get_rain_totals', self.parser.parse_get_rain_totalspart)

    def get_piezo_rain_data(self):
        return self._get('get_piezo_rain', self.parser.parse_get_piezo_rain, self.get_device_units())

    def get_piezo_rain_datapart(self):
        return self._get('get_piezo_rain', self.parser.parse_get_piezo_rainpart)

    def get_wn34_offset_data(self):
        return self._get('get_cli_wh34', self.parser.parse_get_cli_wh34, self.get_device_units())

    def get_pm25_offset_data(self):
        return self._get('get_cli_pm25', self.parser.parse_get_cli_pm25, self.get_device_units())

    def get_co2_offset_data(self):
        return self._get('get_cli_co2', self.parser.parse_get_cli_co2, self.get_device_units())

    def get_lds_offset_data(self):
        return self._get('get_cli_lds', self.parser.parse_get_cli_lds)

    def get_calibration_data(self):
        return self._get('get_calibration_data', self.parser.parse_get_calibration_data, self.get_device_units())

    def get_multich_calibration_data(self):
        return self._get('get_cli_multiCh', self.parser.parse_get_cli_multich, self.get_device_units())

    def get_soil_calibration_data(self):
        return self._get('get_cli_soilad', self.parser.parse_get_cli_soilad)

    def get_soil_adnow_data(self):
        return self._get('get_cli_soilad', self.parser.parse_get_cli_soiladnow)

    def get_device_info_data(self):
        return self._get('get_device_info', self.parser.parse_get_device_info)

    def get_device_info_datapart(self):
        return self._get('get_device_info', self.parser.parse_get_device_infopart)

    def get_stationtype(self):
        return self._get('get_version', self.parser.parse_get_stationtype)

    def get_ws_settings(self):
        return self._get('get_ws_settings', self.parser.parse_get_ws_settings)

    def get_sdmmc_info_data(self):
        return self._get('get_sdmmc_info', self.parser.parse_get_sdmmc_info)

    def get_device_units(self):
        parsed = self._get('get_units_info', self.parser.parse_get_units_info)
        units = {'group_percent': 'percent', 'group_direction': 'degree_compass', 'group_uv': 'uv_index',
                 'group_fraction': 'ppm', 'group_concentration': 'microgram_per_meter_cubed'}
        for code_group, code in parsed.items():
            group, names = self.unit_code_to_string.get(code_group, (None, ()))
            if isinstance(code, int) and 0 <= code < len(names):
                units[group] = names[code]
        if 'group_rain' in units:
            for key in ('rain_rate', 'depth', 'altitude'):
                group, names = self.unit_code_to_string[key]
                units[group] = names[parsed['rain']]
        if 'group_temperature' in units:
            units['group_deltat'] = self.unit_code_to_string['deltat'][1][parsed['temperature']]
        return units

    @property
    def ip_address(self):
        return self.api.ip_address

    def _version(self):
        return self.parser.parse_get_version(self.api.call('get_version'))

    @property
    def model(self):
        if self._model is None:
            self._model = self._version().get('version')[8:].strip(' \n\r')
        return self._model

    @property
    def mac_address(self):
        return self.parser.parse_get_network_info(self.api.call('get_network_info')).get('mac')

    @property
    def firmware_version(self):
        return self._version().get('firmware_version')

    @property
    def firmware_update_avail(self):
        version = self.api.call('get_version')
        return version['newVersion'] == '1' if version is not None and 'newVersion' in version else None

    @property
    def firmware_update_message(self):
        info = self.api.call('get_device_info')
        return info.get('curr_msg') if info is not None else None

    @property
    def sensor_firmware_versions(self):
        data = self.get_sensors_data()
        return {s.upper(): data.get(f'{s}.version', 'not available') for s in self.sensors_with_firmware
                if data.get(f'{s}.battery') not in (None, 9)}

    @property
    def paired_rain_gauges(self):
        info = self.get_sensors_data(connected_only=True, flatten_data=False)
        result = set()
        if 'wh69' in info or 'wh40' in info:
            result.add('tipping')
        if 'ws85' in info or 'ws90' in info:
            result.add('piezo')
        return tuple(result)
