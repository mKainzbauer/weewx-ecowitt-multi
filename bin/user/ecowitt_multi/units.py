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
# Modified by mKainzbauer, 2026-11: module level side effects moved into the
# idempotent function register_units(); additional obs groups of WeeWX fields
# come from the sensor catalog (see catalog.py).
"""Additional WeeWX unit groups, units, formats and conversions used by Ecowitt devices."""

import weewx.units

from .util import rng as _rng

_REGISTERED = False

# Source: S2 weewx-EcowittGateway @5cbe533, bin/user/weewx-EcowittGateway.py, module level unit
#         configuration, _OBS_GROUPS, _set_conv() and define_units(). Adapted: executed from
#         register_units() instead of at import time.
_U = weewx.units
for _std in (_U.USUnits, _U.MetricUnits, _U.MetricWXUnits):
    _std['group_dbm'] = 'dBm'
    _std['group_string'] = 'string'
_U.default_unit_format_dict.update({'dBm': '%.0f', 'hPa': '%.2f', 'string': '%s'})
_U.default_unit_label_dict.update({'dBm': ' dBm', 'string': ''})

_OBS_GROUPS = {
    'group_percent': _rng('leafWet{}', 8) + _rng('soilMoist{}', 16) + _rng('signal{}', 8) + ['consolebattp'],
    'group_count': _rng('soilad{}', 16) + _rng('soilECad{}', 16) + _rng('ldsheat_ch{}', 4) + [
        'charge_stat', 'ws6210_batt', 'wh68_batt', 'wh69_batt', 'wqt01_batt', 'wn64_batt', 'srain_piezo',
        'srain', 'ws90_ver', 'ws85_ver', 'rain_annual_reset', 'rain_day_reset', 'rain_week_reset',
        'rain_source', 'piezo', 'rain_batt', 'piezorain_batt', 'lightning_strike_count',
        'lightning_noise_count'],
    'group_temperature': _rng('soilmTemp{}', 16),
    'group_usiecm': _rng('soilEC{}', 16),
    'group_concentration': ['pm1_24h_co2', 'pm4_24h_co2', 'pm25_24h_co2', 'pm10_24h_co2',
                            *_rng('pm25_avg_24h_ch{}', 4)],
    'group_rainrate': ['rainrate', 'rrain_piezo', 'p_rainrate'],
    'group_rain': ['eventRain', 'hourRain', 'rain24', 'weekRain', 'totalRain', 'erain_piezo', 'hrain_piezo',
                   'drain_piezo', 'wrain_piezo', 'mrain_piezo', 'yrain_piezo', 'rain_piezo', 'rain24_piezo',
                   'train_piezo', 't_rain', 'p_rain', 't_rainyear', 'p_rainyear'],
    'group_volt': ['rainBatteryStatus', 'hailBatteryStatus', 'windBatteryStatus', 'ws68_batt', 'ws80_batt',
                   'ws85_batt', 'ws90_batt', 'wqt01batt', 'wn64batt', 'ws1900batt', 'console_batt', 'consoleext_batt',
                   'ws90cap_volt', 'ws85cap_volt'] + _rng('ldsbatt{}', 4),
    'group_speed2': ['maxdailygust'],
    'group_distance': ['lightning_dist', 'lightning_distance'],
    'group_time': ['lightning_disturber_count'],
    'group_deltatime': ['runtime'],
    'group_data': ['heap', 'pb'],
    'group_direction': ['windDir10'],
    'group_string': ['apName', 'stationtype'],
}
for _grp, _fields in _OBS_GROUPS.items():
    for _f in _fields:
        _U.obs_group_dict[_f] = _grp


def _set_conv(src, targets, overwrite=False):
    """Add unit conversion functions, optionally keeping existing ones."""
    conv = _U.conversionDict.setdefault(src, {})
    for dest, fn in targets.items():
        if overwrite or dest not in conv:
            conv[dest] = fn


