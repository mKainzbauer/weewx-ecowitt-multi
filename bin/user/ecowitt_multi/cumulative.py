# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 mKainzbauer
# Portions Copyright (C) 2026 Ian Millard, derived from work Copyright (C) 2024-25 Gary Roderick
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version. See the LICENSE file or https://www.gnu.org/licenses/.
#
# Modified by mKainzbauer, 2026: counter handling across several sources (see CHANGELOG.md).
"""Cumulative counters (rain, lightning) of several sources without double counting.

Every source keeps its own last raw total. A delta from source s covers (s.prev_ts, ts]. It is
used only if s is the current provider of the field or the interval starts after the time up to
which the field has already been counted; otherwise s just re-baselines. A source takes over from
the current provider if it has a higher priority or the provider is stale.
"""

import logging

log = logging.getLogger(__name__)


# Source: S2 @5cbe533, lines 716-756 (DeltaTracker.calc): wraparound/reset semantics. Adapted per source.
def counter_delta(last, new):
    """Delta between two raw totals; a decreasing total is a reset, so the new total is the delta."""
    if last is None or new is None:
        return None
    return new if new < last else new - last


class CumulativeMerger:

    def __init__(self, priority, stale_after=300):
        """priority: function(field) -> list of source names, best first."""
        self.priority = priority
        self.stale_after = stale_after
        self.last = {}              # (source, field) -> (raw, ts)
        self.covered_until = {}     # field -> ts
        self.provider = {}          # field -> source

    def _rank(self, field, source):
        order = self.priority(field)
        return order.index(source) if source in order else len(order)

    def update(self, source, field, raw, ts):
        """Feed a raw total of source; return the delta to report (or None)."""
        try:
            raw = float(raw)
        except (TypeError, ValueError):
            return None
        prev = self.last.get((source, field))
        self.last[(source, field)] = (raw, ts)
        if prev is None:
            log.debug('%s: %s baseline %s', source, field, raw)
            return None
        prev_raw, prev_ts = prev
        if ts <= prev_ts:
            return None
        delta = counter_delta(prev_raw, raw)
        if raw < prev_raw:
            log.info('%s: %s counter reset detected: new=%s last=%s', source, field, raw, prev_raw)
        provider, covered = self.provider.get(field), self.covered_until.get(field)
        if provider is None or provider == source:
            accept = True
        else:
            no_overlap = covered is None or prev_ts >= covered
            better = self._rank(field, source) < self._rank(field, provider)
            stale = covered is None or ts - covered > self.stale_after
            accept = no_overlap and (better or stale)
        if not accept:
            return None
        if provider != source:
            log.info('%s: now counted from %s (was %s)', field, source, provider)
        self.provider[field] = source
        self.covered_until[field] = ts
        return delta

    def forget(self, source):
        """Drop the baselines of source (e.g. after an outage that is filled from history)."""
        for key in [k for k in self.last if k[0] == source]:
            del self.last[key]
