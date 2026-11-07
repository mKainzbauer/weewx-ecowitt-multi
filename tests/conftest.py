# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 mKainzbauer
"""Test setup: WeeWX source tree (WEEWX_SRC, default ../weewx/src) and bin/ on sys.path.

All device data used in the tests is synthetic (hand-written), not captured from real gateways.
"""

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WEEWX_SRC = os.environ.get('WEEWX_SRC', os.path.join(os.path.dirname(ROOT), 'weewx', 'src'))
for path in (WEEWX_SRC, os.path.join(ROOT, 'bin')):
    if path not in sys.path:
        sys.path.insert(0, path)

from user.ecowitt_multi.sources.base import Source  # noqa: E402
from user.ecowitt_multi.util import DeviceIOError  # noqa: E402


class FakeSource(Source):
    """A source returning queued canonical data; None in the queue means 'offline'."""

    kind = 'http'

    def __init__(self, name, frames=None, records=None, **config):
        super().__init__(name, dict(config))
        self.frames = list(frames or [])
        self.polls = 0
        self.history_records = records

    def poll(self, now):
        self.polls += 1
        frame = self.frames.pop(0) if len(self.frames) > 1 else (self.frames[0] if self.frames else None)
        if frame is None:
            raise DeviceIOError(f'{self.name}: offline')
        return dict(frame, datetime=now)

    def history_providers(self, **options):
        if self.history_records is None:
            return []
        records = self.history_records

        class Provider:
            name = 'fake history'

            def gen_history_records(self, start_ts=None):
                return [r for r in records if start_ts is None or r['datetime'] > start_ts]

        return [Provider()]


@pytest.fixture
def catalog():
    from user.ecowitt_multi.catalog import load_catalog
    return load_catalog()


class Clock:
    def __init__(self, start=1_800_000_000):
        self.now = start

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


@pytest.fixture
def clock():
    return Clock()
