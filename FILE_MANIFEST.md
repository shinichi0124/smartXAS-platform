# FILE_MANIFEST — smartLarch Platform (public tree)

| Path | Type | Purpose | Safe to delete? |
|------|------|---------|-----------------|
| `README.md` | doc | Quick start | No |
| `START_HERE.md` | doc | Step-by-step | No |
| `AGENTS.md` | doc | Public repo notes | No |
| `CONTRIBUTING.md` | doc | Development | No |
| `CHANGELOG.md` | doc | Public history note | No |
| `THIRD_PARTY_NOTICES.md` | doc | Runtime components and literature credits | No |
| `SECURITY.md` | doc | Reporting | No |
| `LICENSE` | doc | Copyright; directly copy is prohibited | No |
| `Launch-smartLarch-Platform.*` | launcher | Win / macOS | No |
| `Install-Runtime.*` | script | First-run Miniconda + larch | No |
| `source/` | source | Application | No |
| `plugins/` | slot | Runtime plugin directory; **HTML not in git** | No |
| `scripts/` | script | `make_release.py`, `validate_fit92.py`, maintainer sync | Scripts: no. Staging: yes |
| `ui/` | eng | layout snapshot | Keep current |
| `latest*.json` | meta | Update feed names/hashes | Regenerable |

Zips, `docs/`, `data/`, `source/tests/`, `Rawdatabase/`, `output/`, `tmp/`, `Troubleshootings/` and plugin HTML are not part of this git tree.
