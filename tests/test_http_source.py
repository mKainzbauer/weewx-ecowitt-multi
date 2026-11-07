# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 mKainzbauer
"""HttpSource and the S2 parser with a synthetic get_livedata_info response (no network)."""

import pytest

from user.ecowitt_multi.mapping import CatalogMapper
from user.ecowitt_multi.sources.http_source import HttpSource
from user.ecowitt_multi.sources.parser import EcowittHttpParser
from user.ecowitt_multi.util import DeviceIOError

LIVEDATA = {
    'common_list': [{'id': '0x02', 'val': '50.0', 'unit': 'F'}, {'id': '0x07', 'val': '80%'},
                    {'id': '0x0B', 'val': '7.2 km/h'}, {'id': '0x0A', 'val': '180'}],
    'wh25': [{'intemp': '21.0', 'unit': 'C', 'inhumi': '45%', 'abs': '1000.0 hPa', 'rel': '1013.0 hPa'}],
    'rain': [{'id': '0x13', 'val': '100.4 mm'}, {'id': '0x0E', 'val': '1.2 mm/Hr'}],
}


class FakeDevice:
    def __init__(self):
        self.parser = EcowittHttpParser()
        self.fail = False
        self.sensor_calls = 0

    def get_live_data(self):
        if self.fail:
            raise DeviceIOError('timeout')
        return self.parser.parse_get_livedata_info(LIVEDATA)

    def get_rain_totalspart(self):
        return {}

    get_piezo_rain_datapart = get_device_info_datapart = get_rain_totalspart

    def get_stationtype(self):
        return {'apName': 'GW2000A'}

    def get_sensors_data(self):
        self.sensor_calls += 1
        return {}


def test_http_source_poll_and_mapping(catalog):
    dev = FakeDevice()
    src = HttpSource('gw1', {'host': '192.0.2.1', 'port': 8080}, device=dev)
    assert src.host == '192.0.2.1:8080'
    data = src.poll(1000)
    assert data['datetime'] == 1000 and data['apName'] == 'GW2000A'
    record = CatalogMapper(catalog).map(data)
    assert record['outTemp'] == pytest.approx(10.0)
    assert record['outHumidity'] == 80
    assert record['windSpeed'] == pytest.approx(2.0)
    assert record['barometer'] == pytest.approx(1013.0)
    assert record['pressure'] == pytest.approx(1000.0)
    assert record['inTemp'] == pytest.approx(21.0)
    assert 'wbgt' in record and 'vpd' in record   # derived like upstream


def test_sensor_info_is_cached(catalog):
    dev = FakeDevice()
    src = HttpSource('gw1', {'host': '192.0.2.1', 'sensors_interval': 600}, device=dev)
    src.poll(0)
    src.poll(20)
    src.poll(700)
    assert dev.sensor_calls == 2


def test_http_errors_become_device_io_errors():
    dev = FakeDevice()
    dev.fail = True
    src = HttpSource('gw1', {'host': '192.0.2.1'}, device=dev)
    with pytest.raises(DeviceIOError, match='gw1'):
        src.poll(0)


def test_host_is_required():
    with pytest.raises(ValueError):
        HttpSource('gw1', {}, device=FakeDevice())
