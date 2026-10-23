# smartLarch Platform

Unified **Athena-style XAFS prep** + **Larch/Artemis-style EXAFS fitting** in one desktop client (PySide6, xraylarch).

Version: see `source/app/version.py` (`APP_VERSION`).

Copyright © 2026 by Dr. GENG Xun. Directly copy is prohibited.

## Which file should I run?

| Goal | Command / file |
|------|----------------|
| **Windows** | `Launch-smartLarch-Platform.bat` (first run can install runtime) |
| **macOS** | `Launch-smartLarch-Platform.command` |
| **Repair runtime** | `Install-Runtime.ps1` / `.sh` |
| **Dev** | `$env:PYTHONPATH = "$PWD\source"; python -m app.main` |

The GUI is **single-instance**. Quit is a real quit (tray exit included).

## First-run runtime

If no xraylarch environment is found, the launcher can install a **private** Miniconda env (internet once, ~1–2 GB):

- Windows: `%LOCALAPPDATA%\smartLarch\Miniconda3\envs\smartlarch\`
- macOS: `~/Library/Application Support/smartLarch/miniconda3/envs/smartlarch/`

Official `xraylarch` / conda `larch` is still accepted.

## Plugins

Companion HTML tools are **not in this git repository**. They are maintained separately. Drop files into `plugins/` locally (see `plugins/README.md`). Do not commit plugin HTML here.

## Packages

Release zip files are **never stored in git**. Build with `python scripts/make_release.py` if you are packaging; attach zips to a GitHub Release if you distribute binaries.

## Docs

| File | Content |
|------|---------|
| `START_HERE.md` | Step-by-step |
| `CONTRIBUTING.md` | Development |
| `CHANGELOG.md` | Public history note |
| `SECURITY.md` | Reporting and residual risks |
| `THIRD_PARTY_NOTICES.md` | Third-party components |

Copyright © 2026 by Dr. GENG Xun. Directly copy is prohibited.
