# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 mKainzbauer
import weewx.manager

from user.ecowitt_multi.schema import add_columns, ecowitt_schema, missing_columns, schema_version, \
    write_schema_version


def test_ecowitt_schema_extends_wview_extended(catalog):
    schema = ecowitt_schema(catalog)
    names = [n for n, _ in schema['table']]
    assert names[:3] == ['dateTime', 'usUnits', 'interval']
    assert len(names) == len(set(names))
    assert {'outTemp', 't_rain', 'p_rain', 'lightning_strike_count'} <= set(names)
    assert ('wind', 'vector') in schema['day_summaries']


def test_add_missing_columns(tmp_path, catalog):
    db = {'driver': 'weedb.sqlite', 'database_name': str(tmp_path / 'test.sdb')}
    import weewx.schemas.wview_extended as wve
    with weewx.manager.DaySummaryManager.open_with_create(db, schema=wve.schema) as mgr:
        missing = missing_columns(mgr, ['outTemp', 't_rain', 'p_rain', 'ecowitt_new_1', 'nonsense'], catalog)
        assert missing == {'t_rain': 'REAL', 'p_rain': 'REAL', 'ecowitt_new_1': 'REAL'}
        add_columns(mgr, missing)
        assert missing_columns(mgr, missing, catalog) == {}
        assert 't_rain' in mgr.daykeys
        write_schema_version(mgr, catalog.schema_version)
        assert schema_version(mgr) == catalog.schema_version
