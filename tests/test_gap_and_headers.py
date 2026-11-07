# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 mKainzbauer
import os
import re

from user.ecowitt_multi.gap import EMIT, RESTART, SUPPRESS, GapMonitor
from user.ecowitt_multi import gap as gap_module

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_gap_state_machine():
    gap_module._STATE['last_restart'] = None
    g = GapMonitor(archive_interval=300, max_gap_wait=600)
    assert g.update(0, {'a', 'b'}, {'a', 'b'}) == EMIT
    assert g.update(200, set(), {'a', 'b'}) == EMIT          # short dropout
    assert g.update(400, set(), {'a', 'b'}) == SUPPRESS      # outage
    assert g.update(500, {'a'}, {'a', 'b'}) == SUPPRESS      # waiting for b
    assert g.update(520, {'a', 'b'}, {'a', 'b'}) == RESTART
    # a second outage soon after is not restarted again (min_restart_interval)
    g.update(600, {'a', 'b'}, {'a', 'b'})
    g.update(1000, set(), {'a', 'b'})
    assert g.update(1100, {'a', 'b'}, {'a', 'b'}) == EMIT


def test_gap_max_wait():
    gap_module._STATE['last_restart'] = None
    g = GapMonitor(archive_interval=300, max_gap_wait=600)
    g.update(0, {'a', 'b'}, {'a', 'b'})
    g.update(400, set(), {'a', 'b'})
    assert g.update(500, {'a'}, {'a', 'b'}) == SUPPRESS
    assert g.update(1100, {'a'}, {'a', 'b'}) == RESTART


def test_gap_mode_none():
    g = GapMonitor(archive_interval=300, mode='none')
    g.update(0, {'a'}, {'a'})
    g.update(400, set(), {'a'})
    assert g.update(500, {'a'}, {'a'}) == EMIT


def test_spdx_headers():
    """Every Python file carries an SPDX license identifier and a copyright line."""
    missing = []
    for top in ('bin', 'tests', 'tools'):
        for dirpath, _, files in os.walk(os.path.join(ROOT, top)):
            for name in files:
                if name.endswith('.py'):
                    path = os.path.join(dirpath, name)
                    with open(path, encoding='utf-8') as f:
                        head = f.read(600)
                    if os.path.getsize(path) and not (re.search(r'SPDX-License-Identifier: GPL-3\.0-or-later', head)
                                                      and 'Copyright' in head):
                        missing.append(os.path.relpath(path, ROOT))
    with open(os.path.join(ROOT, 'install.py'), encoding='utf-8') as f:
        if 'SPDX-License-Identifier' not in f.read(300):
            missing.append('install.py')
    assert not missing, missing


def test_s2_derived_files_name_their_source():
    pkg = os.path.join(ROOT, 'bin', 'user', 'ecowitt_multi')
    for rel in ('mapping.py', 'sources/parser.py', 'sources/http_api.py', 'history/sd.py', 'history/cloud.py'):
        with open(os.path.join(pkg, rel), encoding='utf-8') as f:
            text = f.read()
        assert 'Ian Millard' in text and 'Gary Roderick' in text and '# Source: S2' in text, rel
