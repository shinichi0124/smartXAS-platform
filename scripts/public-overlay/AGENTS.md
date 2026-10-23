# smartLarch Platform — public repo notes

Unified Athena-style XAFS preparation + Larch/Artemis-style fitting on **xraylarch**.

Copyright © 2026 by Dr. GENG Xun. Directly copy is prohibited.

## Run

- Windows: `Launch-smartLarch-Platform.bat` (or `.ps1`)
- macOS: `Launch-smartLarch-Platform.command`
- First run can install a private Miniconda env (`Install-Runtime.ps1` / `.sh`)

Version: `source/app/version.py` (`APP_VERSION`).

## What this repository is

Platform **source only**. Do not commit:

- Plugin HTML (`plugins/*.html`) — plugins are maintained separately
- Release zips (`*.zip`) — never stored in git
- Beamline / synchrotron spectra, proposal data, or private `.prj` archives
- API keys, tokens, or local paths

The `plugins/` folder is a runtime slot. Drop companion HTML there locally; do not add those files to git.

## Contribute

See `CONTRIBUTING.md`. Edit bilingual strings in `source/app/i18n.py` (zh+en together).
