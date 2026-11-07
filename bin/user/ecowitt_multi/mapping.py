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
# Modified by mKainzbauer, 2026-11: extracted from the single-file upstream driver; default maps can
# be replaced by maps built from the sensor catalog (catalog.py).
"""Sensor tables, field mappers, counter delta tracking and channel remapping."""

import datetime
import logging
import math
import re

import weeutil.weeutil
import weewx
import weewx.units

from .util import InvertibleMap, chmap as _chmap, natural_sort_dict, rng as _rng

log = logging.getLogger(__name__)


# Source: S2 weewx-EcowittGateway @5cbe533, bin/user/weewx-EcowittGateway.py, lines 287-391: static sensor tables, wind_array, route_wind_voltage, _build_default_groups. Unchanged.
# sensor model: number of channels (0 = single sensor)
SENSOR_CHANNELS = {'wn20': 0, 'wh24': 0, 'wh25': 0, 'wh26': 0, 'wh65': 0, 'wn32': 0, 'wn32p': 0, 'wn31': 8,
                   'wn34': 8, 'wn35': 8, 'wn38': 0, 'wh40': 0, 'wh41': 4, 'wh45': 0, 'wh51': 16, 'wh54': 4,
                   'wh55': 4, 'wh57': 0, 'wh68': 0, 'wh69': 0, 'ws80': 0, 'ws85': 0, 'ws90': 0}


# multi-channel sensor model: (number of channels, live data groups it feeds)
SENSOR_GROUPS = {'wn31': (8, ('ch_aisle',)), 'wn34': (8, ('ch_temp',)), 'wn35': (8, ('ch_leaf',)),
                 'wh41': (4, ('ch_pm25',)), 'wh51': (16, ('ch_soil', 'ch_ec')), 'wh54': (4, ('ch_lds',)),
                 'wh55': (4, ('ch_leak',))}
_GROUP_MODEL = {group: model for model, (_, groups) in SENSOR_GROUPS.items() for group in groups}
# a live data field that helps identify each multi-channel sensor
_SENSOR_READING = {'ch_aisle': ('temp', 'humidity'), 'ch_temp': ('temp',), 'ch_leaf': ('humidity',),
                   'ch_pm25': ('PM25',), 'ch_soil': ('humidity',), 'ch_ec': ('temp', 'ec'),
                   'ch_lds': ('depth',), 'ch_leak': ('status',)}
_READING_UNIT = {'temp': '\u00b0', 'humidity': '%', 'PM25': ' \u00b5g/m\u00b3', 'ec': ' \u00b5S/cm', 'depth': ' mm'}
_UNREGISTERED_IDS = ('FFFFFFFE', 'FFFFFFFF')


def _registered(data, model):
    """True if flattened sensor data shows a sensor paired in this model's slot."""
    sensor_id = data.get(f'{model}.id')
    return sensor_id is not None and str(sensor_id).upper() not in _UNREGISTERED_IDS


def wind_array(sensor_data):
    """The wind array whose battery voltage the device reports with the wind direction (0x0A).

    'wh68' when a WS68 is paired and a WS80 is not, otherwise 'ws80' (also when neither, or both, are
    paired, which keeps the earlier behaviour).
    """
    return 'wh68' if _registered(sensor_data, 'wh68') and not _registered(sensor_data, 'ws80') else 'ws80'


def route_wind_voltage(data, array):
    """Move the 0x0A wind array voltage to 'wh68.voltage' when the array is a WS68, so that it is reported
    as ws68_batt rather than ws80_batt. The WS68 battery state (a level) stays in wh68_batt."""
    if array == 'wh68' and 'common_list.0x0A.voltage' in data:
        data.setdefault('wh68.voltage', data.pop('common_list.0x0A.voltage'))


def _sensor_names(model, channels):
    return [model] if channels == 0 else _rng(model + '.ch{}', channels)


