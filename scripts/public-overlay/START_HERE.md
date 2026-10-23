# START HERE — smartLarch Platform

## 1. Environment

Missing xraylarch → first launch can install a private runtime (internet once), or run `Install-Runtime.ps1` / `.sh`.

Developers: official `C:\ProgramData\xraylarch` or conda env `larch`.

## 2. Launch

**Windows:** `Launch-smartLarch-Platform.bat`  
**macOS:** `chmod +x Launch-smartLarch-Platform.command && ./Launch-smartLarch-Platform.command`

If launch fails, look for `smartLarch-launch.log` (and `smartLarch-runtime-install.log` / `smartLarch-startup-error.txt`) **next to the `.bat`**, in the extracted folder. Do not look in `output\`.

## 3. Plugins

Not stored in this git repository. Place companion HTML in `plugins/` locally. See `plugins/README.md`.

## 4. Packages

Zips are never committed. If you package locally: `python scripts/make_release.py`.

Copyright © 2026 by Dr. GENG Xun. Directly copy is prohibited.
