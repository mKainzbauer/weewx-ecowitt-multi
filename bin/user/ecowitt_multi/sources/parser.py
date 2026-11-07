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
# Modified by mKainzbauer, 2026-11: extracted from the single-file upstream driver; units are
# registered on construction; device list and defaults made local.
"""Parser of Ecowitt local HTTP API responses (flattened, converted to one unit system)."""

import datetime
import logging
import re
import time
from operator import itemgetter

import weeutil.weeutil
import weewx
import weewx.units

from ..units import register_units
from ..util import DebugOptions, ParseError, ProcessorError, UnitError, flatten, obfuscate

log = logging.getLogger(__name__)

DEFAULT_UNIT_SYSTEM = weewx.METRICWX
DEFAULT_FILTER_BATTERY = False
DEFAULT_GET_SOILAD = True
DEFAULT_ONLY_REGISTERED_SENSORS = False


# Source: S2 weewx-EcowittGateway @5cbe533, bin/user/weewx-EcowittGateway.py, lines 118-124: module constants SUPPORTED_DEVICES/KNOWN_DEVICES. Unchanged.

SUPPORTED_DEVICES = ('GW1000', 'WH2650', 'GW1100', 'GW1200', 'GW2000', 'GW3000', 'WN1700', 'WN1820', 'WN1821',
                     'WN1920', 'WN1980', 'WS6210', 'WS3800', 'WS3820', 'WS3900', 'WS3910')
# devices with only the binary TCP API (no local HTTP API)
TCP_ONLY_DEVICES = ('GW1000', 'WH2650')
UNSUPPORTED_DEVICES = ()
KNOWN_DEVICES = SUPPORTED_DEVICES + UNSUPPORTED_DEVICES


# Source: S2 weewx-EcowittGateway @5cbe533, bin/user/weewx-EcowittGateway.py, lines 2283-2869: helpers _try/_copy/_same/_to_vt/_pct and class EcowittHttpParser. Adapted: register_units() in __init__.
_SKIP = object()
_BLANK = weewx.units.ValueTuple(None, None, None)


def _try(fn, value, default=None):
    """Return fn(value) or default if the conversion fails."""
    try:
        return fn(value)
    except (TypeError, ValueError):
        return default


def _copy(dest, src, spec, skip_bad=False):
    """For each (dest_key, src_key, fn) with src_key in src set dest[dest_key] = fn(src[src_key]).

    fn None copies the raw value; a failed conversion stores None (or is skipped if skip_bad).
    """
    for dkey, skey, fn in spec:
        if skey in src:
            value = src[skey] if fn is None else _try(fn, src[skey], _SKIP)
            if value is _SKIP:
                if skip_bad:
                    continue
                value = None
            dest[dkey] = value
    return dest


def _same(fn, *keys):
    return tuple((k, k, fn) for k in keys)


def _to_vt(value, unit, group, fn=weeutil.weeutil.to_float):
    try:
        return weewx.units.ValueTuple(fn(value), unit, group)
    except (TypeError, ValueError):
        return _BLANK


def _pct(value):
    return float(value.split('%')[0])


