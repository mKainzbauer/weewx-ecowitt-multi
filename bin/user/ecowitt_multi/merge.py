# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 mKainzbauer
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version. See the LICENSE file or https://www.gnu.org/licenses/.
"""Merging the records of competing sources by a configurable priority.

Each source has a cache with its latest valid record. A field is taken from the first source
in the field's priority list ([[priority]] or source_priority) whose cache is not older than
max_age, contains the field and whose value passed the plausibility check.
"""

import logging

log = logging.getLogger(__name__)


class PriorityMerger:

    def __init__(self, source_priority, field_priority=None, max_age=300, limits=None, debug=False):
        """source_priority: list of source names, best first.
        field_priority: {field or 'prefix*': [source names]}.
        limits: function(field) -> (min, max) or None."""
        self.source_priority = list(source_priority)
        self.field_priority = {}
        self.prefix_priority = []
        for key, value in (field_priority or {}).items():
            order = [s for s in value if s in self.source_priority]
            unknown = [s for s in value if s not in self.source_priority]
            if unknown:
                log.error('priority %s: unknown source(s) %s ignored', key, ', '.join(unknown))
            order += [s for s in self.source_priority if s not in order]
            if key.endswith('*'):
                self.prefix_priority.append((key[:-1], order))
            else:
                self.field_priority[key] = order
        self.prefix_priority.sort(key=lambda p: -len(p[0]))
        self.max_age = max_age
        self.limits = limits or (lambda field: None)
        self.debug = debug
        self.cache = {}   # source -> (ts, record)
        self.rejected = set()

    def priority(self, field):
        if field in self.field_priority:
            return self.field_priority[field]
        for prefix, order in self.prefix_priority:
            if field.startswith(prefix):
                return order
        return self.source_priority

    def validate(self, source, record):
        """record without implausible values."""
        result = {}
        for field, value in record.items():
            limits = self.limits(field)
            if limits is not None and isinstance(value, (int, float)) and not isinstance(value, bool):
                if not limits[0] <= value <= limits[1]:
                    if (source, field) not in self.rejected:
                        log.warning('%s: %s=%s outside %s..%s, ignored', source, field, value, *limits)
                        self.rejected.add((source, field))
                    continue
            result[field] = value
        return result

    def update(self, source, ts, record):
        """A successful poll of source replaces its cache."""
        self.cache[source] = (ts, self.validate(source, record))

    def drop(self, source):
        self.cache.pop(source, None)

    def fresh_sources(self, now):
        return [s for s, (ts, _) in self.cache.items() if now - ts <= self.max_age]

    def merge(self, now):
        """(record, provenance {field: source}) from the fresh caches."""
        fresh = {s: rec for s, (ts, rec) in self.cache.items() if now - ts <= self.max_age}
        fields = set()
        for rec in fresh.values():
            fields.update(rec)
        record, provenance = {}, {}
        for field in fields:
            for source in self.priority(field):
                rec = fresh.get(source)
                if rec is not None and rec.get(field) is not None:
                    record[field] = rec[field]
                    provenance[field] = source
                    break
        if self.debug:
            log.info('merge provenance: %s', ', '.join(f'{f}<{s}' for f, s in sorted(provenance.items())))
        return record, provenance

    def missing_or_degraded(self, now, required, first):
        """Required fields that the best source 'first' did not supply."""
        ts_rec = self.cache.get(first)
        rec = ts_rec[1] if ts_rec and now - ts_rec[0] <= self.max_age else {}
        return {f for f in required if rec.get(f) is None or self.priority(f)[0] != first}
