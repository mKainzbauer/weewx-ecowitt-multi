# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 mKainzbauer
import copy
import json
import os
import subprocess
import sys

import pytest

from user.ecowitt_multi.catalog import DEFAULT_CATALOG, Catalog, CatalogError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_bundled_catalog_is_valid(catalog):
    assert catalog.version and catalog.schema_version
    assert catalog.live_map['outTemp'] == 'common_list.0x02.val'
    assert catalog.counters['t_rain'] == ['rain.0x13.val', 'rain.0x12.val']
    assert catalog.aliases['rain'] == 't_rain'
    assert catalog.aliases_for('piezo')['rain'] == 'p_rain'
    assert catalog.live_map_for('piezo')['rainRate'] == 'piezoRain.0x0E.val'
    assert catalog.agg['t_rain'] == 'sum'


def test_limits(catalog):
    assert catalog.limits('outTemp') == (-60.0, 80.0)
    assert catalog.limits('outHumidity') == (0.0, 100.0)


@pytest.mark.parametrize('mutate, message', [
    (lambda d: d['fields'].append(dict(d['fields'][0])), 'duplicate'),
    (lambda d: d['fields'][0].update(agg='median'), 'aggregation'),
    (lambda d: d['fields'].append({'weewx': 'x', 'alias_of': 'nope', 'group': 'group_count'}), 'unknown field'),
    (lambda d: d.update(catalog_version='1.0'), 'semantic'),
    (lambda d: d['fields'][0].update(min_schema_version='9.0.0'), 'needs schema'),
])
def test_invalid_catalog(mutate, message):
    with open(DEFAULT_CATALOG, encoding='utf-8') as f:
        data = json.load(f)
    data = copy.deepcopy(data)
    mutate(data)
    with pytest.raises(CatalogError, match=message):
        Catalog(data=data)


def test_unknown_keys(catalog):
    data = {'common_list.0x02.val': 1.0, 'newsensor.1.val': 4.2, 'ws90.version': 140, 'foo.name': 'x'}
    assert catalog.unknown_keys(data) == {'newsensor.1.val': 4.2}
    assert catalog.unknown_field_name('newsensor.1.val') == 'ecowitt_newsensor_1_val'
    draft = catalog.draft_entries(['newsensor.1.val'])
    assert draft[0]['key'] == 'newsensor.1.val' and draft[0]['min_schema_version'] == catalog.schema_version


def test_sql_columns_respect_schema_version(catalog):
    cols = catalog.sql_columns()
    assert 'outTemp' in cols and 'rain' in cols
    assert catalog.sql_columns('0.0.1') == {}


def test_catalog_is_up_to_date():
    """The catalog JSON matches what tools/build_catalog.py generates."""
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path))
    result = subprocess.run([sys.executable, os.path.join(ROOT, 'tools', 'build_catalog.py'), '--check'],
                            capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stdout + result.stderr
