# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 mKainzbauer
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version. See the LICENSE file or https://www.gnu.org/licenses/.
"""Installer for the Ecowitt Multi-Gateway driver: weectl extension install <zip or directory>"""

import configobj
from io import StringIO

from weecfg.extension import ExtensionInstaller

VERSION = '0.1.0'

CONFIG = """
[EcowittMulti]
    driver = user.ecowitt_multi
    source_priority = gw1
    poll_interval = 20
    rain_source = tipping
    unknown_fields = log
    gap_mode = restart
    max_catchup_days = 7
    auto_add_columns = false
    [[sources]]
        [[[gw1]]]
            type = http
            host = 192.168.1.10
            history = auto
    [[priority]]
"""

PKG = 'bin/user/ecowitt_multi'


def loader():
    return EcowittMultiInstaller()


class EcowittMultiInstaller(ExtensionInstaller):

    def __init__(self):
        super().__init__(
            version=VERSION,
            name='ecowitt_multi',
            description='WeeWX driver for several Ecowitt gateways (HTTP/MQTT) with priority merge and '
                        'SD card / Ecowitt.net backfill.',
            author='mKainzbauer',
            author_email='',
            data_services='user.ecowitt_multi.schema.EcowittSchemaService',
            config=configobj.ConfigObj(StringIO(CONFIG)),
            files=[
                (PKG, [f'{PKG}/__init__.py', f'{PKG}/catalog.py', f'{PKG}/configurator.py',
                       f'{PKG}/cumulative.py', f'{PKG}/ecowitt_catalog.json', f'{PKG}/gap.py',
                       f'{PKG}/mapping.py', f'{PKG}/merge.py', f'{PKG}/schema.py', f'{PKG}/units.py',
                       f'{PKG}/util.py']),
                (f'{PKG}/sources', [f'{PKG}/sources/__init__.py', f'{PKG}/sources/base.py',
                                    f'{PKG}/sources/http_api.py', f'{PKG}/sources/http_source.py',
                                    f'{PKG}/sources/mqtt_source.py', f'{PKG}/sources/parser.py']),
                (f'{PKG}/history', [f'{PKG}/history/__init__.py', f'{PKG}/history/aggregate.py',
                                    f'{PKG}/history/cloud.py', f'{PKG}/history/sd.py']),
            ])

    def configure(self, engine):
        """Use [EcowittMulti] as the station type if the user agrees."""
        import weeutil.weeutil
        if engine.config_dict.get('Station', {}).get('station_type') == 'EcowittMulti':
            return False
        try:
            ans = weeutil.weeutil.y_or_n('Use the Ecowitt Multi-Gateway driver as station type? (y/n) ',
                                         default='n')
        except (EOFError, OSError):
            return False
        if ans == 'y':
            engine.config_dict.setdefault('Station', {})['station_type'] = 'EcowittMulti'
            engine.config_dict.setdefault('StdArchive', {})['record_generation'] = 'software'
            return True
        return False
