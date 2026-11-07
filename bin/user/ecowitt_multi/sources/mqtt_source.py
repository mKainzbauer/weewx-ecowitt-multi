# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 mKainzbauer
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version. See the LICENSE file or https://www.gnu.org/licenses/.
"""MQTT sources: the native gateway MQTT export and ecowitt2mqtt.

Payload keys are translated to canonical keys with the catalog's protocol_map (native,
Ecowitt protocol keys, imperial strings) or ecowitt2mqtt_map. The key tables are based on
the public Ecowitt protocol (S8) and the ecowitt2mqtt documentation (S9) and have not been
verified against every firmware; unknown keys are passed on as 'mqtt.<key>'.

paho-mqtt is only needed when an MQTT source is configured.
"""

import json
import logging
import threading
import time
import urllib.parse

import weewx

from ..units import convert_value, register_units
from ..util import DeviceIOError, to_bool, to_int
from .base import Source

log = logging.getLogger(__name__)

# values ecowitt2mqtt calculates itself; ignored unless mqtt_use_calculated (WeeWX derives its own)
E2M_CALCULATED = frozenset((
    'dewpoint', 'dewpointin', 'heatindex', 'heatindexin', 'feelslike', 'feelslikein', 'windchill', 'frostpoint',
    'frostrisk', 'humidex', 'humidexperception', 'thermalperception', 'safe_exposure_time', 'solarradiation_perceived',
    'simmerindex', 'simmerperception', 'beaufortscale', 'absolute_humidity', 'absolute_humidityin'))
_IGNORED_KEYS = frozenset(('passkey', 'stationtype', 'model', 'freq', 'dateutc', 'runtime', 'heap', 'interval'))


def _number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def decode_payload(payload):
    """A dict from a JSON or an x-www-form-urlencoded payload (bytes or str)."""
    if isinstance(payload, (bytes, bytearray)):
        payload = payload.decode('utf-8', errors='replace')
    payload = payload.strip()
    if payload.startswith('{'):
        data = json.loads(payload)
        if not isinstance(data, dict):
            raise ValueError('JSON payload is not an object')
        return data
    if '=' in payload:
        return dict(urllib.parse.parse_qsl(payload, keep_blank_values=True))
    raise ValueError('payload is neither JSON nor form encoded')


def _translate(data, table, unit_index, unit_system, use_calculated=False, calculated=frozenset()):
    result, meta = {}, {}
    for key, raw in data.items():
        lkey = str(key).lower()
        if lkey in _IGNORED_KEYS:
            meta[lkey] = raw
            continue
        if lkey in calculated and not use_calculated:
            continue
        value = _number(raw)
        entry = table.get(key) or table.get(lkey)
        if entry is None:
            if value is not None:
                result[f'mqtt.{lkey}'] = value
            continue
        if value is None:
            continue
        canonical, unit, group = entry[0], entry[unit_index], entry[-1]
        try:
            result[canonical] = convert_value(value, unit, group, unit_system)
        except (KeyError, TypeError, ValueError) as e:
            log.debug('cannot convert %s=%s %s: %s', key, raw, unit, e)
    if 'stationtype' in meta:
        result['apName'] = meta['stationtype']
    return result


def parse_native(payload, catalog, unit_system=weewx.METRICWX):
    """Canonical data from a native gateway payload (Ecowitt protocol keys, imperial units)."""
    register_units()
    return _translate(decode_payload(payload), catalog.protocol_map, 1, unit_system)


def parse_e2m(payload, catalog, e2m_units='imperial', use_calculated=False, unit_system=weewx.METRICWX):
    """Canonical data from an ecowitt2mqtt single-topic payload."""
    register_units()
    index = 2 if e2m_units == 'metric' else 1
    return _translate(decode_payload(payload), catalog.ecowitt2mqtt_map, index, unit_system, use_calculated,
                      E2M_CALCULATED)


def parse_e2m_value(key, payload, catalog, e2m_units='imperial', use_calculated=False,
                    unit_system=weewx.METRICWX):
    """Canonical data from one ecowitt2mqtt multi-topic message (topic suffix key)."""
    if isinstance(payload, (bytes, bytearray)):
        payload = payload.decode('utf-8', errors='replace')
    value = payload.strip()
    if value.startswith('{'):
        obj = json.loads(value)
        value = obj.get('value', obj.get('state')) if isinstance(obj, dict) else None
    return parse_e2m(json.dumps({key: value}), catalog, e2m_units, use_calculated, unit_system)


