# Third-party notices — smartLarch Platform

This repository contains smartLarch Platform source code only. The components below are installed separately, under their own licenses; none of their code is copied into this repository.

| Component | Use | Upstream / license |
|---|---|---|
| xraylarch | XAFS processing and fitting runtime | https://github.com/xraypy/xraylarch — BSD-style license |
| PySide6 / Qt | Desktop UI | https://doc.qt.io/qtforpython/ — LGPLv3 / GPL / commercial |
| NumPy, SciPy, Matplotlib | Numerics and plotting | upstream licenses (BSD-style / PSF-based) |
| openpyxl | Excel export | https://foss.heptapod.net/openpyxl/openpyxl — MIT |
| pyMCR | Optional MCR-ALS engine | https://github.com/usnistgov/pyMCR — NIST public-domain notice |
| Miniconda | First-run environment bootstrap (downloaded at install time) | Anaconda repository terms |

## Literature and design credits

- Wavelet transform of EXAFS: implemented from the published methods — H. Funke, A. C. Scheinost, M. Chukalina, *Phys. Rev. B* 71, 094110 (2005) (Morlet); M. Muñoz, P. Argoul, F. Farges, *Am. Mineral.* 88, 694 (2003) (Cauchy); J. Timoshenko, A. Kuzmin, *Comput. Phys. Commun.* 180, 920 (2009).
- XAFS conventions follow M. Newville and B. Ravel (Larch / Demeter documentation).
- Design reference for batch workflows: zanes (https://github.com/kaifengZheng/zanes); no code from it is included.

No license to smartLarch Platform itself is granted by this notice; see `LICENSE`.