def _build_default_groups():
    """Unit group of every (flattened) field the driver can emit."""
    T, C, V, PCT, B = 'group_temperature', 'group_count', 'group_volt', 'group_percent', 'group_boolean'
    SPD, RAD, R, CONC, FRAC = 'group_speed', 'group_radiation', 'group_rain', 'group_concentration', 'group_fraction'
    g = {'datetime': 'group_time'}
    for obs, grp in (('0x02', T), ('0x03', T), ('3', T), ('0x04', T), ('4', T), ('0x05', T),
                     ('5', 'group_pressurevpd'), ('0x07', PCT), ('0x0A', 'group_direction'), ('0x0B', SPD),
                     ('0x0C', SPD), ('0x0F', SPD), ('0x14', SPD), ('0x15', RAD), ('0x16', RAD), ('0x17', 'group_uv'),
                     ('0x19', SPD), ('0xA1', T)):
        g.update({f'common_list.{obs}.val': grp, f'common_list.{obs}.battery': C, f'common_list.{obs}.voltage': V})
    g['common_list.0xA2.val'] = T
    for arr in ('rain', 'piezoRain'):
        for obs in ('0x0D', '0x0E', '0x10', '0x11', '0x12', '0x13'):
            g.update({f'{arr}.{obs}.val': R, f'{arr}.{obs}.battery': C, f'{arr}.{obs}.voltage': V})
        g.update({f'{arr}.0x0E.val': 'group_rainrate', f'{arr}.0x0F.val': R, f'{arr}.0x7D.val': R,
                  f'{arr}.0x14.val': R})
    g.update({'rain.srain': B, 'rain.srain.val': B, 'piezoRain.srain_piezo': B, 'piezoRain.srain_piezo.val': B,
              't_rain': R, 't_rainyear': R, 'p_rain': R, 'p_rainyear': R,
              'piezoRain.0x13.ws85cap_volt': V, 'piezoRain.0x13.ws90cap_volt': V,
              'piezoRain.0x13.ws85_ver': C, 'piezoRain.0x13.ws90_ver': C,
              'wh25.intemp': T, 'wh25.inhumi': PCT, 'wh25.abs': 'group_pressure', 'wh25.rel': 'group_pressure',
              'wh25.CO2': FRAC, 'wh25.CO2_24H': FRAC,
              'console.battery': C, 'console.console_batt_volt': V, 'console.console_ext_volt': V,
              'console.charge_stat': C, 'console.battery_proz': PCT,
              'lightning.distance': 'group_distance', 'lightning.timestamp': 'group_time',
              'lightning.count': C, 'lightning.num': C,
              'co2.temperature': T, 'co2.temp': T, 'co2.humidity': PCT, 'co2.CO2': FRAC, 'co2.CO2_24H': FRAC,
              'co2.battery': C,
              'debug.heap': 'group_data', 'debug.runtime': 'group_deltatime',
              'debug.usr_interval': 'group_deltatime', 'debug.is_cnip': B,
              'apName': 'group_string', 'stationtype': 'group_string'})
    for pm in ('PM25', 'PM10', 'PM1', 'PM4'):
        g.update({f'co2.{pm}': CONC, f'co2.{pm}_RealAQI': C, f'co2.{pm}_24HAQI': C})
    for ch in range(1, 17):
        g.update({f'ch_soil.{ch}.humidity': PCT, f'ch_soil.{ch}.voltage': V, f'ch_soil{ch}nowAd': C,
                  f'ch_ec.{ch}.temp': T, f'ch_ec.{ch}.ec': 'group_usiecm', f'wh51.ch{ch}.voltage': V})
        if ch <= 8:
            g.update({f'ch_aisle.{ch}.temp': T, f'ch_aisle.{ch}.humidity': PCT, f'ch_temp.{ch}.temp': T,
                      f'ch_temp.{ch}.voltage': V, f'ch_leaf.{ch}.humidity': PCT, f'ch_leaf.{ch}.voltage': V})
        if ch <= 4:
            g.update({f'ch_pm25.{ch}.PM25': CONC, f'ch_pm25.{ch}.PM25_24H': CONC, f'ch_pm25.{ch}.PM25x': CONC,
                      f'ch_pm25.{ch}.PM25_RealAQI': C, f'ch_pm25.{ch}.PM25_24HAQI': C, f'ch_leak.{ch}.status': C,
                      f'ch_lds.{ch}.air': 'group_depth', f'ch_lds.{ch}.depth': 'group_depth',
                      f'ch_lds.{ch}.total_height': 'group_depth', f'ch_lds.{ch}.total_heat': C,
                      f'ch_lds.{ch}.voltage': V})
    for model, channels in SENSOR_CHANNELS.items():
        for name in _sensor_names(model, channels):
            g.update({f'{name}.battery': C, f'{name}.signal': C, f'{name}.rssi': 'group_dbm'})
    for f in ('ws85.version', 'ws85_vers', 'ws90.version', 'ws90_vers', 'radcompensation', 'upgrade', 'newVersion'):
        g[f] = C
    g.update({'wqt01.ec': 'group_usiecm', 'wqt01.toc': 'group_organicpollution', 'wqt01.turb': 'group_ntu',
              'wqt01.cod': 'group_organicpollution', 'wqt01.tds': 'group_organicpollution', 'wqt01.CO2': FRAC,
              'wqt01.CO2_24H': FRAC})
    g['wh68.voltage'] = V
    for model in ('wqt01', 'wn64'):
        g.update({f'{model}.voltage': V, f'{model}.battery': C, f'{model}.signal': C, f'{model}.rssi': 'group_dbm'})
    return g


DEFAULT_GROUPS = _build_default_groups()


