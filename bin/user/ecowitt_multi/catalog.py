# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 mKainzbauer
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version. See the LICENSE file or https://www.gnu.org/licenses/.
"""The versioned Ecowitt sensor catalog (ecowitt_catalog.json).

The catalog is the single place where new Ecowitt sensors are added: it maps the
driver's canonical (flattened HTTP API) keys to WeeWX fields and holds the unit group,
aggregation, SQL type, SD card column, Ecowitt.net field, Ecowitt protocol key and
ecowitt2mqtt key of each value. See CONTRIBUTING.md.
"""

import json
import logging
import os
import re

import weewx.units

log = logging.getLogger(__name__)

DEFAULT_CATALOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ecowitt_catalog.json')
AGGREGATIONS = ('avg', 'sum', 'max', 'min', 'last', 'vecdir')
SQL_TYPES = ('REAL', 'INTEGER')
_SEMVER = re.compile(r'^\d+\.\d+\.\d+$')

# Plausibility limits per unit group in METRICWX units (plan: "validation").
DEFAULT_LIMITS = {
    'group_temperature': (-60.0, 80.0), 'group_percent': (0.0, 100.0), 'group_pressure': (800.0, 1100.0),
    'group_speed': (0.0, 90.0), 'group_speed2': (0.0, 90.0), 'group_direction': (0.0, 360.0),
    'group_radiation': (0.0, 2000.0), 'group_uv': (0.0, 20.0), 'group_rainrate': (0.0, 2000.0),
    'group_concentration': (0.0, 1000.0), 'group_fraction': (0.0, 10000.0), 'group_distance': (0.0, 50.0),
}

# flattened keys ending in one of these are metadata, not observations
_META_SUFFIXES = ('.id', '.address', '.name', '.enabled', '.channel', '.version', '.date', '.unit', '.img',
                  '.idst', '.type', 'datetime', '.battery_proz_raw')


class CatalogError(Exception):
    pass


def version_tuple(text):
    return tuple(int(p) for p in str(text).split('.'))


