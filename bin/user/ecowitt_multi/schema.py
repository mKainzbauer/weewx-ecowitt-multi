# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 mKainzbauer
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version. See the LICENSE file or https://www.gnu.org/licenses/.
"""Database schema support.

schema_mode = weewx    the WeeWX schema (wview_extended); fields without a column are not stored.
schema_mode = ecowitt  wview_extended plus a column for every numeric catalog field
                       ("Ecowitt schema", versioned with the catalog's schema_version).

EcowittSchemaService (optional, a data service) adds missing columns before StdArchive
stores a record (live and catchup records) if auto_add_columns is enabled.
"""

import logging

import weewx
import weewx.engine

from .catalog import load_catalog, version_tuple
from .util import to_bool

log = logging.getLogger(__name__)

SCHEMA_META_KEY = 'ecowitt_schema_version'


def _wview_extended():
    try:
        import weewx.schemas.wview_extended as wve  # WeeWX 5
    except ImportError:  # WeeWX 4
        import schemas.wview_extended as wve
    return wve


def ecowitt_schema(catalog=None, max_schema=None):
    """A WeeWX schema dict (table, day_summaries) = wview_extended + numeric catalog fields."""
    catalog = catalog or load_catalog()
    table = list(_wview_extended().table)
    known = {name for name, _ in table}
    for name, sql in sorted(catalog.sql_columns(max_schema).items()):
        if name not in known:
            table.append((name, sql))
            known.add(name)
    day = [(name, 'scalar') for name, _ in table if name not in ('dateTime', 'usUnits', 'interval')]
    return {'table': table, 'day_summaries': day + [('wind', 'vector')]}


def __getattr__(name):
    # [DataBindings] schema = user.ecowitt_multi.schema.schema
    if name == 'schema':
        return ecowitt_schema()
    raise AttributeError(name)


def missing_columns(manager, fields, catalog=None):
    """{field: SQL type} of fields that have no column in the archive of manager."""
    catalog = catalog or load_catalog()
    sqlkeys = set(manager.sqlkeys)
    result = {}
    for field in fields:
        if field in sqlkeys or field in ('dateTime', 'usUnits', 'interval'):
            continue
        entry = catalog.by_weewx.get(field)
        if entry is not None and 'sql' in entry:
            result[field] = entry['sql']
        elif entry is None and field.startswith('ecowitt_'):
            result[field] = 'REAL'
    return result


def add_columns(manager, columns):
    """Add columns (and, with a day summary manager, their day summaries)."""
    for name, sql in sorted(columns.items()):
        log.info("adding column '%s' (%s) to %s", name, sql, manager.table_name)
        manager.add_column(name, sql)
    if columns:
        manager.sqlkeys = manager.connection.columnsOf(manager.table_name)
        if hasattr(manager, '_create_sync'):
            manager._create_sync()

def schema_version(manager):
    try:
        return manager._read_metadata(SCHEMA_META_KEY)
    except Exception:  # noqa: BLE001
        return None


def write_schema_version(manager, version):
    try:
        manager._write_metadata(SCHEMA_META_KEY, version)
    except Exception as e:  # noqa: BLE001
        log.debug('cannot write schema version: %s', e)


class EcowittSchemaService(weewx.engine.StdService):
    """Adds missing columns for catalog (and unknown ecowitt_*) fields before records are stored."""

    def __init__(self, engine, config_dict):
        super().__init__(engine, config_dict)
        cfg = config_dict.get('EcowittMulti', {})
        self.enabled = to_bool(cfg.get('auto_add_columns', False))
        self.add_unknown = to_bool(cfg.get('auto_add_unknown_columns', False))
        self.binding = config_dict.get('StdArchive', {}).get('data_binding', 'wx_binding')
        self.catalog = load_catalog(cfg.get('catalog_file'))
        self.checked = set()
        if self.enabled:
            self.bind(weewx.NEW_ARCHIVE_RECORD, self.new_archive_record)
            log.info('EcowittSchemaService: adding missing columns automatically')

    def new_archive_record(self, event):
        new = set(event.record) - self.checked
        if not new:
            return
        self.checked |= new
        manager = self.engine.db_binder.get_manager(self.binding)
        columns = missing_columns(manager, new, self.catalog)
        if not self.add_unknown:
            columns = {k: v for k, v in columns.items() if not k.startswith('ecowitt_')}
        if columns:
            add_columns(manager, columns)
            current = schema_version(manager)
            if current is None or version_tuple(current) < version_tuple(self.catalog.schema_version):
                write_schema_version(manager, self.catalog.schema_version)