# Source: S2 weewx-EcowittGateway @5cbe533, bin/user/weewx-EcowittGateway.py, lines 490-710: FieldMapper, _SIGNAL_SENSORS, _signal_map, HttpMapper, SdMapper. Unchanged.
class FieldMapper:
    """Maps flattened device fields to WeeWX fields."""

    def __init__(self, driver_debug=None, default_map=None, **mapper_config):
        self.driver_debug = driver_debug
        self.wn32_indoor = weeutil.weeutil.tobool(mapper_config.get('wn32_indoor', False))
        self.wn32_outdoor = weeutil.weeutil.tobool(mapper_config.get('wn32_outdoor', False))
        self.field_map = self.construct_field_map(default_map or {}, **mapper_config)

    def construct_field_map(self, def_map, **config):
        default_map = InvertibleMap(def_map)
        for enabled, old, new in ((self.wn32_indoor, 'wh25', 'wn32'), (self.wn32_outdoor, 'wh26', 'wn32p')):
            if enabled:
                for source in (f'{old}.battery', f'{old}.signal'):
                    if source in default_map.inverse:
                        dest = default_map.inverse[source]
                        default_map.pop(dest)
                        default_map[dest.replace(old, new)] = source
        field_map = config.get('field_map')
        if field_map is None:
            field_map = dict(default_map)
        extensions = config.get('field_map_extensions', {})
        if extensions:
            ext_sources = set(extensions.values())
            field_map = {k: v for k, v in field_map.items() if v not in ext_sources}
            field_map.update(extensions)
        return InvertibleMap(field_map)

    def map_data(self, rec, unit_system=None):
        if not self.field_map:
            return rec
        mapped = {dest: rec.get(src) for dest, src in self.field_map.items() if src in rec}
        if unit_system is not None:
            mapped['usUnits'] = unit_system
        return mapped


# (WeeWX field prefix, device model, channels) for signal/rssi fields
_SIGNAL_SENSORS = (('wn20', 'wn20', 0), ('wh25', 'wh25', 0), ('wh26', 'wh26', 0), ('wh31', 'wn31', 8),
                   ('wn34', 'wn34', 8), ('wn35', 'wn35', 8), ('wn38', 'wn38', 0), ('wh40', 'wh40', 0),
                   ('wh41', 'wh41', 4), ('wh45', 'wh45', 0), ('wh51', 'wh51', 16), ('wh54', 'wh54', 4),
                   ('wh55', 'wh55', 4), ('wh57', 'wh57', 0), ('wh68', 'wh68', 0), ('wh69', 'wh69', 0),
                   ('ws80', 'ws80', 0), ('ws85', 'ws85', 0), ('ws90', 'ws90', 0), ('wqt01', 'wqt01', 0),
                   ('wn64', 'wn64', 0))


def _signal_map():
    """WeeWX signal/rssi fields, e.g. 'wh31_ch1_sig': 'wn31.ch1.signal'."""
    result = {}
    for suffix, key in (('sig', 'signal'), ('rssi', 'rssi')):
        for dest, src, channels in _SIGNAL_SENSORS:
            if channels:
                result.update(_chmap(((f'{dest}_ch{{}}_{suffix}', f'{src}.ch{{}}.{key}'),), channels))
            else:
                result[f'{dest}_{suffix}'] = f'{src}.{key}'
    return result