class MqttSource(Source):

    kind = 'mqtt'
    push = True

    def __init__(self, name, config, catalog, debug=None, client_factory=None):
        super().__init__(name, config, debug)
        self.catalog = catalog
        self.broker = config.get('broker', 'localhost')
        self.port = to_int(config.get('port', 1883))
        self.topic = config.get('topic', 'ecowitt/#')
        self.format = str(config.get('format', 'native')).lower()
        if self.format not in ('native', 'ecowitt2mqtt'):
            raise ValueError(f"source '{name}': format must be 'native' or 'ecowitt2mqtt'")
        self.e2m_units = str(config.get('e2m_units', 'imperial')).lower()
        self.use_calculated = to_bool(config.get('mqtt_use_calculated', False))
        self.max_age = to_int(config.get('mqtt_timeout', 180))
        self.username, self.password = config.get('username'), config.get('password')
        self.tls = to_bool(config.get('tls', False))
        self.client_id = config.get('client_id', f'weewx-ecowitt-multi-{name}')
        self.client_factory = client_factory
        self.client = None
        self.lock = threading.Lock()
        self.data = {}
        self.msg_ts = None

    # -- message handling (also used by the tests) ---------------------------------

    def handle(self, topic, payload, ts=None):
        if str(topic).endswith('/status'):
            return
        try:
            if self.format == 'native':
                data = parse_native(payload, self.catalog)
                replace = True
            else:
                text = payload.decode('utf-8', errors='replace') if isinstance(payload, (bytes, bytearray)) \
                    else str(payload)
                obj = json.loads(text) if text.strip().startswith('{') else None
                if isinstance(obj, dict) and not set(obj) <= {'value', 'state', 'unit', 'attributes'}:
                    data = parse_e2m(text, self.catalog, self.e2m_units, self.use_calculated)
                    replace = True
                else:
                    key = str(topic).rstrip('/').split('/')[-1]
                    data = parse_e2m_value(key, text, self.catalog, self.e2m_units, self.use_calculated)
                    replace = False
        except (ValueError, UnicodeError) as e:
            log.warning('%s: ignoring message on %s: %s', self.name, topic, e)
            return
        with self.lock:
            if replace:
                self.data = data
            else:
                self.data.update(data)
            self.msg_ts = ts or time.time()

    def poll(self, now):
        with self.lock:
            if self.msg_ts is None or now - self.msg_ts > self.max_age:
                raise DeviceIOError(f'{self.name}: no MQTT data for more than {self.max_age} s')
            data = dict(self.data)
            data['datetime'] = int(self.msg_ts)
        return data

    # -- broker connection ---------------------------------------------------------

    def start(self):
        if self.client_factory is not None:
            self.client = self.client_factory(self)
            return
        try:
            import paho.mqtt.client as mqtt
        except ImportError as e:
            raise DeviceIOError(f"{self.name}: MQTT source needs paho-mqtt (pip install paho-mqtt)") from e
        try:
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=self.client_id)
        except AttributeError:  # paho-mqtt < 2.0
            client = mqtt.Client(client_id=self.client_id)
        if self.username:
            client.username_pw_set(self.username, self.password)
        if self.tls:
            client.tls_set()

        def on_connect(c, userdata, flags, rc, properties=None):
            log.info('%s: connected to %s:%s, subscribing to %s', self.name, self.broker, self.port, self.topic)
            c.subscribe(self.topic)

        def on_message(c, userdata, msg):
            self.handle(msg.topic, msg.payload)

        client.on_connect, client.on_message = on_connect, on_message
        try:
            client.connect_async(self.broker, self.port)
            client.loop_start()
        except OSError as e:
            raise DeviceIOError(f'{self.name}: cannot connect to {self.broker}:{self.port}: {e}') from e
        self.client = client

    def close(self):
        if self.client is not None and hasattr(self.client, 'loop_stop'):
            try:
                self.client.disconnect()
                self.client.loop_stop()
            except Exception as e:  # noqa: BLE001 - shutting down
                log.debug('%s: %s', self.name, e)
        self.client = None

    def history_providers(self, **options):
        cloud = self.cloud_provider() if self.history in ('auto', 'cloud') else None
        return [cloud] if cloud is not None else []
