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
# This file is part of weewx-ecowitt-multi. Parts of the package are adapted from
# weewx-EcowittGateway by Ian Millard (https://github.com/Millardiang/weewx-ecowittGateway,
# commit 5cbe533), which is derived from the Ecowitt HTTP / GW1000 drivers by Gary Roderick.
# See CREDITS.md for the full list of sources and what was changed.
"""WeeWX driver for several Ecowitt gateways (HTTP API, optionally MQTT).

* polls the gateways in priority order and merges competing values field by field
  (source_priority, [[priority]]);
* counts rain and lightning from several sources without double counting;
* fills outages from the gateways' SD card or from Ecowitt.net via the WeeWX catchup
  (genStartupRecords).

Configuration: see README.md, section [EcowittMulti].
"""

import logging
import time

import weewx
import weewx.drivers

from .catalog import load_catalog
from .cumulative import CumulativeMerger
from .gap import EMIT, RESTART, GapMonitor
from .mapping import CatalogMapper
from .merge import PriorityMerger
from .util import CatchupObjectError, DebugOptions, DeviceIOError, DriverError, to_bool, to_int, to_list

log = logging.getLogger(__name__)

DRIVER_NAME = 'EcowittMulti'
DRIVER_VERSION = '0.1.0'

DEFAULT_POLL_INTERVAL = 20
DEFAULT_MAX_CATCHUP_DAYS = 7


def loader(config_dict, engine):
    return EcowittMultiDriver(config_dict=config_dict, **config_dict[DRIVER_NAME])


def confeditor_loader():
    from .configurator import EcowittMultiConfEditor
    return EcowittMultiConfEditor()


def configurator_loader(config_dict):
    from .configurator import EcowittMultiConfigurator
    return EcowittMultiConfigurator()


def make_source(name, config, catalog, debug=None):
    kind = str(config.get('type', 'http')).lower()
    if kind == 'http':
        from .sources.http_source import HttpSource
        return HttpSource(name, config, debug)
    if kind == 'mqtt':
        from .sources.mqtt_source import MqttSource
        return MqttSource(name, config, catalog, debug)
    raise ValueError(f"source '{name}': unknown type '{kind}'")