class HttpMapper(FieldMapper):
    """Field mapper for data obtained from the device HTTP API."""

    default_obs_map = {
        'inTemp': 'wh25.intemp', 'inHumidity': 'wh25.inhumi', 'pressure': 'wh25.abs', 'barometer': 'wh25.rel',
        'outTemp': 'common_list.0x02.val', 'dewpoint': 'common_list.0x03.val', 'feelslike': 'common_list.3.val',
        'appTemp': 'common_list.4.val', 'vpd': 'common_list.5.val', 'outHumidity': 'common_list.0x07.val',
        'radiation': 'common_list.0x15.val', 'uvradiation': 'common_list.0x16.val', 'UV': 'common_list.0x17.val',
        'bgt': 'common_list.0xA1.val', 'wbgt': 'common_list.0xA2.val',
        'lightning_dist': 'lightning.distance', 'lightning_disturber_count': 'lightning.timestamp',
        'lightning_num': 'lightning.num', 'lightningcount': 'lightning.count',
        'lightning_strike_count': 'lightning.count',
        **_chmap((('extraTemp{}', 'ch_aisle.{}.temp'), ('extraHumid{}', 'ch_aisle.{}.humidity'),
                  ('soilTemp{}', 'ch_temp.{}.temp')), 8),
        'co2in': 'wh25.CO2', 'co2in_24h': 'wh25.CO2_24H', 'co2': 'co2.CO2', 'co2_24h': 'co2.CO2_24H',
        'co2_Temp': 'co2.temp', 'co2_Hum': 'co2.humidity',
        **{k: v for name, p in (('pm2_5', '25'), ('pm10_0', '10'), ('pm1_0', '1'), ('pm4_0', '4'))
           for k, v in ((name, f'co2.PM{p}'), (f'pm{p}_24h_co2', f'co2.PM{p}_24H'),
                        (f'pm{p}_RealAQI_co2', f'co2.PM{p}_RealAQI'), (f'pm{p}_24hAQI_co2', f'co2.PM{p}_24HAQI'))},
        **_chmap((('pm25_{}', 'ch_pm25.{}.PM25'), ('pm25_avg_24h_ch{}', 'ch_pm25.{}.PM25_24H'),
                  ('pm25_RealAQI_ch{}', 'ch_pm25.{}.PM25_RealAQI'), ('pm25_AQI_24h_ch{}', 'ch_pm25.{}.PM25_24HAQI')),
                 4, channel_major=True),
        **_chmap((('soilMoist{}', 'ch_soil.{}.humidity'), ('soilMoist{}e', 'ch_ec.{}.humidity'),
                  ('soilmTemp{}', 'ch_ec.{}.temp'), ('soilEC{}', 'ch_ec.{}.ec')), 16),
        **_chmap((('leafWet{}', 'ch_leaf.{}.humidity'),), 8),
        **_chmap((('leak_{}', 'ch_leak.{}.status'), ('air_ch{}', 'ch_lds.{}.air'), ('depth_ch{}', 'ch_lds.{}.depth'),
                  ('ldsheat_ch{}', 'ch_lds.{}.total_heat'), ('thi_ch{}', 'ch_lds.{}.total_height')), 4),
        'heap': 'debug.heap', 'runtime': 'debug.runtime', 'ws_interval': 'debug.usr_interval',
        'radcompensation': 'radcompensation', 'upgrade': 'upgrade', 'newVersion': 'newVersion',
        'apName': 'apName', 'stationtype': 'stationtype', 'rain_source': 'rain_priority',
        'rain_day_reset': 'rain_reset_day', 'rain_week_reset': 'rain_reset_week',
        'rain_annual_reset': 'rain_reset_year', 'piezo': 'rain_piezo', 'raingain': 'rain_gain',
        **{f'gain{i}': f'gain{i + 1}' for i in range(5)},
        **_chmap((('soilad{}', 'ch_soil{}nowAd'), ('soilECad{}', 'ch_ec{}nowAd')), 16),
    }
    default_rain_map = {
        't_rainRate': 'rain.0x0E.val', 't_rainyear': 'rain.0x13.val', 't_rain': 't_rain',
        'eventRain': 'rain.0x0D.val', 'rain': 'rain', 'rainRate': 'rain.0x0E.val', 'hourRain': 'rain.0x7D.val',
        'dayRain': 'rain.0x10.val', 'weekRain': 'rain.0x11.val', 'monthRain': 'rain.0x12.val',
        'yearRain': 'rain.0x13.val', 'rain24': 'rain.0x7C.val', 'totalRain': 'rain.0x14.val',
        'srain': 'rain.srain.val', 'srain_piezo': 'piezoRain.srain_piezo.val', 'p_rainrate': 'piezoRain.0x0E.val',
        'p_rainyear': 'piezoRain.0x13.val', 'p_rain': 'p_rain', 'erain_piezo': 'piezoRain.0x0D.val', 'hail': 'hail',
        'rrain_piezo': 'piezoRain.0x0E.val', 'hailRate': 'piezoRain.0x0E.val', 'hrain_piezo': 'piezoRain.0x7D.val',
        'drain_piezo': 'piezoRain.0x10.val', 'wrain_piezo': 'piezoRain.0x11.val',
        'mrain_piezo': 'piezoRain.0x12.val', 'yrain_piezo': 'piezoRain.0x13.val',
        'rain24_piezo': 'piezoRain.0x7C.val', 'train_piezo': 'piezoRain.0x14.val'}
    default_wind_map = {'windDir': 'common_list.0x0A.val', 'windDir10': 'common_list.0x6D.val',
                        'windSpeed': 'common_list.0x0B.val', 'windGust': 'common_list.0x0C.val',
                        'maxdailygust': 'common_list.0x19.val'}
    default_sensor_state_map = {
        'inTempBatteryStatus': 'wh25.battery', 'outTempBatteryStatus': 'wh65.battery',
        'wh25_batt': 'wh25.battery', 'wh26_batt': 'wh26.battery',
        **_chmap((('batteryStatus{}', 'wn31.ch{}.battery'), ('soilTempBatt{}s', 'wn34.ch{}.battery')), 8),
        **_chmap((('pm25_Batt{}', 'wh41.ch{}.battery'),), 4),
        **_chmap((('soilMoistBatt{}s', 'wh51.ch{}.battery'),), 16),
        'co2_Batt': 'wh45.battery', 'wh40_batt': 'wh40.battery', 'wn20_batt': 'wn20.battery',
        'wn38_batt': 'wn38.battery',
        **_chmap((('leak_Batt{}', 'wh55.ch{}.battery'),), 4),
        'lightning_Batt': 'wh57.battery',
        **_chmap((('soilTempBatt{}', 'ch_temp.{}.voltage'),), 8),
        **_chmap((('soilMoistBatt{}', 'ch_soil.{}.voltage'),), 16),
        **_chmap((('leafWetBatt{}', 'ch_leaf.{}.voltage'),), 8),
        **_chmap((('ldsbatt{}', 'ch_lds.{}.voltage'),), 4),
        'bgtbatt': 'common_list.0xA1.voltage', 'wh68_batt': 'wh68.battery', 'wh69_batt': 'wh69.battery',
        'wh80_batt': 'ws80.battery', 'wh85_batt': 'ws85.battery', 'wh90_batt': 'ws90.battery',
        'rain_batt': 'rain.0x13.battery', 'piezorain_batt': 'piezoRain.0x13.battery',
        'ws85cap_volt': 'piezoRain.0x13.ws85cap_volt', 'ws90cap_volt': 'piezoRain.0x13.ws90cap_volt',
        'ws68_batt': 'wh68.voltage', 'ws80_batt': 'common_list.0x0A.voltage', 'ws85_batt': 'ws85.voltage',
        'ws90_batt': 'ws90.voltage',
        'rainBatteryStatus': 'rain.0x13.voltage', 'hailBatteryStatus': 'piezoRain.0x13.voltage',
        'windBatteryStatus': 'ws80.voltage', 'consBatteryVoltage': 'console.console_batt_volt',
        'ws6210_batt': 'console.battery', 'console_batt': 'console.console_batt_volt',
        'consoleext_batt': 'console.console_ext_volt', 'charge_stat': 'console.charge_stat',
        'consolebattp': 'console.battery_proz', 'ws1900batt': 'wh25.ws1900_batt',
        'ws1800batt': 'wh25.ws1800_batt', 'ws6006batt': 'wh25.ws6006_batt', 'wqt01_batt': 'wqt01.battery',
        'wqt01batt': 'wqt01.voltage', 'wn64_batt': 'wn64.battery', 'wn64batt': 'wn64.voltage',
        'ws85_ver': 'piezoRain.0x13.ws85_ver', 'ws90_ver': 'piezoRain.0x13.ws90_ver',
        **_signal_map(),
    }
    default_map = {**default_obs_map, **default_rain_map, **default_wind_map, **default_sensor_state_map}

    def __init__(self, driver_debug=None, default_map=None, **mapper_config):
        super().__init__(driver_debug=driver_debug, default_map=default_map or HttpMapper.default_map,
                         **mapper_config)
        if 'datetime' in self.field_map.inverse:
            self.field_map.pop(self.field_map.inverse['datetime'])
        self.field_map['dateTime'] = 'datetime'
        rain_src, wind_src = set(self.default_rain_map.values()), set(self.default_wind_map.values())
        self.rain_map = {d: s for d, s in self.field_map.items() if s in rain_src}
        self.wind_map = {d: s for d, s in self.field_map.items() if s in wind_src}
        for w_field, e_field in self.field_map.items():
            if w_field not in weewx.units.obs_group_dict and e_field in DEFAULT_GROUPS:
                weewx.units.obs_group_dict[w_field] = DEFAULT_GROUPS[e_field]
        if getattr(driver_debug, 'any', False) or weewx.debug > 0:
            log.info('     field map is %s', natural_sort_dict(self.field_map))


