# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 mKainzbauer
from user.ecowitt_multi.cumulative import CumulativeMerger
from user.ecowitt_multi.merge import PriorityMerger


def limits(field):
    return {'outTemp': (-60, 80)}.get(field)


def test_fallback_to_second_source():
    """Plan example: source 1 has no outdoor temperature, source 2 has one."""
    m = PriorityMerger(['gw1', 'gw2'], limits=limits)
    m.update('gw1', 100, {'inTemp': 21.0})
    m.update('gw2', 100, {'inTemp': 22.0, 'outTemp': 5.0})
    record, prov = m.merge(100)
    assert record == {'inTemp': 21.0, 'outTemp': 5.0}
    assert prov == {'inTemp': 'gw1', 'outTemp': 'gw2'}


def test_field_priority_and_prefix():
    m = PriorityMerger(['gw1', 'gw2'], {'outTemp': ['gw2'], 'soil*': ['gw2', 'gw1']})
    m.update('gw1', 100, {'outTemp': 1.0, 'soilMoist1': 10, 'inTemp': 20})
    m.update('gw2', 100, {'outTemp': 2.0, 'soilMoist1': 20, 'inTemp': 21})
    record, _ = m.merge(100)
    assert record == {'outTemp': 2.0, 'soilMoist1': 20, 'inTemp': 20}
    assert m.priority('outTemp') == ['gw2', 'gw1']


def test_stale_cache_is_ignored():
    m = PriorityMerger(['gw1', 'gw2'], max_age=300)
    m.update('gw1', 0, {'outTemp': 1.0})
    m.update('gw2', 250, {'outTemp': 2.0})
    assert m.merge(200)[0]['outTemp'] == 1.0
    assert m.merge(400)[0]['outTemp'] == 2.0
    assert m.fresh_sources(400) == ['gw2']


def test_implausible_values_fall_back():
    m = PriorityMerger(['gw1', 'gw2'], limits=limits)
    m.update('gw1', 100, {'outTemp': 150.0})
    m.update('gw2', 100, {'outTemp': 3.0})
    assert m.merge(100)[0]['outTemp'] == 3.0


def prio(field):
    return ['gw1', 'gw2']


def test_counter_single_source_and_reset():
    c = CumulativeMerger(prio)
    assert c.update('gw1', 't_rain', 10.0, 0) is None
    assert c.update('gw1', 't_rain', 10.5, 20) == 0.5
    assert c.update('gw1', 't_rain', 0.2, 40) == 0.2  # yearly reset


def test_counters_of_two_sources_are_not_double_counted():
    c = CumulativeMerger(prio, stale_after=300)
    c.update('gw1', 't_rain', 100.0, 0)
    c.update('gw2', 't_rain', 50.0, 1)
    total = 0
    for ts, r1, r2 in ((20, 101.0, 51.0), (40, 102.0, 52.0)):
        for src, raw, t in (('gw1', r1, ts), ('gw2', r2, ts + 1)):
            total += c.update(src, 't_rain', raw, t) or 0
    assert total == 2.0
    assert c.provider['t_rain'] == 'gw1'


def test_counter_handover_when_provider_goes_offline():
    c = CumulativeMerger(prio, stale_after=60)
    c.update('gw1', 't_rain', 100.0, 0)
    assert c.update('gw1', 't_rain', 101.0, 20) == 1.0
    c.update('gw2', 't_rain', 50.0, 30)
    assert c.update('gw2', 't_rain', 50.5, 50) is None      # gw1 still current
    assert c.update('gw2', 't_rain', 51.5, 100) == 1.0      # gw1 stale -> gw2 takes over
    assert c.provider['t_rain'] == 'gw2'
    assert c.update('gw1', 't_rain', 104.0, 120) is None    # overlaps, re-baseline only
    assert c.update('gw1', 't_rain', 104.5, 140) == 0.5     # better source is back
    assert c.provider['t_rain'] == 'gw1'
