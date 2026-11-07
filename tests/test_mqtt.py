# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 mKainzbauer
"""MQTT payload parsing. Payloads are synthetic examples in the documented formats (S8, S9)."""

import pytest

from user.ecowitt_multi.sources.mqtt_source import MqttSource, parse_e2m, parse_native
from user.ecowitt_multi.util import DeviceIOError

NATIVE = ('{"PASSKEY": "ABC", "stationtype": "GW2000A", "runtime": "123456", "tempinf": "74.3", '
          '"humidityin": "45", "baromrelin": "29.92", "tempf": "50.0", "humidity": "82", '
          '"windspeedmph": "3.4", "rainratein": "0.00", "yearlyrainin": "10.0", "wh90batt": "3.2"}')
E2M = ('{"tempin": 22.5, "humidityin": 53.0, "baromrel": 1013.2, "temp": 12.3, "humidity": 88.0, '
       '"winddir": 81, "windspeed": 0.0, "dewpoint": 10.3, "heatindex": 12.3}')


def test_native_payload_is_converted_to_metricwx(catalog):
    data = parse_native(NATIVE, catalog)
    assert data['common_list.0x02.val'] == pytest.approx(10.0)
    assert data['common_list.0x07.val'] == 82
    assert data['wh25.rel'] == pytest.approx(1013.2, abs=0.1)
    assert data['common_list.0x0B.val'] == pytest.approx(1.52, abs=0.01)    # m/s
    assert data['rain.0x13.val'] == pytest.approx(254.0)
    assert data['mqtt.wh90batt'] == 3.2 and data['apName'] == 'GW2000A'
    assert 'mqtt.passkey' not in data and 'mqtt.runtime' not in data


def test_native_form_encoded_payload(catalog):
    data = parse_native('PASSKEY=ABC&tempf=32.0&humidity=50', catalog)
    assert data['common_list.0x02.val'] == pytest.approx(0.0)


def test_e2m_metric_payload_ignores_calculated(catalog):
    data = parse_e2m(E2M, catalog, e2m_units='metric')
    assert data['common_list.0x02.val'] == pytest.approx(12.3)
    assert data['wh25.intemp'] == pytest.approx(22.5)
    assert data['wh25.rel'] == pytest.approx(1013.2)
    assert not any('dewpoint' in k or 'heatindex' in k for k in data)
    assert 'mqtt.dewpoint' in parse_e2m(E2M, catalog, e2m_units='metric', use_calculated=True)


def test_mqtt_source_single_and_multi_topic(catalog):
    src = MqttSource('e2m', {'format': 'ecowitt2mqtt', 'e2m_units': 'metric', 'topic': 'ecowitt2mqtt/#'}, catalog)
    with pytest.raises(DeviceIOError):
        src.poll(1000)
    src.handle('ecowitt2mqtt/dev1', E2M, ts=1000)
    assert src.poll(1010)['common_list.0x02.val'] == pytest.approx(12.3)
    src.handle('ecowitt2mqtt/dev1/temp', b'14.0', ts=1020)
    src.handle('ecowitt2mqtt/dev1/status', b'online', ts=1030)
    data = src.poll(1030)
    assert data['common_list.0x02.val'] == pytest.approx(14.0) and data['datetime'] == 1020
    with pytest.raises(DeviceIOError):
        src.poll(1020 + 181)


def test_mqtt_source_bad_payload_is_ignored(catalog):
    src = MqttSource('gw', {'format': 'native'}, catalog)
    src.handle('ecowitt/gw', b'not a payload', ts=1)
    assert src.msg_ts is None
