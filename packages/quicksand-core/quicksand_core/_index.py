"""Package index URLs used by ``quicksand install`` and auto-install.

Single source of truth so the CLI and the fat-wheel fallback agree on
where wheels live.
"""

from __future__ import annotations

# PEP 503 simple index generated from the per-package GitHub Releases and
# served via the quicksand GitHub Pages site.
QUICKSAND_INDEX_URL = "https://microsoft.github.io/quicksand/simple/"

# Public PyPI — used as a fallback for transitive dependencies that aren't
# quicksand packages (pydantic, anyio, …).
PYPI_INDEX_URL = "https://pypi.org/simple/"