class SdMapper(FieldMapper):
    """Field mapper for device SD card history (CSV column names)."""

    default_map = {
        'wh25.intemp': 'Indoor Temperature', 'wh25.inhumi': 'Indoor Humidity', 'wh25.abs': 'ABS Pressure',
        'wh25.rel': 'REL Pressure', 'feelslike': 'Feels Like', 'common_list.0x02.val': 'Outdoor Temperature',
        'common_list.0x07.val': 'Outdoor Humidity', 'common_list.0x03.val': 'Dew Point', 'common_list.5.val': 'VPD',
        'common_list.0x0B.val': 'Wind', 'common_list.0x0C.val': 'Gust', 'common_list.0x0A.val': 'Wind Direction',
        'common_list.0x6D.val': 'windDir_10min_avg', 'common_list.0x15.val': 'Solar Rad',
        'common_list.0x17.val': 'UV-Index', 'common_list.0xA1.val': 'BGT', 'common_list.0xA2.val': 'WBGT',
        **{f'{arr}.{obs}.val': f'{pre}{name}' for arr, pre in (('rain', ''), ('piezoRain', 'Piezo '))
           for obs, name in (('0x0E', 'Rate' if pre else 'Rain Rate'), ('0x7D', 'Hourly Rain'), ('0x0D', 'Event Rain'),
                             ('0x10', 'Daily Rain'), ('0x7C', '24h Rain'), ('0x11', 'Weekly Rain'),
                             ('0x12', 'Monthly Rain'), ('0x13', 'Yearly Rain'), ('0x14', 'Total Rain'))},
        'rain.srain.val': 'Rain Status', 'piezoRain.srain_piezo.val': 'Piezo srain',
        **_chmap((('ch_aisle.{}.temp', 'CH{} Temperature'), ('ch_aisle.{}.humidity', 'CH{} Humidity'),
                  ('dewpoint{}', 'CH{} Dew point'), ('heatindex{}', 'CH{} HeatIndex'),
                  ('ch_leaf.{}.humidity', 'WH35 CH{}hum'), ('ch_temp.{}.temp', 'WN34 CH{}')), 8),
        'lightning.timestamp': 'Thunder time', 'lightning.count': 'Thunder count',
        'lightning.distance': 'Thunder distance', 'co2.temp': 'AQIN Temperature', 'co2.humidity': 'AQIN Humidity',
        'co2.CO2': 'AQIN CO2', 'co2.PM25': 'AQIN PM2.5', 'co2.PM10': 'AQIN PM10', 'co2.PM1': 'AQIN PM1.0',
        'co2.PM4': 'AQIN PM4.0',
        **_chmap((('ch_soil.{}.humidity', 'SoilMoisture CH{}'), ('ch_soil{}nowAd', 'SoilMoistureAD CH{}'),
                  ('ch_ec.{}.temp', 'SoilTemp CH{}'), ('ch_ec.{}.ec', 'SoilEC CH{}')), 16),
        **_chmap((('ch_leak.{}.status', 'Water CH{}'), ('ch_pm25.{}.PM25x', 'Pm2.5 CH{}'),
                  ('ch_pm25.{}.PM25', 'PM2.5 CH{}'), ('ch_lds.{}.air', 'LDS_Air CH{}'),
                  ('ch_lds.{}.depth', 'LDS_Depth CH{}'), ('ch_lds.{}.total_heat', 'LDS_Heat CH{}')), 4),
        'console.console_batt_volt': 'Console Battery ', 'console.console_ext_volt': 'External Supply ',
        'console.battery_proz': 'Console Battery Percentage', 'console.charge_stat': 'Charge',
        **{f'wqt01.{k}': f'WQT_{k.upper()}' for k in ('ec', 'toc', 'turb', 'cod', 'tds', 'CO2')},
    }
    _leak_status = {'Normal': 0, 'Leaking': 1, 'Offline': 2}

    def __init__(self, driver_debug=None, default_map=None, **mapper_config):
        super().__init__(driver_debug=driver_debug, default_map=default_map or SdMapper.default_map,
                         **mapper_config)

    def map_data(self, rec, unit_system=None):
        if not self.field_map:
            return rec
        debug = getattr(self.driver_debug, 'catchup', False)
        mapped = {}
        for field, value in rec.items():
            key = re.sub(r'\(.*?\)', '', field)
            if 'Time' in key or key == '':
                if debug:
                    log.info('Problem with key %s', key)
                continue
            try:
                pm = re.search(r'Pm2\.5 CH([1-4])', key)
                dest = f'ch_pm25.{pm.group(1)}.PM25' if pm else self.field_map.inverse[key]
                if key == 'Thunder time':
                    mapped[dest] = datetime.datetime.strptime(value, '%Y-%m-%d %H:%M').timestamp()
                elif value in ('--', ''):
                    if debug:
                        log.info('no Data field %s', key)
                elif 'Water' in key:
                    if value in self._leak_status:
                        mapped[dest] = self._leak_status[value]
                else:
                    mapped[dest] = float(value)
            except (KeyError, TypeError, ValueError, OverflowError) as e:
                if debug:
                    log.info("Error mapping field '%s': %s", field, e)
        return mapped


