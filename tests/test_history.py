# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 mKainzbauer
"""History sources with synthetic SD card CSV / Ecowitt.net responses (injected, no network)."""

import datetime
import io
import json
import time

import pytest

from user.ecowitt_multi.history.aggregate import aggregate, bucket_end, merge_buckets
from user.ecowitt_multi.history.cloud import EcowittNetCatchup
from user.ecowitt_multi.history.sd import EcowittDeviceCatchup
from user.ecowitt_multi.util import CatchupObjectError, DeviceIOError

CSV = ('Time,Indoor Temperature(\u2103),Outdoor Temperature(\u2103),Outdoor Humidity(%),Yearly Rain(mm)\n'
       '2026-01-10 10:00,21.0,4.0,80,100.0\n'
       '2026-01-10 10:05,21.2,4.5,81,100.4\n'
       '2026-01-10 10:10,21.4,5.0,82,100.4\n')


class FakeDevice:
    model = 'GW2000A'

    def __init__(self, sd=True):
        self.sd = sd

    def get_sdmmc_info_data(self):
        if not self.sd:
            raise DeviceIOError('no SD card')
        return {'info': {'Interval': '5'}, 'file_list': [{'name': '202601A.csv', 'type': 'file'},
                                                        {'name': 'log', 'type': 'file'}]}


def fake_urlopen(url, timeout=None):
    assert url == 'http://192.0.2.1:81/202601A.csv'
    return io.BytesIO(CSV.encode('utf-8'))


def test_sd_history_records():
    sd = EcowittDeviceCatchup(ip_address='192.0.2.1:80', device=FakeDevice(), urlopen=fake_urlopen)
    start = datetime.datetime(2026, 1, 10, 10, 0).timestamp()
    recs = list(sd.gen_history_records(start_ts=start))
    assert [r['datetime'] - start for r in recs] == [300, 600]
    assert recs[0]['common_list.0x02.val'] == pytest.approx(4.5)
    assert recs[0]['rain.0x13.val'] == pytest.approx(100.4)
    assert recs[0]['interval'] == 5


def test_sd_not_supported():
    with pytest.raises(CatchupObjectError):
        EcowittDeviceCatchup(ip_address='192.0.2.1', device=FakeDevice(sd=False))


class FakeResponse(io.BytesIO):
    headers = type('H', (), {'get_content_charset': staticmethod(lambda: 'utf-8')})()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_cloud_history_records():
    ts = (int(time.time()) - 86400) // 300 * 300
    body = {'code': 0, 'data': {'outdoor': {'temperature': {'list': {str(ts): '4.5'}}},
                                'rainfall': {'yearly': {'list': {str(ts): '100.4'}}}}}
    calls = []

    def urlopen(req, timeout=None):
        calls.append(req.full_url)
        return FakeResponse(json.dumps(body).encode())

    cloud = EcowittNetCatchup(api_key='k', app_key='a', mac='00:11:22:33:44:55', urlopen=urlopen)
    recs = list(cloud.gen_history_records(start_ts=ts - 600, stop_ts=ts + 60))
    assert recs == [{'datetime': ts, 'interval': 5, 'common_list.0x02.val': 4.5, 'rain.0x13.val': 100.4}]
    assert 'temp_unitid=1' in calls[0]


def test_cloud_without_keys_yields_nothing():
    assert list(EcowittNetCatchup().gen_history_records(start_ts=0, stop_ts=1)) == []


def test_aggregation_types():
    recs = [{'dateTime': 10, 'outTemp': 1.0, 't_rain': 0.2, 'windDir': 350, 'windGust': 3, 'lightning_num': 1},
            {'dateTime': 300, 'outTemp': 3.0, 't_rain': 0.3, 'windDir': 10, 'windGust': 5, 'lightning_num': 2}]
    agg = {'outTemp': 'avg', 't_rain': 'sum', 'windDir': 'vecdir', 'windGust': 'max', 'lightning_num': 'last'}
    out = aggregate(recs, agg.get, 300)[300]
    assert out['outTemp'] == 2.0 and out['t_rain'] == pytest.approx(0.5) and out['windGust'] == 5
    assert out['windDir'] in (0.0, 360.0) and out['lightning_num'] == 2
    assert bucket_end(301, 300) == 600


def test_merge_buckets_counter_from_one_source():
    per_source = {'gw1': {600: {'outTemp': 1.0}}, 'gw2': {600: {'outTemp': 2.0, 't_rain': 0.4}}}
    recs = merge_buckets(per_source, lambda f: ['gw1', 'gw2'], {'t_rain': []}, {'rain': 't_rain'}, 300, 300, 900)
    assert recs == [{'outTemp': 1.0, 't_rain': 0.4, 'rain': 0.4, 'dateTime': 600, 'usUnits': 17, 'interval': 5}]
