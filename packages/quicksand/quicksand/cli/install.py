"""Install optional quicksand packages from the quicksand simple index.

Delegates everything (version resolution, platform wheel selection,
transitive dependency resolution) to ``pip``. Every wheel attached to every
per-package GitHub release is exposed through a PEP 503 index at
``https://microsoft.github.io/quicksand/simple/``. PyPI is configured as an
extra index so transitive dependencies resolve normally.

Arguments use standard pip requirement syntax (PEP 508): ``quicksand-qemu``,
``quicksand-qemu==0.5.9``, ``quicksand-ubuntu>=0.4,<0.5``. Short aliases
(``qemu`` → ``quicksand-qemu``) are accepted in place of the project name.

Programmatic API::

    from quicksand import install

    install("qemu", "ubuntu")          # latest
    install("ubuntu==0.4.0")           # pin a version
"""

from __future__ import annotations

import argparse
import platform
import subprocess
import sys

from packaging.requirements import Requirement

# Short aliases → actual project names on the simple index / PyPI.
ALIASES: dict[str, list[str]] = {
    "qemu": ["quicksand-qemu"],
    "ubuntu": ["quicksand-ubuntu"],
    "alpine": ["quicksand-alpine"],
    "alpine-desktop": ["quicksand-alpine-desktop"],
    "ubuntu-desktop": ["quicksand-ubuntu-desktop"],
    "agent": ["quicksand-agent"],
    "cua": ["quicksand-cua"],
    "dev": ["quicksand-image-tools", "quicksand-overlay-scaffold", "quicksand-base-scaffold"],
}

# PEP 503 simple index served from the quicksand GitHub Pages site, listing
# every wheel attached to every per-package GitHub release. Falling back to
# PyPI keeps transitive dependencies (``pydantic``, ``anyio``, …) resolvable.
QUICKSAND_INDEX_URL = "https://microsoft.github.io/quicksand/simple/"
PYPI_INDEX_URL = "https://pypi.org/simple/"


# ── Helpers ───────────────────────────────────────────────────────────


def _expand_requirement(raw: str) -> list[Requirement]:
    """Parse *raw* as a PEP 508 requirement, expanding aliases on the name.

    ``qemu==0.5.9`` → ``[Requirement("quicksand-qemu==0.5.9")]``.
    ``dev`` → three Requirements (one per package in the alias).
    """
    req = Requirement(raw)
    mapped = ALIASES.get(req.name, [req.name])
    if mapped == [req.name]:
        return [req]
    out: list[Requirement] = []
    for pkg in mapped:
        clone = Requirement(pkg)
        clone.specifier = req.specifier
        clone.extras = req.extras
        clone.marker = req.marker
        out.append(clone)
    return out


def _collect(raw_requirements: list[str] | tuple[str, ...]) -> list[Requirement]:
    """Expand aliases and dedupe while preserving order."""
    seen: set[str] = set()
    out: list[Requirement] = []
    for raw in raw_requirements:
        for req in _expand_requirement(raw):
            key = str(req)
            if key not in seen:
                seen.add(key)
                out.append(req)
    return out


# ── Public API ────────────────────────────────────────────────────────


def install(*requirements: str) -> None:
    """Install quicksand packages from the GitHub-hosted simple index.

    Uses ``pip install`` so pip's resolver picks versions. Pass standard
    pip requirement strings (``quicksand-qemu==0.5.9``, ``ubuntu>=0.4``)
    or the short aliases listed in :data:`ALIASES`.

    Args:
        *requirements: PEP 508 requirements (e.g. ``"quicksand-qemu"``,
            ``"qemu==0.5.9"``, ``"ubuntu>=0.4,<0.5"``).

    Raises:
        RuntimeError: If pip install fails.

    Examples::

        from quicksand import install

        install("qemu", "ubuntu")
        install("alpine==0.4.0")
    """
    if not requirements:
        raise ValueError("At least one requirement is required")

    rc = _install_packages(_collect(requirements))
    if rc != 0:
        raise RuntimeError("pip install failed")


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register the install subcommand."""
    all_aliases = ", ".join(ALIASES.keys())
    parser = subparsers.add_parser(
        "install",
        help="Install packages from the quicksand simple index",
    )
    parser.add_argument(
        "requirements",
        nargs="+",
        metavar="REQUIREMENT",
        help=(
            "Pip requirements (e.g. quicksand-qemu, qemu==0.5.9, ubuntu>=0.4). "
            f"Aliases: {all_aliases}."
        ),
    )


def cmd(args: argparse.Namespace) -> int:
    """Install packages from the quicksand simple index."""
    return _install_packages(_collect(args.requirements))


# ── Implementation ────────────────────────────────────────────────────


def _install_packages(requirements: list[Requirement]) -> int:
    """Run ``pip install`` against the quicksand simple index."""
    print(f"Installing: {', '.join(str(r) for r in requirements)}")
    print(f"Platform: {platform.system()} {platform.machine()}")
    print()

    rc = _run_pip_install(requirements)
    if rc != 0:
        print("\nError: pip install failed.")
        return rc

    print(f"\nInstalled {len(requirements)} package(s)")
    return 0


def _run_pip_install(requirements: list[Requirement]) -> int:
    """Invoke ``pip install`` and let pip resolve versions from our indexes."""
    pip_args = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--upgrade",
        "--upgrade-strategy",
        "only-if-needed",
        "--index-url",
        QUICKSAND_INDEX_URL,
        "--extra-index-url",
        PYPI_INDEX_URL,
    ]
    pip_args.extend(str(r) for r in requirements)

    print(f"Running: pip install {' '.join(pip_args[4:])}")
    return subprocess.run(pip_args).returncode