class EcowittHttpParser:
    """Parses Ecowitt HTTP API responses."""

    default_device_units = {'group_volt': 'volt', 'group_uv': 'uv_index', 'group_percent': 'percent',
                            'group_direction': 'degree_compass', 'group_fraction': 'ppm',
                            'group_concentration': 'microgram_per_meter_cubed', 'group_boolean': 'boolean',
                            'group_usiecm': 'micro_siemens_per_centimeter',
                            'group_organicpollution': 'milligram_per_liter'}
    unit_lookup = {'c': 'degree_C', 'f': 'degree_F', 'km': 'km', 'mi': 'mile', 'nmi': 'nautical_mile', 'hpa': 'hPa',
                   'kfc': 'kfc', 'klux': 'klux', 'kpa': 'kPa', 'inhg': 'inHg', 'mmhg': 'mmHg', 'mm': 'mm',
                   'in': 'inch', 'ft': 'foot', 'mm/hr': 'mm_per_hour', 'in/hr': 'inch_per_hour',
                   'km/h': 'km_per_hour', 'm/s': 'meter_per_second', 'mph': 'mile_per_hour', 'knots': 'knot',
                   '%': 'percent', 'w/m2': 'watt_per_meter_squared', 'us/cm': 'micro_siemens_per_centimeter',
                   'mg/l': 'milligram_per_liter', 'ntu': 'nephelometric_turbidity_unit'}
    _T, _PRESS = ('group_temperature', 'conv'), ('group_pressure', 'conv')
    _HUM, _DIR = ('group_percent', int), ('group_direction', int)
    _SPD, _RAIN = ('group_speed', 'conv'), ('group_rain', 'conv')
    # common_list/rain/piezoRain object id -> (unit group, post-processing) or None (ignored)
    object_kinds = {
        '0x01': _T, '0x02': _T, '0x03': _T, '3': _T, '0x04': _T, '4': _T, '0x05': _T, '5': _PRESS,
        '0x07': _HUM, '0x08': None, '0x09': None, '0x0A': _DIR, '0x0B': _SPD, '0x0C': _SPD,
        '0x0D': _RAIN, '0x0E': ('group_rainrate', 'conv'), '0x0F': _RAIN, '0x10': _RAIN, '0x11': _RAIN,
        '0x12': _RAIN, '0x13': _RAIN, '0x14': _RAIN, '0x15': ('group_radiation', 'conv'), '0x16': _HUM,
        '0x17': _DIR, '0x18': None, '0x19': _SPD, '0x6D': _DIR, '0x7C': _RAIN, '0x7D': _RAIN, '0xA1': _T,
        '0xA2': _T, 'srain_piezo': ('group_boolean', int), 'srain': ('group_boolean', int)}
    rain_map = {'day_rain': 'rainDay', '24h_rain': 'rain24', 'week_rain': 'rainWeek', 'month_rain': 'rainMonth',
                'year_rain': 'rainYear', 'total_rain': 'rainTotal'}
    piezo_rain_map = {'day_rain': 'drain_piezo', '24h_rain': 'rain24_piezo', 'week_rain': 'wrain_piezo',
                      'month_rain': 'mrain_piezo', 'year_rain': 'yrain_piezo', 'total_rain': 'train_piezo'}
    not_registered = ('fffffffe', 'ffffffff')

    def __init__(self, unit_system=DEFAULT_UNIT_SYSTEM, show_battery=DEFAULT_FILTER_BATTERY,
                 log_unknown_fields=True, get_soilad=DEFAULT_GET_SOILAD, debug=None):
        self.unit_system = unit_system
        self.show_battery = show_battery
        self.log_unknown_fields = log_unknown_fields
        self.get_soilad = get_soilad
        self.debug = debug if isinstance(debug, DebugOptions) else DebugOptions()
        register_units()

    # -- generic helpers ---------------------------------------------------

    @staticmethod
    def _require_dict(response, name):
        try:
            return dict(response)
        except (TypeError, ValueError) as e:
            raise ParseError(f"Error parsing '{name}' data: {e}")

    def _post(self, vt, group, post):
        if post == 'vt':
            return vt
        if post == 'conv':
            return weewx.units.convert(vt, weewx.units.std_groups[self.unit_system][group]).value
        return int(vt.value) if post is int else vt.value

    def _put_obs(self, dest, src, key, group, post=None, device_units=None, bad=None):
        """Parse observation src[key] into dest[key]; return False if key is absent."""
        if key not in src:
            return False
        try:
            vt = self.parse_obs_value(key, src, group, device_units)
        except (ParseError, UnitError):
            dest[key] = bad
        else:
            dest[key] = self._post(vt, group, post)
        return True

    @staticmethod
    def _channels(response, key='channel'):
        """Yield (item, {'channel': n}) for list items with a valid channel number."""
        for item in response:
            try:
                yield item, {'channel': int(item[key])}
            except (KeyError, TypeError, ValueError):
                continue

    @staticmethod
    def _first(response, name):
        try:
            return response[0]
        except (KeyError, TypeError) as e:
            raise ParseError(f"Cannot parse '{name}' array: {e}")

    def _log_skip(self, msg, *args):
        if weewx.debug or self.debug.parser or self.log_unknown_fields:
            log.info(msg, *args)

    # -- API responses ---------------------------------------------------------

    @staticmethod
    def parse_get_version(response):
        EcowittHttpParser._require_dict(response, 'parse_get_version')
        parsed = {}
        if 'version' in response:
            version = str(response['version'])
            parsed['version'] = version
            parsed['stationtype'] = version[8:].strip(' \n\r')
            parts = version.split('_')
            parsed['firmware_version'] = parts[1].strip() if len(parts) > 1 else None
        _copy(parsed, response, _same(weeutil.weeutil.to_int, 'newVersion'))
        parsed['platform'] = response.get('platform')
        return parsed

    def parse_get_livedata_info(self, response, flatten_data=True):
        if response is None:
            raise ParseError("Error parsing 'get_livedata_info' data: No raw data")
        data = {}
        for group, group_data in response.items():
            fn = getattr(self, f'process_{group}_array', None)
            if fn is None:
                if weewx.debug or self.log_unknown_fields:
                    log.info("Skipped unknown livedata group '%s'", group)
                continue
            try:
                data[group] = fn(group_data)
            except ProcessorError as e:
                self._log_skip("Error processing livedata group '%s': %s", group, e)
        return flatten(data) if flatten_data else data

    def parse_get_sensors_info(self, response, connected_only=DEFAULT_ONLY_REGISTERED_SENSORS, flatten_data=True):
        if response is None:
            raise ParseError("Error parsing 'get_sensors_info' data: No raw data")
        parsed = {}
        for sensor in response:
            model, channel, data = self.process_sensor_array(sensor, connected_only=connected_only)
            if model is not None:
                if channel is not None:
                    parsed.setdefault(model, {})[channel] = data
                else:
                    parsed[model] = data
        return flatten(parsed) if flatten_data else parsed

    @staticmethod
    def parse_get_ws_settings(response):
        parsed = EcowittHttpParser._require_dict(response, 'parse_get_ws_settings')
        _copy(parsed, response, _same(weeutil.weeutil.to_int, 'ost_interval', 'wu_interval', 'wcl_interval',
                                      'wow_interval', 'mqtt_transport', 'mqtt_port', 'mqtt_keepalive',
                                      'mqtt_interval', 'ecowitt_port', 'ecowitt_upload', 'usr_wu_port',
                                      'usr_wu_upload'))
        for key, fn in (('Customized', lambda v: v.lower() == 'enable'), ('Protocol', lambda v: v.lower())):
            if key in response:
                try:
                    parsed[key] = fn(response[key])
                except AttributeError:
                    parsed[key] = None
        return parsed

    @staticmethod
    def parse_get_stationtype(response):
        EcowittHttpParser._require_dict(response, 'parse_get_version')
        return {'stationtype': str(response['version'])[8:].strip(' \n\r')} if 'version' in response else {}

    @staticmethod
    def parse_get_calibration_data(response, device_units):
        EcowittHttpParser._require_dict(response, 'parse_get_calibration_data')
        to_float = weeutil.weeutil.to_float
        parsed = {dest: _try(to_float, response.get(src)) for dest, src in (
            ('solar_wave', 'SolarRadWave'), ('solar_gain', 'solarRadGain'), ('uv_gain', 'uvGain'),
            ('wind_gain', 'windGain'))}
        for dest, src, group, unit in (
                ('intemp_offset', 'inTempOffset', 'group_deltat', None),
                ('inhumid_offset', 'inHumiOffset', 'group_percent', 'percent'),
                ('abs_offset', 'absOffset', 'group_pressure', None),
                ('rel_offset', 'relOffset', 'group_pressure', None),
                ('altitude', 'altitude', 'group_altitude', None),
                ('outtemp_offset', 'outTempOffset', 'group_deltat', None),
                ('outhumid_offset', 'outHumiOffset', 'group_percent', 'percent'),
                ('winddir_offset', 'windDirOffset', 'group_direction', 'degree_compass')):
            if src in response:
                vt = _to_vt(response[src], None, group)
                parsed[dest] = vt if vt is _BLANK else weewx.units.ValueTuple(vt.value, unit or device_units[group], vt.group)
        _copy(parsed, response, _same(weeutil.weeutil.to_bool, 'th_cli', 'wh34_cli', 'pm25_cli', 'soil_cli'))
        return parsed

    @staticmethod
    def parse_get_rain_totalspart(response, device_units=None):
        EcowittHttpParser._require_dict(response, 'get_rain_totals')
        to_int = weeutil.weeutil.to_int
        parsed = {dest: _try(fn, response.get(src)) for dest, src, fn in (
            ('rain_priority', 'rainFallPriority', to_int), ('rain_gain', 'rainGain', weeutil.weeutil.to_float),
            ('rain_reset_day', 'rstRainDay', to_int), ('rain_reset_week', 'rstRainWeek', to_int),
            ('rain_reset_year', 'rstRainYear', to_int), ('rain_piezo', 'piezo', to_int))}
        return parsed

    @staticmethod
    def parse_get_rain_totals(response, device_units):
        parsed = EcowittHttpParser.parse_get_rain_totalspart(response)
        if 'list' in response:
            parsed['rain_list'] = response['list']
            for gauge in parsed['rain_list']:
                if 'value' in gauge:
                    gauge['value'] = weeutil.weeutil.to_int(gauge['value'])
        for dest, src in EcowittHttpParser.rain_map.items():
            if src in response:
                parsed[dest] = _to_vt(response[src], device_units['group_rain'], 'group_rain')
        return parsed

    @staticmethod
    def parse_get_piezo_rainpart(response, device_unit_data=None):
        EcowittHttpParser._require_dict(response, 'get_piezo_rain')
        return _copy({}, response, tuple((f'gain{n}', f'rain{n}_gain', weeutil.weeutil.to_float)
                                         for n in range(1, 6)))

    @staticmethod
    def parse_get_piezo_rain(response, device_unit_data):
        parsed = EcowittHttpParser.parse_get_piezo_rainpart(response)
        for dest, src in EcowittHttpParser.piezo_rain_map.items():
            if src in response:
                parsed[dest] = _to_vt(response[src], device_unit_data['group_rain'], 'group_rain')
        return parsed

    @staticmethod
    def parse_get_device_infopart(response):
        return _copy({}, response, _same(weeutil.weeutil.to_int, 'radcompensation', 'upgrade', 'newVersion')
                     + (('apName', 'apName', None),))

    @staticmethod
    def parse_get_device_info(response):
        to_int = weeutil.weeutil.to_int
        parsed = _copy({}, response, (
            ('sensor_type', 'sensorType', to_int), ('rf_freq', 'rf_freq', to_int), ('afc', 'AFC', to_int),
            ('tz_auto', 'tz_auto', to_int), ('tz_name', 'tz_name', None), ('tz_index', 'tz_index', to_int),
            ('dst', 'dst_stat', to_int), ('rad_comp', 'radcompensation', to_int), ('upgrade', 'upgrade', to_int),
            ('ap_auto', 'apAuto', to_int), ('newVersion', 'newVersion', to_int), ('curr_msg', 'curr_msg', None),
            ('ap', 'apName', None), ('ap_pwd', 'APpwd', obfuscate), ('time', 'time', to_int),
            ('date', 'date', lambda v: int(time.mktime(datetime.datetime.strptime(v, '%Y-%m-%dT%H:%M').timetuple())))))
        return parsed

    @staticmethod
    def parse_get_network_info(response):
        parsed = EcowittHttpParser._require_dict(response, 'parse_get_network_info')
        wifi_pwd = parsed.pop('wifi_pwd', None)
        _copy(parsed, response, (('eth_ip_type', 'ethIpType', weeutil.weeutil.to_int),
                                 ('staIpType', 'staIpType', weeutil.weeutil.to_int)))
        if wifi_pwd is not None:
            parsed['wifi_pwd'] = obfuscate(wifi_pwd)
        return parsed

    @staticmethod
    def parse_get_units_info(response):
        parsed = EcowittHttpParser._require_dict(response, 'parse_get_units_info')
        return {k: _try(weeutil.weeutil.to_int, v) for k, v in parsed.items()}

    @staticmethod
    def parse_get_cli_soiladnow(response):
        parsed = {}
        for sensor, d in EcowittHttpParser._channels((s for s in response if isinstance(s, dict)), 'ch'):
            _copy(parsed, sensor, ((f"ch_soil{d['channel']}nowAd", 'nowAd', weeutil.weeutil.to_int),))
        return parsed

    @staticmethod
    def parse_get_cli_soilad(response):
        result = []
        for sensor, d in EcowittHttpParser._channels((s for s in response if isinstance(s, dict)), 'ch'):
            _copy(d, sensor, (('id', 'id', None), ('name', 'name', None))
                  + _same(weeutil.weeutil.to_int, 'soilVal', 'nowAd', 'minVal', 'maxVal')
                  + (('checked', 'checked', weeutil.weeutil.to_bool),))
            result.append(d)
        return sorted(result, key=itemgetter('channel'))

    def _parse_cli_list(self, response, obs, device_units):
        """Parse a calibration list; obs is ((key, unit group), ...), all required."""
        result = []
        for sensor, d in self._channels((s for s in response if isinstance(s, dict))):
            _copy(d, sensor, (('id', 'id', None), ('name', 'name', None)))
            if all(self._put_obs(d, sensor, k, g, 'vt', device_units) for k, g in obs):
                result.append(d)
        return sorted(result, key=itemgetter('channel'))

    def parse_get_cli_multich(self, response, device_units=None):
        return self._parse_cli_list(response, (('temp', 'group_deltat'), ('humi', 'group_percent')), device_units)

    def parse_get_cli_pm25(self, response, device_units=None):
        return self._parse_cli_list(response, (('val', 'group_concentration'),), device_units)

    def parse_get_cli_wh34(self, response, device_units=None):
        if device_units is None or 'group_temperature' not in device_units:
            raise ParseError('No WN34 or device temperature unit information.')
        result = self._parse_cli_list(response, (('temp', 'group_deltat'),), device_units)
        for d in result:
            d.setdefault('id', None)
            d.setdefault('name', None)
        return result

    def parse_get_cli_co2(self, response, device_units=None):
        self._require_dict(response, 'parse_get_cli_co2')
        parsed = {}
        for key in ('pm1', 'pm25', 'pm4', 'pm10'):
            self._put_obs(parsed, response, key, 'group_concentration', 'vt', device_units)
        self._put_obs(parsed, response, 'co2', 'group_fraction', 'vt', device_units)
        return _copy(parsed, response, (('id', 'id', None), ('name', 'name', None)))

    def parse_get_cli_lds(self, response):
        result = []
        for sensor, d in self._channels(response, 'ch'):
            for key in ('offset', 'total_height'):
                self._put_obs(d, sensor, key, 'group_depth', 'vt', bad=_BLANK)
            _copy(d, sensor, _same(int, 'total_heat', 'level'))
            if set(d) & {'offset', 'total_height', 'total_heat'}:
                d['id'], d['name'] = sensor.get('id'), sensor.get('name')
                result.append(d)
        return sorted(result, key=itemgetter('channel'))

    @staticmethod
    def parse_get_sdmmc_info(response):
        try:
            parsed = dict(response)
            parsed['info']['interval'] = int(response['info']['Interval'])
        except (KeyError, TypeError, ValueError) as e:
            raise ParseError(f"Error parsing 'get_sdmmc_info' data: {e}")
        return parsed

    # -- livedata groups ---------------------------------------------------

    def process_common_list_array(self, response):
        result = {}
        for item in response:
            if 'id' not in item:
                continue
            oid = item['id']
            if oid not in self.object_kinds:
                self._log_skip("Skipped unknown livedata observation ID '%s'", oid)
                continue
            kind = self.object_kinds[oid]
            if kind is None:
                result[oid] = None
                continue
            try:
                result[oid] = self._process_object(item, *kind)
            except ProcessorError as e:
                if weewx.debug >= 2 or self.debug.parser:
                    log.info("Error processing common_list ID '%s': %s", oid, e)
                result[oid] = {'val': None}
        return result

    process_rain_array = process_piezoRain_array = process_common_list_array

    def _process_object(self, item, group, post):
        obj = {'id': item['id']}
        try:
            vt = self.parse_obs_value('val', item, group)
        except (KeyError, UnitError) as e:
            raise ProcessorError(e) from e
        except ParseError:
            obj['val'] = None
        else:
            obj['val'] = self._post(vt, group, post)
        if group in ('group_rain', 'group_rainrate'):
            _copy(obj, item, _same(int, 'battery'), skip_bad=True)
        self._put_obs(obj, item, 'voltage', 'group_volt')
        if group == 'group_rain':
            for key in ('ws85cap_volt', 'ws90cap_volt'):
                self._put_obs(obj, item, key, 'group_volt')
            _copy(obj, item, _same(int, 'ws85_ver', 'ws90_ver'))
        return obj

    def process_ch_aisle_array(self, response):
        result = []
        for sensor, d in self._channels(response):
            if any([self._put_obs(d, sensor, 'temp', 'group_temperature', 'conv'),
                    self._put_obs(d, sensor, 'humidity', 'group_percent', int)]):
                d['name'] = sensor.get('name')
                result.append(d)
        return result

    def process_ch_temp_array(self, response):
        result = []
        for sensor, d in self._channels(response):
            if self._put_obs(d, sensor, 'temp', 'group_temperature', 'conv'):
                d['name'] = sensor.get('name')
                self._put_obs(d, sensor, 'voltage', 'group_volt')
                result.append(d)
        return result

    def process_ch_lds_array(self, response):
        result = []
        for sensor, d in self._channels(response):
            found = [self._put_obs(d, sensor, k, 'group_depth', 'conv') for k in ('air', 'depth', 'total_height')]
            if found[0] or found[1]:
                _copy(d, sensor, _same(int, 'total_heat'), skip_bad=True)
                d['name'] = sensor.get('name')
                self._put_obs(d, sensor, 'voltage', 'group_volt')
                result.append(d)
        return result

    def process_ch_ec_array(self, response):
        result = []
        for sensor, d in self._channels(response):
            if not (self._put_obs(d, sensor, 'humidity', 'group_percent', int)
                    and self._put_obs(d, sensor, 'temp', 'group_temperature', 'conv')):
                continue
            if self._put_obs(d, sensor, 'ec', 'group_usiecm', int) and d['ec'] == 4095:
                d['ec'] = None
            d['name'] = sensor.get('name')
            self._put_obs(d, sensor, 'voltage', 'group_volt')
            result.append(d)
        return result

    def process_ch_soil_array(self, response):
        result = []
        for sensor, d in self._channels(response):
            if self._put_obs(d, sensor, 'humidity', 'group_percent', int):
                d['name'] = sensor.get('name')
                self._put_obs(d, sensor, 'voltage', 'group_volt')
                result.append(d)
        return result

    def process_ch_pm25_array(self, response):
        result = []
        for sensor, d in self._channels(response):
            _copy(d, sensor, _same(float, 'PM25', 'PM25_24H') + _same(int, 'PM25_RealAQI', 'PM25_24HAQI'))
            if set(d) & {'PM25', 'PM25_RealAQI', 'PM25_24HAQI'}:
                result.append(d)
        return result

    def process_wh25_array(self, response):
        item, d = self._first(response, 'wh25'), {}
        self._put_obs(d, item, 'intemp', 'group_temperature', 'conv')
        self._put_obs(d, item, 'inhumi', 'group_percent', int)
        for key in ('abs', 'rel'):
            self._put_obs(d, item, key, 'group_pressure', 'conv')
        return _copy(d, item, _same(int, 'CO2', 'CO2_24H'))

    def process_lightning_array(self, response):
        item = self._first(response, 'lightning')
        d = _copy({}, item, _same(int, 'count'))
        self._put_obs(d, item, 'distance', 'group_distance', 'conv')
        _copy(d, item, (('date', 'date', None), ('timestamp', 'timestamp', lambda v: int(time.mktime(
            datetime.datetime.strptime(v, '%m/%d/%Y %H:%M:%S').timetuple())))))
        return d

    def process_co2_array(self, response):
        item, d = self._first(response, 'co2'), {}
        self._put_obs(d, item, 'temp', 'group_temperature', 'conv')
        self._put_obs(d, item, 'humidity', 'group_percent', int)
        _copy(d, item, _same(int, 'CO2', 'CO2_24H'))
        for pm in ('PM25', 'PM10', 'PM1', 'PM4'):
            _copy(d, item, _same(float, pm, f'{pm}_24H') + _same(int, f'{pm}_RealAQI', f'{pm}_24HAQI'))
        return d

    def process_ch_leaf_array(self, response):
        result = []
        for item in response:
            d = dict(item)
            _copy(d, item, (('humidity', 'humidity', _pct),))
            d['name'] = item.get('name')
            self._put_obs(d, item, 'voltage', 'group_volt')
            result.append(d)
        return result

    def process_ch_leak_array(self, response):
        result = []
        for item in response:
            d = dict(item)
            if 'status' in item:
                d['status'] = {'normal': 0, 'leaking': 1}.get(str(item['status']).lower())
            _copy(d, item, _same(int, 'battery'))
            self._put_obs(d, item, 'voltage', 'group_volt')
            result.append(d)
        return result

    def process_debug_array(self, response):
        item = self._first(response, 'debug')
        return _copy({}, item, _same(int, 'heap', 'runtime', 'usr_interval')
                     + _same(weeutil.weeutil.to_bool, 'is_cnip'))

    def process_console_array(self, response):
        item = self._first(response, 'console')
        d = _copy({}, item, _same(int, 'battery', 'charge_stat'))
        for key in ('console_batt_volt', 'console_ext_volt'):
            self._put_obs(d, item, key, 'group_volt')
        return d

    def process_wqt01_array(self, response):
        item, d = self._first(response, 'wqt01'), {}
        self._put_obs(d, item, 'ec', 'group_usiecm', int)
        for key in ('toc', 'cod', 'tds'):
            self._put_obs(d, item, key, 'group_organicpollution')
        _copy(d, item, _same(int, 'turb', 'CO2', 'CO2_24H', 'battery'))
        self._put_obs(d, item, 'voltage', 'group_volt')
        return d

    def process_sensor_array(self, sensor, connected_only):
        sensor_id = sensor.get('id')
        unregistered = sensor_id.lower() in self.not_registered
        if connected_only and unregistered:
            return None, None, None
        data = {'address': _try(int, sensor.get('type')), 'id': sensor_id}
        try:
            data['battery'] = (None if not self.show_battery and int(sensor.get('signal')) == 0
                               else int(sensor.get('batt')))
        except (TypeError, ValueError):
            data['battery'] = None
        data['rssi'] = None if unregistered else _try(int, sensor.get('rssi'))
        data['signal'] = None if unregistered else _try(int, sensor.get('signal'))
        data['enabled'] = _try(lambda v: int(v) == 1, sensor.get('idst'))
        if 'version' in sensor:
            data['version'] = sensor['version']
        match = re.search(r'CH\d+', sensor.get('name') or '')
        return sensor.get('img'), match.group(0).lower() if match else None, data

    # -- values and units --------------------------------------------------------

    @staticmethod
    def get_model(text):
        if text is not None:
            return next((m for m in KNOWN_DEVICES if m in text.upper()), None)
        return None

    get_model_from_firmware = get_model

    def parse_obs_value(self, key, json_object, unit_group, device_units=None):
        """Return a ValueTuple for json_object[key] (raises KeyError if absent)."""
        device_units = self.default_device_units if device_units is None else device_units
        match = re.match(r'([0-9.,+-]+)(.*)', str(json_object[key]))
        if match is None:
            raise ParseError(f"Could not determine value and unit for '{key}' in JSON object '{json_object}'")
        value, unit = match.group(1), match.group(2).strip().lower()
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            raise ParseError(f"Could not convert '{value}' to a float")
        unit_str = unit if unit else json_object['unit'] if 'unit' in json_object else None
        if unit_str is not None:
            try:
                return weewx.units.ValueTuple(numeric, self.get_weewx_unit(unit_str, unit_group), unit_group)
            except UnitError as e:
                log.error("parse_obs_value: Could not determine unit applicable to field '%s: %s': %s",
                          key, numeric, e)
                raise ParseError(f"Could not equate Ecowitt unit '{unit_str}' with a WeeWX unit")
        if unit_group not in device_units:
            raise UnitError(f"Could not determine device units for '{unit_group}'")
        return weewx.units.ValueTuple(numeric, device_units[unit_group], unit_group)

    @classmethod
    def get_weewx_unit(cls, unit_string, unit_group=None):
        try:
            unit = cls.unit_lookup[unit_string.lower()]
        except (AttributeError, KeyError):
            raise UnitError(f"unknown Ecowitt unit string: '{unit_string}'")
        if unit_group is not None and unit_group.lower() in ('group_depth', 'group_deltat'):
            unit += '2'
        return unit
