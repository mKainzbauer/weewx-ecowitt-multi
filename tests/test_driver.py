# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 mKainzbauer
"""Driver tests with synthetic canonical data (keys as produced by the HTTP API parser)."""

import pytest
import weewx

from conftest import FakeSource
from user.ecowitt_multi import EcowittMultiDriver
from user.ecowitt_multi import gap as gap_module

T, H, IT, YR = 'common_list.0x02.val', 'common_list.0x07.val', 'wh25.intemp', 'rain.0x13.val'
CONFIG = {'StdArchive': {'archive_interval': 300}}


def make_driver(sources, clock, **options):
    return EcowittMultiDriver(config_dict=CONFIG, source_objects=sources, clock=clock, sleep=clock.sleep,
                              poll_interval=20, **options)


@pytest.fixture(autouse=True)
def reset_restart_state():
    gap_module._STATE['last_restart'] = None


def test_priority_merge_and_fallback(clock):
    gw1 = FakeSource('gw1', [{IT: 21.0, YR: 100.0}])
    gw2 = FakeSource('gw2', [{IT: 22.0, T: 5.0, H: 80.0, YR: 50.0}])
    drv = make_driver([gw1, gw2], clock)
    packets = drv.genLoopPackets()
    p = next(packets)
    assert p['inTemp'] == 21.0 and p['outTemp'] == 5.0 and p['outHumidity'] == 80.0
    assert p['usUnits'] == weewx.METRICWX and p['dateTime'] == clock.now


def test_source_priority_option_reorders(clock):
    gw1 = FakeSource('gw1', [{IT: 21.0}])
    gw2 = FakeSource('gw2', [{IT: 22.0}])
    drv = make_driver([gw1, gw2], clock, source_priority='gw2, gw1')
    assert next(drv.genLoopPackets())['inTemp'] == 22.0


def test_lower_source_polled_only_when_needed(clock):
    gw1 = FakeSource('gw1', [{IT: 21.0, T: 4.0}])
    gw2 = FakeSource('gw2', [{IT: 22.0, T: 5.0}])
    drv = make_driver([gw1, gw2], clock)
    it = drv.genLoopPackets()
    for _ in range(4):
        next(it)
    assert gw1.polls == 4 and gw2.polls == 1   # first cycle polls all


def test_lower_source_polled_for_unique_fields(clock):
    gw1 = FakeSource('gw1', [{IT: 21.0}])
    gw2 = FakeSource('gw2', [{T: 5.0}])
    drv = make_driver([gw1, gw2], clock)
    it = drv.genLoopPackets()
    for _ in range(3):
        p = next(it)
    assert gw2.polls == 3 and p['outTemp'] == 5.0


def test_rain_deltas_and_alias(clock):
    gw1 = FakeSource('gw1', [{IT: 20.0, YR: 100.0}, {IT: 20.0, YR: 100.4}, {IT: 20.0, YR: 100.6}])
    drv = make_driver([gw1], clock)
    it = drv.genLoopPackets()
    first = next(it)
    assert 't_rain' not in first
    second = next(it)
    assert second['t_rain'] == pytest.approx(0.4) and second['rain'] == pytest.approx(0.4)


def test_unknown_fields_are_passed(clock):
    gw1 = FakeSource('gw1', [{IT: 20.0, 'newsensor.1.val': 7.5}])
    drv = make_driver([gw1], clock, unknown_fields='pass')
    assert next(drv.genLoopPackets())['ecowitt_newsensor_1_val'] == 7.5
    gw1 = FakeSource('gw1', [{IT: 20.0, 'newsensor.1.val': 7.5}])
    drv = make_driver([gw1], clock, unknown_fields='ignore')
    assert 'ecowitt_newsensor_1_val' not in next(drv.genLoopPackets())


def test_outage_triggers_restart(clock):
    frames = [{IT: 20.0}] * 3 + [None] * 20 + [{IT: 20.0}]
    gw1 = FakeSource('gw1', frames)
    drv = make_driver([gw1], clock, max_gap_wait=600)
    it = drv.genLoopPackets()
    with pytest.raises(weewx.WeeWxIOError, match='restart'):
        for _ in range(50):
            next(it)
    assert gap_module.last_restart() is not None


def test_outage_without_history_is_not_restarted(clock):
    frames = [{IT: 20.0}] * 3 + [None] * 20 + [{IT: 20.0}] * 5
    gw1 = FakeSource('gw1', frames, history='none')
    drv = make_driver([gw1], clock)
    it = drv.genLoopPackets()
    packets = [next(it) for _ in range(6)]
    assert len(packets) == 6


def test_catchup_merges_history_by_priority(clock):
    now = clock.now
    last_ts = now - 3600
    h1 = [{'datetime': last_ts - 300 + i * 300, IT: 21.0, YR: 10.0 + i * 0.2} for i in range(13)]
    h2 = [{'datetime': last_ts - 300 + i * 300, IT: 22.0, T: 3.0 + i, YR: 5.0} for i in range(13)]
    drv = make_driver([FakeSource('gw1', [{}], records=h1), FakeSource('gw2', [{}], records=h2)], clock)
    records = list(drv.genStartupRecords(last_ts))
    assert records, 'no catchup records'
    assert all(last_ts < r['dateTime'] <= now for r in records)
    assert all(r['interval'] == 5 and r['usUnits'] == weewx.METRICWX for r in records)
    r = records[0]
    assert r['inTemp'] == 21.0 and 'outTemp' in r
    assert r['t_rain'] == pytest.approx(0.2) and r['rain'] == pytest.approx(0.2)
    assert sum(x['t_rain'] for x in records) == pytest.approx(0.2 * len(records))


def test_no_catchup_for_empty_database(clock):
    drv = make_driver([FakeSource('gw1', [{}], records=[])], clock)
    assert list(drv.genStartupRecords(None)) == []


def test_single_host_shorthand(clock):
    drv = EcowittMultiDriver(config_dict=CONFIG, host='192.0.2.1', clock=clock)
    assert [s.name for s in drv.sources] == ['gw1'] and drv.sources[0].host == '192.0.2.1'


def test_loader_builds_sources_from_stanza():
    import configobj
    from user.ecowitt_multi import loader
    cfg = configobj.ConfigObj({'StdArchive': {'archive_interval': '300'},
                               'EcowittMulti': {'source_priority': 'mq, gw1',
                                                'sources': {'gw1': {'type': 'http', 'host': '192.0.2.1'},
                                                            'mq': {'type': 'mqtt', 'broker': 'localhost'},
                                                            'off': {'host': '192.0.2.9', 'enabled': 'false'}}}})
    drv = loader(cfg, None)
    assert drv.source_priority == ['mq', 'gw1']
