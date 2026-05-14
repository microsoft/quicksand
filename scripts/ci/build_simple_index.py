"""Generate a PEP 503 simple repository index from GitHub Releases.

Walks every release on the repo, groups wheels by their package name (parsed
from the per-package release tag, e.g. ``quicksand-qemu/v0.5.9``), and emits
static HTML pages that pip can consume::

    <root>/simple/index.html              ← lists every package
    <root>/simple/<package>/index.html    ← lists every wheel for the package

Run inside CI before the Pages artifact is uploaded so the index ships with
the docs site:

    python scripts/ci/build_simple_index.py docs/.vitepress/dist

Environment:
    GH_TOKEN: optional; raises GitHub API rate limits.
    GITHUB_REPOSITORY: ``owner/name`` (defaults to ``microsoft/quicksand``).
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from collections import defaultdict
from collections.abc import Iterator
from html import escape
from pathlib import Path

REPO = os.environ.get("GITHUB_REPOSITORY", "microsoft/quicksand")
API_ROOT = f"https://api.github.com/repos/{REPO}"

# Per-package release tags look like ``<pkg>/v<version>``. ``-base`` / ``-dev``
# suffixes denote internal markers, not user-installable releases.
_TAG_RE = re.compile(r"^(?P<pkg>[a-zA-Z0-9._-]+)/v(?P<version>.+?)(?:-base|-dev)?$")


def _api(path: str) -> urllib.request.Request:
    req = urllib.request.Request(path)
    req.add_header("Accept", "application/vnd.github+json")
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    return req


def _paginate(path: str) -> Iterator[dict]:
    page = 1
    while True:
        url = f"{path}?per_page=100&page={page}"
        with urllib.request.urlopen(_api(url)) as resp:
            batch = json.load(resp)
        if not batch:
            return
        yield from batch
        if len(batch) < 100:
            return
        page += 1


def _normalize(name: str) -> str:
    """PEP 503 name normalization: lowercase + runs of ``-_.`` collapsed to ``-``."""
    return re.sub(r"[-_.]+", "-", name).lower()


def collect_releases() -> dict[str, list[dict]]:
    """Return ``{normalized_package_name: [wheel_asset, ...]}``.

    Each asset dict has ``filename`` and ``url`` keys.
    """
    by_pkg: dict[str, list[dict]] = defaultdict(list)
    for release in _paginate(f"{API_ROOT}/releases"):
        match = _TAG_RE.match(release["tag_name"])
        if not match:
            continue
        if release["tag_name"].endswith(("-base", "-dev")):
            continue
        pkg = _normalize(match.group("pkg"))
        for asset in release.get("assets", []):
            name = asset["name"]
            if not name.endswith(".whl"):
                continue
            by_pkg[pkg].append(
                {
                    "filename": name,
                    "url": asset["browser_download_url"],
                }
            )
    return by_pkg


def render_package_page(package: str, assets: list[dict]) -> str:
    """Render the per-package PEP 503 page."""
    # Sort by filename for stable output.
    assets = sorted(assets, key=lambda a: a["filename"])
    lines = [
        "<!DOCTYPE html>",
        '<html><head><meta name="pypi:repository-version" content="1.0">',
        f"<title>Links for {escape(package)}</title>",
        "</head><body>",
        f"<h1>Links for {escape(package)}</h1>",
    ]
    for asset in assets:
        href = escape(asset["url"], quote=True)
        text = escape(asset["filename"])
        lines.append(f'<a href="{href}">{text}</a><br>')
    lines.append("</body></html>")
    return "\n".join(lines)


def render_root_page(packages: list[str]) -> str:
    """Render ``simple/index.html`` listing every package."""
    packages = sorted(packages)
    lines = [
        "<!DOCTYPE html>",
        '<html><head><meta name="pypi:repository-version" content="1.0">',
        "<title>Simple Index</title>",
        "</head><body>",
        "<h1>Simple Index</h1>",
    ]
    for pkg in packages:
        lines.append(f'<a href="{escape(pkg, quote=True)}/">{escape(pkg)}</a><br>')
    lines.append("</body></html>")
    return "\n".join(lines)


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    out_root = Path(sys.argv[1]) / "simple"
    out_root.mkdir(parents=True, exist_ok=True)

    by_pkg = collect_releases()
    if not by_pkg:
        print("No wheel assets found across GitHub releases.", file=sys.stderr)
        return 1

    for pkg, assets in by_pkg.items():
        pkg_dir = out_root / pkg
        pkg_dir.mkdir(parents=True, exist_ok=True)
        (pkg_dir / "index.html").write_text(render_package_page(pkg, assets))
        print(f"  {pkg}: {len(assets)} wheels")

    (out_root / "index.html").write_text(render_root_page(list(by_pkg)))
    print(f"\nGenerated index for {len(by_pkg)} package(s) at {out_root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