def define_units():
    """Define the additional unit groups, units, formats and conversions used."""
    groups = {'group_depth': ('foot2', 'mm2'), 'group_pressurevpd': ('inHg', 'hPa'),
              'group_usiecm': ('micro_siemens_per_centimeter',) * 2,
              'group_organicpollution': ('pounds_per_gallon', 'milligram_per_liter'),
              'group_ntu': ('nephelometric_turbidity_unit',) * 2,
              'group_deltat': ('degree_F2', 'degree_C2')}
    for group, (us, metric) in groups.items():
        _U.USUnits[group] = us
        _U.MetricUnits[group] = _U.MetricWXUnits[group] = metric
    _U.obs_group_dict['vpd'] = 'group_pressurevpd'
    _U.obs_group_dict['micro_siemens_per_centimeter'] = 'group_usiecm'
    _U.obs_group_dict['nephelometric_turbidity_unit'] = 'group_ntu'
    for unit, fmt, label in (('micro_siemens_per_centimeter', '%.0f', ' µS/cm'), ('foot2', '%.2f', ' ft'),
                             ('mm2', '%.0f', ' mm'), ('milligram_per_liter', '%.1f', ' mg/L'),
                             ('pounds_per_gallon', '%.9f', ' lb/gal'), ('nephelometric_turbidity_unit', '%.0f', 'NTU'),
                             ('degree_C2', '%.1f', '°C'), ('degree_E2', '%.1f', '°C'), ('degree_F2', '%.1f', '°F'),
                             ('degree_K2', '%.1f', '°C')):
        _U.default_unit_format_dict[unit] = fmt
        _U.default_unit_label_dict[unit] = label
    _U.default_unit_format_dict['microgram_per_meter_cubed'] = '%.1f'
    for unit, fmt, label in (('byte', '%.d', ' B'), ('kilobyte', '%.3f', ' kB'), ('megabyte', '%.3f', ' MB')):
        _U.default_unit_format_dict[unit] = _U.default_unit_format_dict.get(unit) or fmt
        _U.default_unit_label_dict[unit] = _U.default_unit_label_dict.get(unit) or label
    # conversions for units only this driver defines
    for src, targets in {
            'milligram_per_liter': {'pounds_per_gallon': lambda x: x * 8.345e-06},
            'pounds_per_gallon': {'milligram_per_liter': lambda x: x / 8.345e-06},
            'mm2': {'inch2': lambda x: x / 25.4, 'foot2': lambda x: x / 304.8, 'cm2': lambda x: x / 10.0,
                    'meter2': lambda x: x / 1000.0},
            'cm2': {'inch2': lambda x: x / 2.54, 'foot2': lambda x: x / 30.48, 'mm2': lambda x: x * 10.0,
                    'meter2': lambda x: x / 100.0},
            'm2': {'inch2': lambda x: x / 0.0254, 'foot2': lambda x: x / 0.3048, 'mm2': lambda x: x * 1000.0,
                   'cm2': lambda x: x * 100.0},
            'inch2': {'foot2': lambda x: x / 12.0, 'mm2': lambda x: x * 25.4, 'cm2': lambda x: x * 2.54,
                      'meter2': lambda x: x * 0.0254},
            'foot2': {'inch2': lambda x: x * 12.0, 'mm2': lambda x: x * 304.8, 'cm2': lambda x: x * 30.48,
                      'meter2': lambda x: x * 0.3048},
            'degree_C2': {'degree_F2': lambda x: x * 9 / 5, 'degree_E2': lambda x: x * 7 / 5, 'degree_K2': lambda x: x},
            'degree_E2': {'degree_C2': lambda x: x * 5 / 7, 'degree_F2': lambda x: x * 9 / 7,
                          'degree_K2': lambda x: x * 5 / 7},
            'degree_F2': {'degree_C2': lambda x: x * 5 / 9, 'degree_E2': lambda x: x * 7 / 9,
                          'degree_K2': lambda x: x * 5 / 9},
            'degree_K2': {'degree_C2': lambda x: x, 'degree_E2': lambda x: x * 7 / 5, 'degree_F2': lambda x: x * 9 / 5},
    }.items():
        _set_conv(src, targets, overwrite=True)
    # conversions that WeeWX may already provide
    for src, targets in {
            'nautical_mile': {'meter': lambda x: x * 1852.0, 'km': lambda x: x * 1.852, 'mile': lambda x: x * 1.151},
            'meter': {'nautical_mile': lambda x: x / 1852.0},
            'km': {'nautical_mile': lambda x: x / 1.852},
            'mile': {'nautical_mile': lambda x: x / 1.151},
            'knot': {'meter_per_second': lambda x: x * 0.514444, 'km_per_hour': lambda x: x * 1.852,
                     'mile_per_hour': lambda x: x * 1.151},
            'meter_per_second': {'knot': lambda x: x / 0.514444},
            'km_per_hour': {'knot': lambda x: x / 1.852},
            'mile_per_hour': {'knot': lambda x: x / 1.151},
            'byte': {'bit': lambda x: x * 8, 'kilobyte': lambda x: x / 1024.0, 'megabyte': lambda x: x / 1024.0 ** 2},
            'kilobyte': {'bit': lambda x: x * 8192, 'byte': lambda x: x * 1024, 'megabyte': lambda x: x / 1024.0},
            'megabyte': {'bit': lambda x: x * 8 * 1024 ** 2, 'byte': lambda x: x * 1024 ** 2,
                         'kilobyte': lambda x: x * 1024},
            'klux': {'lux': lambda x: x * 1000, 'watt_per_meter_squared': lambda x: x * 1000 / 126.7,
                     'kfc': lambda x: x * 10.76 / 0.1267 ** 2},
            'lux': {'klux': lambda x: x / 1000.0, 'watt_per_meter_squared': lambda x: x / 126.7,
                    'kfc': lambda x: x * 0.01076 / 0.1267 ** 2},
            'watt_per_meter_squared': {'klux': lambda x: x * 0.1267, 'lux': lambda x: x * 126.7,
                                       'kfc': lambda x: x * 10.76 / 0.1267},
            'kfc': {'klux': lambda x: x * 0.1267 ** 2 / 10.76, 'lux': lambda x: x * 126.7 ** 2 / 10.76,
                    'watt_per_meter_squared': lambda x: x * 0.1267 / 10.76},
    }.items():
        _set_conv(src, targets)


def register_units():
    """Define the additional units once (safe to call repeatedly)."""
    global _REGISTERED
    if not _REGISTERED:
        define_units()
        _REGISTERED = True


def convert_value(value, from_unit, group, unit_system):
    """Convert value given in from_unit (unit group 'group') to the target unit system."""
    if value is None or from_unit is None:
        return value
    vt = weewx.units.ValueTuple(value, from_unit, group)
    return weewx.units.convertStd(vt, unit_system).value