class Catalog:
    """Loaded and validated sensor catalog."""

    def __init__(self, path=None, data=None):
        self.path = path or DEFAULT_CATALOG
        if data is None:
            try:
                with open(self.path, encoding='utf-8') as f:
                    data = json.load(f)
            except (OSError, ValueError) as e:
                raise CatalogError(f'cannot read sensor catalog {self.path}: {e}') from e
        errors = self.validate(data)
        if errors:
            raise CatalogError(f'invalid sensor catalog {self.path}: ' + '; '.join(errors[:10]))
        self.data = data
        self.version = data['catalog_version']
        self.schema_version = data['schema_version']
        self.groups = data.get('groups', {})
        self.fields = data['fields']
        self.by_weewx = {f['weewx']: f for f in self.fields}
        self.live_map = {f['weewx']: f['key'] for f in self.fields if 'key' in f}
        self.counters = {f['weewx']: list(f['counter']) for f in self.fields if 'counter' in f}
        self.aliases = {f['weewx']: f['alias_of'] for f in self.fields if 'alias_of' in f}
        self.agg = {f['weewx']: f.get('agg', 'avg') for f in self.fields}
        self.sd_map = data.get('sd_map', {})
        self.cloud_map = data.get('cloud_map', {})
        self.protocol_map = data.get('protocol_map', {})
        self.ecowitt2mqtt_map = data.get('ecowitt2mqtt_map', {})
        self.known_keys = set(self.live_map.values()) | set(self.groups) | set(self.sd_map) \
            | {k for keys in self.counters.values() for k in keys}

    # -- validation ------------------------------------------------------------

    @staticmethod
    def validate(data):
        """A list of problems of a decoded catalog (empty if it is valid)."""
        errors = []
        if not isinstance(data, dict):
            return ['catalog is not a JSON object']
        for key in ('catalog_version', 'schema_version'):
            if not _SEMVER.match(str(data.get(key, ''))):
                errors.append(f"'{key}' must be a semantic version (n.n.n)")
        fields = data.get('fields')
        if not isinstance(fields, list) or not fields:
            return errors + ["'fields' must be a non-empty list"]
        seen = set()
        names = {f.get('weewx') for f in fields if isinstance(f, dict)}
        for i, f in enumerate(fields):
            if not isinstance(f, dict) or not f.get('weewx'):
                errors.append(f'field #{i} has no WeeWX name')
                continue
            name = f['weewx']
            if name in seen:
                errors.append(f"duplicate WeeWX field '{name}'")
            seen.add(name)
            kinds = [k for k in ('key', 'counter', 'alias_of') if k in f]
            if len(kinds) != 1:
                errors.append(f"'{name}' needs exactly one of key, counter, alias_of")
            if 'alias_of' in f and f['alias_of'] not in names:
                errors.append(f"'{name}' is an alias of unknown field '{f['alias_of']}'")
            if 'counter' in f and (not isinstance(f['counter'], list) or not f['counter']):
                errors.append(f"'{name}': counter must be a non-empty list of keys")
            if f.get('agg', 'avg') not in AGGREGATIONS:
                errors.append(f"'{name}': unknown aggregation '{f.get('agg')}'")
            if 'sql' in f and f['sql'] not in SQL_TYPES:
                errors.append(f"'{name}': unknown SQL type '{f['sql']}'")
            if not str(f.get('group', '')).startswith('group_'):
                errors.append(f"'{name}': missing unit group")
            if 'min_schema_version' in f and not _SEMVER.match(str(f['min_schema_version'])):
                errors.append(f"'{name}': min_schema_version must be n.n.n")
            if 'limits' in f and (not isinstance(f['limits'], list) or len(f['limits']) != 2):
                errors.append(f"'{name}': limits must be [min, max]")
        if 'schema_version' in data and _SEMVER.match(str(data['schema_version'])):
            top = version_tuple(data['schema_version'])
            for f in fields:
                msv = f.get('min_schema_version') if isinstance(f, dict) else None
                if msv and _SEMVER.match(str(msv)) and version_tuple(msv) > top:
                    errors.append(f"'{f['weewx']}' needs schema {msv} > catalog schema {data['schema_version']}")
        for name in ('protocol_map', 'ecowitt2mqtt_map'):
            for key, value in data.get(name, {}).items():
                if not isinstance(value, list) or len(value) < 3:
                    errors.append(f"{name}['{key}'] must be [canonical key, unit, ..., group]")
        return errors

    # -- lookups ---------------------------------------------------------------

    def group(self, weewx_name):
        entry = self.by_weewx.get(weewx_name)
        return entry.get('group') if entry else weewx.units.obs_group_dict.get(weewx_name)

    def limits(self, weewx_name):
        entry = self.by_weewx.get(weewx_name, {})
        if 'limits' in entry:
            return tuple(entry['limits'])
        return DEFAULT_LIMITS.get(self.group(weewx_name))

    def sql_columns(self, max_schema=None):
        """{WeeWX field: SQL type} of the numeric catalog fields (optionally up to a schema version)."""
        result = {}
        for f in self.fields:
            if 'sql' not in f:
                continue
            if max_schema and version_tuple(f.get('min_schema_version', '1.0.0')) > version_tuple(max_schema):
                continue
            result[f['weewx']] = f['sql']
        return result

    def live_map_for(self, rain_source='tipping'):
        """{WeeWX field: canonical key}; 'rainRate' from the piezo gauge if rain_source is piezo."""
        result = dict(self.live_map)
        if rain_source == 'piezo':
            result['rainRate'] = 'piezoRain.0x0E.val'
        return result

    def aliases_for(self, rain_source='tipping'):
        result = dict(self.aliases)
        if rain_source == 'piezo':
            result['rain'] = 'p_rain'
        return result

    def register_obs_groups(self):
        """Make WeeWX aware of the unit group of every catalog field it does not know yet."""
        for f in self.fields:
            weewx.units.obs_group_dict.setdefault(f['weewx'], f['group'])

    # -- unknown fields -----------------------------------------------------------

    def unknown_keys(self, data):
        """Numeric canonical keys in data that the catalog does not know."""
        result = {}
        for key, value in data.items():
            if key in self.known_keys or key.endswith(_META_SUFFIXES) or isinstance(value, bool):
                continue
            if isinstance(value, (int, float)):
                result[key] = value
        return result

    @staticmethod
    def unknown_field_name(key):
        return 'ecowitt_' + re.sub(r'[^0-9A-Za-z]+', '_', key).strip('_')

    def draft_entries(self, keys):
        """Draft catalog entries for unknown keys (for a pull request, see CONTRIBUTING.md)."""
        return [{'weewx': self.unknown_field_name(k), 'key': k, 'group': 'group_count', 'agg': 'avg', 'sql': 'REAL',
                 'min_schema_version': self.schema_version, 'origin': 'user'} for k in sorted(keys)]


_CACHE = {}


def load_catalog(path=None):
    """Load (once per path) the catalog; an explicit catalog_file overrides the bundled one."""
    path = path or DEFAULT_CATALOG
    if path not in _CACHE:
        _CACHE[path] = Catalog(path)
        log.info('sensor catalog %s version %s (schema %s)', path, _CACHE[path].version,
                 _CACHE[path].schema_version)
    return _CACHE[path]
