# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 mKainzbauer
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version. See the LICENSE file or https://www.gnu.org/licenses/.
"""Outage detection and gap filling via the WeeWX catchup mechanism.

WeeWX calls genStartupRecords() only when the engine starts. After an outage (no data from
any source for longer than archive_interval) the driver therefore stops emitting loop packets
(so no archive record is written for the outage) until the sources are back, and then raises
WeeWxIOError: weewxd restarts the engine and StdArchive fetches the missing records.
"""

import logging
import time

log = logging.getLogger(__name__)

NORMAL, OUTAGE, RECOVERING = 'normal', 'outage', 'recovering'
EMIT, SUPPRESS, RESTART = 'emit', 'suppress', 'restart'

# survives engine restarts (the module stays imported by weewxd)
_STATE = {'last_restart': None}


def last_restart():
    return _STATE['last_restart']


def note_restart(ts=None):
    _STATE['last_restart'] = time.time() if ts is None else ts


class GapMonitor:

    def __init__(self, archive_interval=300, mode='restart', max_gap_wait=3600, min_restart_interval=900):
        self.archive_interval = archive_interval
        self.mode = mode
        self.max_gap_wait = max_gap_wait
        self.min_restart_interval = min_restart_interval
        self.state = NORMAL
        self.last_data = None
        self.remembered = set()
        self.outage_start = None
        self.recover_start = None

    def update(self, now, online, history_capable):
        """Next action given the sources that delivered data now (online) and the sources with history."""
        if online:
            if self.state == NORMAL:
                self.last_data = now
                self.remembered |= set(online)
                return EMIT
            if self.state == OUTAGE:
                self.state, self.recover_start = RECOVERING, now
                log.info('sources back after %d s: %s', now - self.outage_start, ', '.join(sorted(online)))
            back_all = self.remembered <= set(online)
            waited = now - self.recover_start >= self.max_gap_wait
            if not (back_all or waited):
                return SUPPRESS
            return self._finish(now, set(online) & set(history_capable))
        if self.state == NORMAL:
            if self.last_data is not None and now - self.last_data > self.archive_interval:
                self.state, self.outage_start = OUTAGE, self.last_data
                log.warning('outage: no data from any source since %d s', now - self.last_data)
            elif self.last_data is None:
                return SUPPRESS
            else:
                return EMIT
        return SUPPRESS

    def _finish(self, now, with_history):
        gap = (self.recover_start or now) - self.outage_start
        self.state, self.last_data, self.outage_start, self.recover_start = NORMAL, now, None, None
        if self.mode != 'restart':
            log.info('gap of %d s not filled (gap_mode = %s)', gap, self.mode)
            return EMIT
        if not with_history:
            log.warning('gap of %d s cannot be filled: no source with history is online', gap)
            return EMIT
        last = last_restart()
        if last is not None and now - last < self.min_restart_interval:
            log.warning('gap of %d s not filled: last restart only %d s ago (min_restart_interval)', gap, now - last)
            return EMIT
        log.info('restarting the engine to fill a gap of %d s from %s', gap, ', '.join(sorted(with_history)))
        note_restart(now)
        return RESTART
