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
# Modified by mKainzbauer, 2026-11: helpers extracted from the single-file
# upstream driver into a module; debug groups extended for multi-source use.
"""Small helpers shared by all modules of the Ecowitt multi-gateway driver."""

import logging
import re
import time
from collections.abc import MutableMapping

import weewx

log = logging.getLogger(__name__)


# Source: S2 weewx-EcowittGateway @5cbe533, bin/user/weewx-EcowittGateway.py, exception classes.
# Adapted: common base class DriverError, HistoryError added.
class DriverError(Exception):
    """Base class of the driver's own exceptions."""


class DeviceIOError(weewx.WeeWxIOError):
    """A source could not be reached or did not answer."""


class UnknownApiCommand(DriverError):
    pass


class CatchupObjectError(DriverError):
    pass


class ParseError(DriverError):
    pass


class ProcessorError(DriverError):
    pass


class UnitError(DriverError):
    pass


class InvalidApiResponseError(DriverError):
    pass


class ApiResponseError(DriverError):
    pass


class HistoryError(DriverError):
    """A history provider cannot deliver records."""


# Source: S2 @5cbe533, classes InvertibleSetError and InvertibleMap. Unchanged.
class InvertibleSetError(DriverError):
    def __init__(self, value):
        self.value = value
        super().__init__(f'The value "{value}" is already in the mapping.')


class InvertibleMap(dict):
    """A dict that maintains an inverse (value -> key) mapping."""

    def __init__(self, *args, inverse=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.inverse = inverse if inverse is not None else \
            self.__class__({v: k for k, v in self.items()}, inverse=self)

    def __setitem__(self, key, value):
        if value in self.inverse:
            raise InvertibleSetError(value)
        dict.__setitem__(self.inverse, value, key)
        dict.__setitem__(self, key, value)

    def __delitem__(self, key):
        dict.__delitem__(self.inverse, self[key])
        dict.__delitem__(self, key)

    def pop(self, key):
        dict.__delitem__(self.inverse, self[key])
        return dict.pop(self, key)


def _to_list(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [v.strip() for v in str(value).split(',') if v.strip()]


# Source: S2 weewx-EcowittGateway @5cbe533, bin/user/weewx-EcowittGateway.py, class DebugOptions.
# Adapted: debug groups 'merge', 'poll', 'gap', 'mqtt', 'schema' added.
class DebugOptions:
    """Driver specific debug switches from the 'debug' config option."""

    debug_groups = ('rain', 'raindelta', 'wind', 'lightning', 'loop', 'sensors', 'parser', 'catchup',
                    'archive', 'merge', 'poll', 'gap', 'mqtt', 'schema')

    def __init__(self, **config):
        requested = {d.lower() for d in _to_list(config.get('debug'))}
        for group in self.debug_groups:
            setattr(self, group, group in requested or 'all' in requested)

    @property
    def any(self):
        return any(getattr(self, g) for g in self.debug_groups)


# Source: S2 @5cbe533, class OnceLogger. Adapted: optional logger name prefix.
class OnceLogger:
    """Logs a repeating error once, and logs when it clears."""

    def __init__(self, label):
        self.label, self.last = label, None

    def error(self, msg):
        if msg != self.last:
            log.error('%s: %s', self.label, msg)
            self.last = msg

    def ok(self, msg):
        if self.last is not None:
            log.info('%s: %s', self.label, msg)
            self.last = None


# Source: S2 @5cbe533, function timestamp_to_string. Unchanged.
def timestamp_to_string(ts):
    """Format a unix timestamp as 'dd Month yyyy hh:mm:ss TZ (ts)'."""
    if ts is None:
        return '******* N/A *******'
    return f"{time.strftime('%d %B %Y %H:%M:%S %Z', time.localtime(ts))} ({int(ts)})"


# Source: S2 @5cbe533, functions _rng and _chmap. Unchanged.
def rng(fmt, n, start=1):
    """Return [fmt.format(start) .. fmt.format(n)]."""
    return [fmt.format(i) for i in range(start, n + 1)]


def chmap(pairs, n, channel_major=False):
    """Build {key_fmt.format(i): value_fmt.format(i)} for channels 1..n."""
    if channel_major:
        return {k.format(i): v.format(i) for i in range(1, n + 1) for k, v in pairs}
    return {k.format(i): v.format(i) for k, v in pairs for i in range(1, n + 1)}


# Source: S2 @5cbe533, function _try. Unchanged.
def try_conv(fn, value, default=None):
    """Return fn(value) or default if the conversion fails."""
    try:
        return fn(value)
    except (TypeError, ValueError):
        return default


# Source: S2 @5cbe533, functions natural_sort_keys / natural_sort_dict. Unchanged.
def natural_sort_keys(source_dict):
    return sorted(source_dict, key=lambda text: [int(c) if c.isdigit() else c
                                                 for c in re.split(r'(\d+)', str(text).lower())])


def natural_sort_dict(source_dict):
    return '{' + ', '.join(f"'{k}': '{source_dict[k]}'" for k in natural_sort_keys(source_dict)) + '}'


# Source: S2 @5cbe533, functions obfuscate / mask_secrets. Adapted: 'passkey' and 'mac' added to the secret keys.
_SECRET_KEY = re.compile(r'pwd|pass|key|token|secret|mac|_id$', re.IGNORECASE)


def obfuscate(plain, obf_char='*'):
    """Obfuscate all but the last few characters of a string."""
    if not plain:
        return plain
    plain = str(plain)
    n = len(plain)
    stem = 0 if n < 3 else 1 if n < 4 else 2 if n < 6 else 3 if n < 8 else 4
    return obf_char * (n - stem) + (plain[-stem:] if stem else '')


def mask_secrets(value, key=''):
    """A copy of a decoded API response with passwords, keys and station IDs obfuscated."""
    if isinstance(value, dict):
        return {k: mask_secrets(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [mask_secrets(v, key) for v in value]
    return obfuscate(str(value)) if value and _SECRET_KEY.search(str(key)) else value


# Source: S2 @5cbe533, functions flatten / channelise_enumerate. Unchanged.
def flatten(dictionary, parent_key=False, separator='.'):
    """Flatten nested dicts/lists into a single level dict with dotted keys."""
    if not hasattr(dictionary, 'keys'):
        return None
    items = {}
    for key, value in dictionary.items():
        new_key = f'{parent_key}{separator}{key}' if parent_key else key
        if isinstance(value, MutableMapping):
            items.update(flatten(value, new_key, separator) if value else {new_key: None})
        elif isinstance(value, list):
            if not value:
                items[new_key] = None
            for k, v in channelise_enumerate(value, channelise=True):
                items.update(flatten({str(k): v}, new_key, separator))
        else:
            items[new_key] = value
    return items


# Adapted: non-dict items and non-numeric ids no longer raise.
def channelise_enumerate(iterable, start=0, channelise=False):
    for n, elem in enumerate(iterable, start):
        if channelise and isinstance(elem, dict) and 'channel' in elem:
            n = int(elem['channel'])
        elif channelise and isinstance(elem, dict) and 'id' in elem:
            try:
                n = int(elem['id'])
            except (TypeError, ValueError):
                n = elem['id']
        yield n, elem


def to_list(value):
    """A comma separated config option (or a ConfigObj list) as a list of stripped strings."""
    return _to_list(value)


def to_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ('1', 'true', 'yes', 'on', 'y')


def to_int(value, default=None):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def to_float(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