class EcowittMultiDriver(weewx.drivers.AbstractDevice):

    def __init__(self, config_dict=None, source_objects=None, clock=time.time, sleep=time.sleep, **stn_dict):
        log.info('%s driver version %s', DRIVER_NAME, DRIVER_VERSION)
        config_dict = config_dict or {}
        self.clock, self.sleep = clock, sleep
        self.debug = DebugOptions(**stn_dict)
        self._archive_interval = to_int(config_dict.get('StdArchive', {}).get('archive_interval', 300))
        self.catalog = load_catalog(stn_dict.get('catalog_file'))
        self.catalog.register_obs_groups()
        self.rain_source = str(stn_dict.get('rain_source', 'tipping')).lower()
        self.poll_interval = to_int(stn_dict.get('poll_interval', DEFAULT_POLL_INTERVAL))
        self.poll_mode = str(stn_dict.get('poll_mode', 'auto')).lower()
        required = to_list(stn_dict.get('required_fields', 'auto'))
        self.required = None if required in ([], ['auto']) else set(required)
        self.max_catchup_days = to_int(stn_dict.get('max_catchup_days', DEFAULT_MAX_CATCHUP_DAYS))
        self.catchup_grace = to_int(stn_dict.get('catchup_grace', 0))
        self.diagnostics = to_bool(stn_dict.get('diagnostic_fields', False))
        cache_max_age = to_int(stn_dict.get('cache_max_age', self.archive_interval))

        sources = source_objects
        if sources is None:
            src_cfg = stn_dict.get('sources') or {}
            if not src_cfg and stn_dict.get('host'):
                # shorthand for a single gateway: host (and history) directly in [EcowittMulti]
                src_cfg = {'gw1': {'type': 'http', 'host': stn_dict['host'],
                                   'history': stn_dict.get('history', 'auto')}}
            if not src_cfg:
                raise weewx.ViolatedPrecondition(f'[{DRIVER_NAME}] needs at least one source in [[sources]]')
            sources = [make_source(n, c, self.catalog, self.debug) for n, c in src_cfg.items()]
        sources = [s for s in sources if s.enabled]
        if not sources:
            raise weewx.ViolatedPrecondition(f'[{DRIVER_NAME}]: all sources are disabled')
        by_name = {s.name: s for s in sources}
        order = [n for n in to_list(stn_dict.get('source_priority')) if n in by_name]
        order += [s.name for s in sources if s.name not in order]
        self.sources = [by_name[n] for n in order]
        self.source_priority = order

        self.mapper = CatalogMapper(self.catalog, self.rain_source, stn_dict.get('field_map_extensions'),
                                    str(stn_dict.get('unknown_fields', 'pass')).lower())
        self.merger = PriorityMerger(order, {k: to_list(v) for k, v in (stn_dict.get('priority') or {}).items()},
                                     max_age=cache_max_age, limits=self.catalog.limits, debug=self.debug.merge)
        self.cumulative = CumulativeMerger(self.merger.priority, stale_after=self.archive_interval)
        self.counters = self.catalog.counters
        self.aliases = self.catalog.aliases_for(self.rain_source)
        self.chosen = {s.name: {} for s in self.sources}
        self.pending = {}
        self.gap = GapMonitor(self.archive_interval, str(stn_dict.get('gap_mode', 'restart')).lower(),
                              to_int(stn_dict.get('max_gap_wait', 3600)),
                              to_int(stn_dict.get('min_restart_interval', 900)))
        self.cycles = 0
        self.started = False
        log.info('sources in priority order: %s', ', '.join(f'{s.name} ({s.kind})' for s in self.sources))

    @property
    def hardware_name(self):
        return DRIVER_NAME

    @property
    def archive_interval(self):
        return self._archive_interval

    # -- polling -------------------------------------------------------------------

    def _start(self):
        if not self.started:
            for source in self.sources:
                try:
                    source.start()
                except DeviceIOError as e:
                    log.error('%s', e)
            self.started = True

    def poll_source(self, source, now):
        """Poll one source and feed the caches; True if it delivered data."""
        try:
            data = source.poll(now)
        except DeviceIOError as e:
            if source.online is not False:
                log.warning('%s', e)
            source.mark(now, False)
            return False
        source.mark(now, True)
        if data is None:
            return True
        ts = data.get('datetime', now)
        record = self.mapper.map(data)
        self.merger.update(source.name, ts, record)
        source.supplied |= set(record)
        for field, raw in self.mapper.counter_values(data, self.counters, self.chosen[source.name]).items():
            source.supplied.add(field)
            delta = self.cumulative.update(source.name, field, raw, ts)
            if delta is not None:
                self.pending[field] = self.pending.get(field, 0.0) + delta
        if self.debug.poll:
            log.info('%s: %d fields', source.name, len(record))
        return True

    def _wanted(self, source, now, delivered):
        """Whether a lower priority source has to be polled in this cycle.

        It is polled if it supplied a required field before that no better source delivered now."""
        if source.push or self.poll_mode == 'all' or self.cycles == 0 or not source.supplied:
            return True
        required = self.required if self.required is not None else source.supplied
        fresh = {s: rec for s, (ts, rec) in self.merger.cache.items()
                 if s in delivered and now - ts <= self.merger.max_age}
        for field in required & source.supplied:
            if field in self.counters:
                order = self.merger.priority(field)
                better = order[:order.index(source.name)] if source.name in order else order
                if not any(self.cumulative.last.get((s, field), (None, 0))[1] == now for s in better):
                    return True
                continue
            order = self.merger.priority(field)
            better = order[:order.index(source.name)] if source.name in order else order
            if not any(fresh.get(s, {}).get(field) is not None for s in better):
                return True
        return False

    def poll_cycle(self, now):
        """Poll the sources; return the names of the sources that delivered data."""
        delivered, polled = set(), False
        for source in self.sources:
            if not source.due(now):
                continue
            if polled and not self._wanted(source, now, delivered):
                continue
            polled = True
            if self.poll_source(source, now):
                delivered.add(source.name)
        self.cycles += 1
        return delivered

    def build_packet(self, now):
        record, _provenance = self.merger.merge(now)
        for field in self.counters:
            if field in self.pending:
                record[field] = self.pending.pop(field)
        for alias, target in self.aliases.items():
            if target in record:
                record[alias] = record[target]
        if self.diagnostics:
            for source in self.sources:
                record[f'ecowitt_{source.name}_online'] = 1 if source.online else 0
        record.update(dateTime=int(now), usUnits=weewx.METRICWX)
        return record

    def history_capable(self):
        return {s.name for s in self.sources
                if s.history != 'none' and (s.has_history or s.kind == 'http' or s.config.get('api_key'))}

    def genLoopPackets(self):
        self._start()
        while True:
            now = self.clock()
            delivered = self.poll_cycle(now)
            action = self.gap.update(now, delivered, self.history_capable())
            if action == RESTART:
                raise weewx.WeeWxIOError('restarting the engine to fill the outage from history')
            if action == EMIT:
                if delivered:
                    yield self.build_packet(now)
            else:
                self.pending.clear()
            delay = self.poll_interval - (self.clock() - now)
            if delay > 0:
                self.sleep(delay)

    # -- history -------------------------------------------------------------------

    def fetch_history(self, source, start_ts, stop_ts):
        """Raw canonical history records of source from its first provider that has data."""
        try:
            providers = source.history_providers(catchup_grace=self.catchup_grace, driver_debug=self.debug)
        except (DriverError, DeviceIOError, OSError) as e:
            log.error('%s: history not available: %s', source.name, e)
            return []
        for provider in providers:
            name = getattr(provider, 'name', provider.__class__.__name__)
            try:
                if hasattr(provider, 'net_to_driver_map'):
                    raw = list(provider.gen_history_records(start_ts=start_ts, stop_ts=stop_ts))
                else:
                    raw = list(provider.gen_history_records(start_ts=start_ts))
            except (DriverError, DeviceIOError, CatchupObjectError, OSError, ValueError, KeyError) as e:
                log.error('%s: %s failed: %s', source.name, name, e)
                continue
            raw = [r for r in raw if r.get('datetime') is not None and r['datetime'] <= stop_ts]
            if raw:
                log.info('%s: %d history records from %s', source.name, len(raw), name)
                source.has_history = True
                return raw
        return []

    def genStartupRecords(self, last_ts):
        from .history.aggregate import aggregate, merge_buckets, source_records
        if last_ts is None:
            log.info('empty database: no catchup')
            return
        now = self.clock()
        start = max(last_ts, now - self.max_catchup_days * 86400)
        if now - start <= self.archive_interval + self.catchup_grace:
            return
        log.info('catchup from %s', time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(start)))
        mapper = CatalogMapper(self.catalog, self.rain_source, unknown_fields='ignore')
        per_source = {}
        for source in self.sources:
            if source.history == 'none':
                continue
            raw = self.fetch_history(source, start - self.archive_interval, now)
            if raw:
                recs = source_records(raw, mapper, self.counters)
                per_source[source.name] = aggregate(recs, lambda f: self.catalog.agg.get(f, 'avg'),
                                                    self.archive_interval)
        records = merge_buckets(per_source, self.merger.priority, self.counters, self.aliases,
                                self.archive_interval, start, now)
        log.info('catchup: %d archive records', len(records))
        for rec in records:
            out = self.merger.validate('history', rec)
            out.update(dateTime=rec['dateTime'], usUnits=rec['usUnits'], interval=rec['interval'])
            yield out

    def closePort(self):
        for source in self.sources:
            try:
                source.close()
            except Exception as e:  # noqa: BLE001 - shutting down
                log.debug('%s: %s', source.name, e)
        self.started = False
