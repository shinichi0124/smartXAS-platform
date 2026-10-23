# Contributing

## Develop

1. Clone this repository.
2. Install Python 3 that can `import larch` and `import PySide6` (official xraylarch, or `Install-Runtime.ps1` / `.sh`).
3. From the repo root:

```powershell
.\Launch-smartLarch-Platform.ps1
```

```bash
chmod +x Launch-smartLarch-Platform.command Install-Runtime.sh
./Launch-smartLarch-Platform.command
```

## Tests

The test suite and its reference data are maintained privately and are not part of this repository.

## Do not commit

- Plugin HTML (`plugins/*.html`) — plugins are maintained separately
- Release zips (`*.zip`)
- Synchrotron / beamline spectra, private `.prj` archives, proposal data
- API keys, tokens, or machine-local paths

## Versioning

Semantic version in `source/app/version.py` (`APP_VERSION`). Keep the UI footer, README, and CHANGELOG in sync.

Copyright © 2026 by Dr. GENG Xun. Directly copy is prohibited.