# Source: S2 weewx-EcowittGateway @5cbe533, bin/user/weewx-EcowittGateway.py, lines 716-756: DeltaTracker, calc_twb. Unchanged.
class DeltaTracker:
    """Turns a cumulative device counter into per-period deltas."""

    def __init__(self, label, candidates, debug=None, prefix='', flag='raindelta'):
        self.label, self.candidates, self.debug, self.prefix, self.flag = label, candidates, debug, prefix, flag
        self.field = self.last = self.delta = None

    def update(self, data):
        """Update from a data dict and return the latest delta."""
        if self.field is None:
            self.field = next((f for f in self.candidates if f in data), None)
            if self.field is not None:
                log.info("%sUsing '%s' for %s total", self.prefix, self.field, self.label)
            elif self.debug is not None and self.debug.rain:
                log.info('%sNo suitable field found for %s', self.prefix, self.label)
        if self.field is not None and self.field in data:
            self.delta = self.calc(data[self.field])
        return self.delta

    def calc(self, new):
        last, self.last = self.last, new
        if last is None:
            log.info('%sskipping %s measurement of %s: no last value', self.prefix, self.label, new)
            return None
        if new is None:
            log.info('%sskipping %s measurement: no current data', self.prefix, self.label)
            return None
        delta = new if new < last else new - last
        if new < last:
            log.info('%s%s counter wraparound detected: new=%s last=%s', self.prefix, self.label, new, last)
        if self.debug is not None and getattr(self.debug, self.flag) and delta != 0:
            log.info('%s%s: last=%s new=%s delta=%s', self.prefix, self.label, last, new, delta)
        return delta


def calc_twb(temp_c, humidity):
    """Stull wet bulb temperature (degree C) used as a WBGT estimate."""
    return (temp_c * math.atan(0.151977 * (humidity + 8.313659) ** 0.5) + math.atan(temp_c + humidity)
            - math.atan(humidity - 1.676331) + 0.00391838 * humidity ** 1.5 * math.atan(0.023101 * humidity)
            - 4.686035)


