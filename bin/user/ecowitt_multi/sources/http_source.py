# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 mKainzbauer
# Portions Copyright (C) 2026 Ian Millard (weewx-EcowittGateway)
# Portions Copyright (C) 2024-25 Gary Roderick (ecowitt_http.py / GW1000 driver)
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version. See the LICENSE file or https://www.gnu.org/licenses/.
#
# Modified by mKainzbauer, 2026: one polled source per gateway instead of a collector thread;
# sensor info cached (see CHANGELOG.md).
"""A gateway read via its local HTTP API (get_livedata_info, get_sensors_info, ...)."""

import logging

from ..mapping import SensorMapper, route_wind_voltage, wind_array
from ..util import DeviceIOError, ParseError, to_int
from .base import Source
from .http_api import DEFAULT_MAX_TRIES, DEFAULT_RETRY_WAIT, DEFAULT_URL_TIMEOUT, EcowittDevice

log = logging.getLogger(__name__)

DEFAULT_SENSORS_INTERVAL = 600


class HttpSource(Source):

    kind = 'http'

    def __init__(self, name, config, debug=None, device=None):
        super().__init__(name, config, debug)
        host = config.get('host')
        if not host:
            raise ValueError(f"source '{name}': 'host' is required")
        port = config.get('port')
        self.host = f'{host}:{port}' if port and ':' not in str(host) else str(host)
        self.sensors_interval = to_int(config.get('sensors_interval', DEFAULT_SENSORS_INTERVAL))
        self.get_soilad = str(config.get('get_soilad', 'true')).lower() not in ('false', '0', 'no')
        self.device = device or EcowittDevice(
            ip_address=self.host, max_tries=to_int(config.get('max_tries', DEFAULT_MAX_TRIES)),
            retry_wait=to_int(config.get('retry_wait', DEFAULT_RETRY_WAIT)),
            url_timeout=to_int(config.get('url_timeout', DEFAULT_URL_TIMEOUT)), debug=debug)
        self.sensor_mapper = SensorMapper(config.get('sensor_map'))
        self.sensors = {}
        self.sensors_ts = None

    # Source: S2 @5cbe533, lines 2154-2175 (EcowittHttpCollector.get_current_data). Adapted: sensor info cached
    # for sensors_interval seconds; errors are reported as DeviceIOError.
    def poll(self, now):
        dev = self.device
        try:
            data = dev.get_live_data()
            for part in (dev.get_rain_totalspart(), dev.get_piezo_rain_datapart(), dev.get_device_info_datapart(),
                         dev.get_stationtype()):
                data.update(part or {})
            if self.get_soilad and any(f'ch_soil.{ch}.humidity' in data for ch in range(1, 17)):
                data.update(dev.get_soil_adnow_data() or {})
            if self.sensors_ts is None or now - self.sensors_ts >= self.sensors_interval:
                try:
                    self.sensors = dev.get_sensors_data() or {}
                    self.sensors_ts = now
                except (DeviceIOError, ParseError) as e:
                    log.debug('%s: sensor info not available: %s', self.name, e)
        except (DeviceIOError, ParseError, OSError, ValueError, KeyError, TypeError) as e:
            raise DeviceIOError(f'{self.name}: {e}') from e
        if 'WS6210' in (data.get('apName') or '') and data.get('debug.usr_interval') is not None:
            data['debug.usr_interval'] *= 60
        data['datetime'] = int(now)
        data.update(self.sensors)
        route_wind_voltage(data, wind_array(data))
        return self.sensor_mapper.apply(data)

    def history_providers(self, **options):
        providers = []
        if self.history in ('auto', 'sd'):
            from ..history.sd import EcowittDeviceCatchup
            from ..util import CatchupObjectError
            try:
                providers.append(EcowittDeviceCatchup(ip_address=self.host, device=self.device,
                                                      sd_port=self.config.get('sd_port', 81), **options))
            except CatchupObjectError as e:
                log.info('%s: no SD card history: %s', self.name, e)
        if self.history in ('auto', 'cloud'):
            cloud = self.cloud_provider()
            if cloud is not None:
                providers.append(cloud)
        return providers
