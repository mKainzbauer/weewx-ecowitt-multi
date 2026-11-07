# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 mKainzbauer
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version. See the LICENSE file or https://www.gnu.org/licenses/.
"""Common base of all data sources."""

import logging

from ..util import to_bool, to_int

log = logging.getLogger(__name__)

HISTORY_MODES = ('auto', 'sd', 'cloud', 'none')


class Source:
    """A data source. poll() returns canonical (flattened HTTP API, METRICWX) data or None."""

    kind = 'base'
    push = False

    def __init__(self, name, config, debug=None):
        self.name = name
        self.config = config
        self.debug = debug
        self.enabled = to_bool(config.get('enabled', True))
        self.history = str(config.get('history', 'auto')).lower()
        if self.history not in HISTORY_MODES:
            log.error("%s: unknown history '%s', using 'auto'", name, self.history)
            self.history = 'auto'
        self.offline_retry = to_int(config.get('offline_retry', 60))
        self.online = None
        self.last_ok = None
        self.last_try = None
        self.supplied = set()
        self.has_history = False

    def start(self):
        pass

    def close(self):
        pass

    def poll(self, now):
        raise NotImplementedError

    def history_providers(self, **options):
        """Objects with gen_history_records(start_ts) in preferred order (may be empty)."""
        return []

    def cloud_provider(self, **options):
        if self.config.get('api_key') and self.config.get('app_key') and self.config.get('mac'):
            from ..history.cloud import EcowittNetCatchup
            return EcowittNetCatchup(api_key=self.config['api_key'], app_key=self.config['app_key'],
                                     mac=self.config['mac'], timeout=to_int(self.config.get('cloud_timeout', 30)),
                                     **options)
        return None

    def due(self, now):
        """False while an offline source waits for offline_retry."""
        return not (self.online is False and self.last_try is not None and now - self.last_try < self.offline_retry)

    def mark(self, now, ok):
        self.last_try = now
        if ok:
            if self.online is False:
                log.info('%s: back online', self.name)
            self.online, self.last_ok = True, now
        else:
            self.online = False

    def __repr__(self):
        return f'{self.__class__.__name__}({self.name})'