# Source: S2 weewx-EcowittGateway @5cbe533, bin/user/weewx-EcowittGateway.py, lines 795-934: sensor_id, channel_keys, SensorMapper. Unchanged.
def sensor_id(value):
    """A hardware sensor ID in a comparable form: upper case hex, no 0x prefix or leading zeros."""
    text = str(value).strip().upper()
    text = text[2:] if text.startswith('0X') else text
    return text.lstrip('0') or '0'


def channel_keys(model, channel):
    """Prefixes (or whole names) of the fields that belong to channel of a multi-channel model."""
    keys = [f'{model}.ch{channel}.'] + [f'{group}.{channel}.' for group in SENSOR_GROUPS[model][1]]
    if model == 'wh51':
        keys += [f'ch_soil{channel}nowAd', f'ch_ec{channel}nowAd']
    return keys


_CHANNEL_KEY = re.compile(r'(?:(\w+?)\.ch(\d+)\.|(ch_[a-z0-9]+)\.(\d+)\.|(ch_soil|ch_ec)(\d+)nowAd$)')
_SENSOR_ID_KEY = re.compile(r'(\w+?)\.ch(\d+)\.id')


class SensorMapper:
    """Reports multi-channel sensors on fixed channels chosen by hardware ID ([[sensor_map]]).

    Each entry is '<sensor ID> = <channel>'. The sensor's data is reported on that channel whichever
    gateway channel it is paired on. A sensor pushed off that channel takes the channel that was
    freed, so data is never merged or lost.
    """

    def __init__(self, config=None):
        self.targets = {}
        for key, value in (config or {}).items():
            match = re.fullmatch(r'(?:ch)?\s*(\d+)', str(value).strip().lower())
            if match and re.fullmatch(r'(0x)?[0-9a-f]+', str(key).strip().lower()):
                self.targets[sensor_id(key)] = int(match.group(1))
            else:
                log.error("sensor_map: ignoring '%s = %s', expected '<sensor ID> = <channel number>'", key, value)
        self.plan_key = None
        self.perms, self.lines, self.problems = {}, [], []

    def __bool__(self):
        return bool(self.targets)

    @staticmethod
    def paired(data):
        """{model: {gateway channel: sensor ID}} for the registered multi-channel sensors in data."""
        found = {}
        for key, value in data.items():
            match = _SENSOR_ID_KEY.fullmatch(key)
            if match and match.group(1) in SENSOR_GROUPS and value is not None \
                    and str(value).upper() not in _UNREGISTERED_IDS:
                found.setdefault(match.group(1), {})[int(match.group(2))] = str(value)
        return found

    def plan(self, sensors):
        """Work out, and log when it changes, the channel moves {model: {from: to}} for sensors."""
        if not self.targets:
            return {}
        paired = self.paired(sensors)
        key = tuple(sorted((m, c, i) for m, chans in paired.items() for c, i in chans.items()))
        if key == self.plan_key:
            return self.perms
        self.plan_key = key
        where = {sensor_id(i): (m, c) for m, chans in paired.items() for c, i in chans.items()}
        wanted, problems = {}, []
        for sid, target in self.targets.items():
            if sid not in where:
                problems.append(f'sensor {sid} is not paired with the gateway, so it is not mapped')
                continue
            model, channel = where[sid]
            count = SENSOR_GROUPS[model][0]
            if not 1 <= target <= count:
                problems.append(f'{model.upper()} sensor {sid}: channel {target} is outside 1-{count}, not mapped')
            elif target in wanted.get(model, {}).values():
                problems.append(f'{model.upper()} sensor {sid}: channel {target} is already mapped to another '
                                'sensor, not mapped')
            else:
                wanted.setdefault(model, {})[channel] = target
        self.perms, self.lines = {}, []
        for model, moves in wanted.items():
            perm = self._permutation(moves, paired[model], SENSOR_GROUPS[model][0])
            for src, dst in sorted(perm.items()):
                if src != dst and src in paired[model]:
                    why = 'mapped' if src in moves else 'moved to make room'
                    self.lines.append(f'{model.upper()} sensor {paired[model][src]} on gateway channel {src} '
                                      f'is reported as channel {dst} ({why})')
            if any(src != dst for src, dst in perm.items()):
                self.perms[model] = perm
        self.problems = problems
        for problem in problems:
            log.warning('sensor_map: %s', problem)
        for line in self.lines:
            log.info('sensor_map: %s', line)
        if not self.lines and not problems:
            log.info('sensor_map: every mapped sensor is already on its channel')
        return self.perms

    @staticmethod
    def _permutation(moves, occupied, count):
        """Extend moves {from: to} to a one-to-one mapping of every channel 1..count."""
        perm = dict(moves)
        taken = set(moves.values())
        for channel in sorted(occupied):
            if channel not in perm and channel not in taken:
                perm[channel] = channel
                taken.add(channel)
        channels = range(1, count + 1)
        # sensors pushed off their channel go first, preferably to the channels mapped sensors left
        sources = sorted(c for c in occupied if c not in perm) + [c for c in channels
                                                                  if c not in perm and c not in occupied]
        free = [c for c in moves if c not in taken] + [c for c in channels if c not in taken and c not in moves]
        perm.update(zip(sources, free))
        return perm

    def reported_channel(self, model, channel):
        return self.perms.get(model, {}).get(channel, channel)

    def apply(self, data, sensors=None):
        """Return data with multi-channel sensor fields moved to their reported channels.

        sensors is the sensor data holding the IDs, if data itself does not (catchup records).
        """
        if not self.targets:
            return data
        perms = self.plan(data if sensors is None else sensors)
        if not perms:
            return data
        result = {}
        for key, value in data.items():
            match = _CHANNEL_KEY.match(key)
            if match:
                model, mch, group, gch, ad, adch = match.groups()
                owner, channel, fmt = ((model, mch, f'{model}.ch{{}}.') if model else
                                       (_GROUP_MODEL.get(group), gch, f'{group}.{{}}.') if group else
                                       ('wh51', adch, f'{ad}{{}}nowAd'))
                new = perms.get(owner, {}).get(int(channel))
                if new is not None:
                    key = fmt.format(new) + key[match.end():]
                    if group and key.endswith('.channel'):
                        value = new
            result[key] = value
        return result


