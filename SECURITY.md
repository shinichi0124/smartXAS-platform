# Security — smartLarch Platform

Report suspected issues privately to the maintainer. Do not attach API keys or confidential spectra.

## Secrets

API keys stay in local Qt `QSettings` on the user's machine. They are not in this repository. Do not commit `.env`, tokens, or credential files.

## Updates and installers

- Main-program updates accept only a current-platform ZIP whose SHA-256 matches its manifest. Startup never executes a neighbouring launcher.
- First-run Miniconda artifacts use immutable versioned URLs and SHA-256 verification.
- AI CLI install buttons open vendor documentation; downloaded scripts are not piped into a shell.

## Data and plugins

- This git tree does **not** ship synchrotron / beamline demo spectra.
- Plugin HTML is **not** stored in this repository. Treat third-party plugin files as untrusted until you review them.
- Release zips are never committed here.

## Residual risks

- API keys are local settings values, not OS credential-vault entries.
- ZIP manifests provide integrity against folder substitution, not publisher authenticity. Compare the published digest before installing an update.
- macOS packaging produced on Windows is unsigned until notarized on a Mac.
