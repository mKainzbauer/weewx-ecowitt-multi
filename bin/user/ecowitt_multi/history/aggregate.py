# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 mKainzbauer
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version. See the LICENSE file or https://www.gnu.org/licenses/.
"""Turning history records of several sources into WeeWX archive records.

Per source: map to WeeWX fields, turn counter totals into deltas, aggregate into archive
buckets (end - archive_interval, end]. Then merge the buckets of all sources by priority.
"""

import logging
import math

import weewx

from ..cumulative import counter_delta

log = logging.getLogger(__name__)


def bucket_end(ts, interval):
    return int(math.ceil(ts / interval) * interval)


def _vecdir(values):
    x = sum(math.sin(math.radians(v)) for v in values)
    y = sum(math.cos(math.radians(v)) for v in values)
    if abs(x) < 1e-9 and abs(y) < 1e-9:
        return None
    return round(math.degrees(math.atan2(x, y)) % 360.0, 1)


def _aggregate(agg, values):
    values = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if not values:
        return None
    if agg == 'sum':
        return sum(values)
    if agg == 'max':
        return max(values)
    if agg == 'min':
        return min(values)
    if agg == 'last':
        return values[-1]
    if agg == 'vecdir':
        return _vecdir(values)
    return sum(values) / len(values)


def source_records(raw_records, mapper, counters):
    """WeeWX records (with 'dateTime') of one source; counter fields as deltas.

    The first record only provides the counter baselines."""
    chosen, last, result = {}, {}, []
    for raw in sorted(raw_records, key=lambda r: r.get('datetime', 0)):
        ts = raw.get('datetime')
        if ts is None:
            continue
        rec = mapper.map(raw)
        for field, total in mapper.counter_values(raw, counters, chosen).items():
            try:
                total = float(total)
            except (TypeError, ValueError):
                continue
            delta = counter_delta(last.get(field), total)
            last[field] = total
            if delta is not None:
                rec[field] = delta
        rec['dateTime'] = int(ts)
        result.append(rec)
    return result


def aggregate(records, agg, interval):
    """{bucket end: record} of records aggregated by agg (function field -> aggregation)."""
    buckets = {}
    for rec in records:
        buckets.setdefault(bucket_end(rec['dateTime'], interval), []).append(rec)
    result = {}
    for end, recs in buckets.items():
        out = {}
        fields = {f for r in recs for f in r if f != 'dateTime'}
        for field in fields:
            value = _aggregate(agg(field), [r.get(field) for r in recs])
            if value is not None:
                out[field] = value
        result[end] = out
    return result


def merge_buckets(per_source, priority, counters, aliases, interval, start_ts, stop_ts):
    """Archive records from {source: {end: record}}, best source per field.

    Counter fields of a bucket come from one source only."""
    ends = sorted({e for b in per_source.values() for e in b if start_ts < e <= stop_ts})
    records = []
    for end in ends:
        rec, sources = {}, {s: b[end] for s, b in per_source.items() if end in b}
        fields = {f for r in sources.values() for f in r}
        for field in fields:
            for source in priority(field):
                value = sources.get(source, {}).get(field)
                if value is not None:
                    rec[field] = value
                    break
        for field in counters:
            for source in priority(field):
                if field in sources.get(source, {}):
                    rec[field] = sources[source][field]
                    break
        for alias, target in aliases.items():
            if target in rec:
                rec[alias] = rec[target]
        rec.update(dateTime=end, usUnits=weewx.METRICWX, interval=interval // 60)
        records.append(rec)
    return records