# ---------------------------------------------------------------------------
# Catalog based record mapping (new in weewx-ecowitt-multi)
# ---------------------------------------------------------------------------

# Source: S2 @5cbe533, EcowittCommon.process_live_data and EcowittHttpDriver.gen_ecowitt_archive_records.
# Adapted: counters are handled by cumulative.py; derived values only fill fields the mapping left empty;
#          output unit system is always METRICWX.
def derive_fields(data, record):
    """Add the values upstream derives from the live data (sensor versions, WS85/WS90 battery, WN51/WH51 EC
    soil moisture, a WBGT estimate and VPD) to record, without overwriting mapped values."""
    for key in ('ws85', 'ws90'):
        if f'{key}.version' in data:
            record.setdefault(f'{key}_ver', data[f'{key}.version'])
    for ch in range(1, 17):
        for src, dest in (('humidity', 'soilMoist'), ('voltage', 'soilMoistBatt')):
            if data.get(f'ch_ec.{ch}.{src}') is not None and record.get(f'{dest}{ch}') is None:
                record[f'{dest}{ch}'] = data[f'ch_ec.{ch}.{src}']
    if 'piezoRain.0x13.voltage' in data:
        for key in ('ws85', 'ws90'):
            if f'piezoRain.0x13.{key}_ver' in data or f'{key}.version' in data:
                record.setdefault(f'{key}_batt', data['piezoRain.0x13.voltage'])
                break
    temp, hum = data.get('common_list.0x02.val'), data.get('common_list.0x07.val')
    if temp is None or hum is None:
        return record
    try:
        temp, hum = float(temp), float(hum)
        if data.get('common_list.0xA2.val') is None and record.get('wbgt') is None:
            record['wbgt'] = calc_twb(temp, hum)
        if data.get('common_list.5.val') is None and record.get('vpd') is None:
            record['vpd'] = round(0.61094 * math.exp(17.625 * temp / (temp + 243.04)) * (1 - hum / 100), 2) * 10
    except (TypeError, ValueError, ZeroDivisionError, OverflowError):
        pass
    return record


class CatalogMapper:
    """Maps canonical source data to WeeWX fields using the sensor catalog."""

    def __init__(self, catalog, rain_source='tipping', field_map_extensions=None, unknown_fields='pass'):
        self.catalog = catalog
        self.field_map = catalog.live_map_for(rain_source)
        for weewx_name, key in (field_map_extensions or {}).items():
            self.field_map[weewx_name] = key
        self.unknown_fields = unknown_fields
        self.logged_unknown = set()

    def map(self, data):
        record = {w: data[k] for w, k in self.field_map.items() if data.get(k) is not None}
        derive_fields(data, record)
        unknown = self.catalog.unknown_keys(data)
        mapped_keys = set(self.field_map.values())
        unknown = {k: v for k, v in unknown.items() if k not in mapped_keys}
        new = set(unknown) - self.logged_unknown
        if new and self.unknown_fields in ('pass', 'log'):
            log.info('fields not in the sensor catalog: %s (see --unknown-fields)', ', '.join(sorted(new)))
        self.logged_unknown |= new
        if self.unknown_fields == 'pass':
            for key, value in unknown.items():
                record.setdefault(self.catalog.unknown_field_name(key), value)
        return record

    @staticmethod
    def counter_values(data, counters, chosen):
        """{counter field: raw total} using, per counter, the first candidate key found (remembered in chosen)."""
        result = {}
        for field, candidates in counters.items():
            key = chosen.get(field)
            if key is None:
                key = next((c for c in candidates if data.get(c) is not None), None)
                if key is None:
                    continue
                chosen[field] = key
                log.info("using '%s' for the %s total", key, field)
            if data.get(key) is not None:
                result[field] = data[key]
        return result
