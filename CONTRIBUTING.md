# Contributing

<!-- SPDX-License-Identifier: GPL-3.0-or-later -->

## Adding a new sensor

Ecowitt releases new sensors regularly. The goal is that a new sensor usually needs only a
**catalog change**, not a code change.

1. Collect data. On a system with the sensor, run:
   ```
   weectl device --dump-api > dump.json
   weectl device --unknown-fields
   ```
   `--unknown-fields` lists keys that are not in the catalog and prints draft catalog entries.
   Until the catalog is updated, `unknown_fields = pass` stores them as `ecowitt_<key>`.
2. Edit the catalog. Add the entries to `EXTRA_FIELDS` in `tools/build_catalog.py`, which
   generates `ecowitt_catalog.json`. Raise `CATALOG_VERSION` / `SCHEMA_VERSION` there.
   Each entry needs:
   - `weewx` name, then exactly one of `key` / `counter` / `alias_of`;
   - `agg` (avg/sum/max/min/last/vecdir), `group` (`group_…`), `sql` (REAL/INTEGER);
   - `min_schema_version`;
   - optionally the SD/cloud/MQTT keys.
3. Bump the version:
   - a new field: raise the catalog `version` (minor) and, if a DB column is needed,
     `schema_version` (minor); set the new fields' `min_schema_version` to it;
   - a changed meaning or type of an existing field: major.
4. Regenerate the catalog and test it:
   ```
   python tools/build_catalog.py
   python tools/build_catalog.py --check
   python -m pytest -q
   ```
   Add a synthetic fixture for the new key (e.g. in `tests/test_catalog.py`). **Do not
   commit real data that contains a MAC address, passkey or API key.** `--dump-api` masks
   secrets, but check the output anyway.
5. Update `CHANGELOG.md` (mention the new catalog/schema version).

Users can try out a newer catalog without updating the driver via `catalog_file = …`. If
`min_schema_version` of a field is higher than the DB's schema version, that field is not
stored. In that case, run `weectl device --check-schema` and then `--add-columns`.

## Code, licence and attribution

- All code is GPL-3.0-or-later. Each file starts with
  `# SPDX-License-Identifier: GPL-3.0-or-later` and a copyright line. `tests/` checks this.
- Code taken from another project (even if modified) needs:
  - a licence compatible with GPLv3;
  - a `# Source: <ID> <repo>@<commit>, <file>, <lines/class>. <Unchanged|Adapted: …>`
    comment;
  - `Portions Copyright` of the original authors in the header;
  - a modification notice;
  - an entry in the source table and provenance table of `CREDITS.md`, added **before** the
    code is used.
- Reference-only sources (concepts, formats) are listed in `CREDITS.md`; copying their code
  is not allowed.
- Commits: write clear messages and list the sources you used.
