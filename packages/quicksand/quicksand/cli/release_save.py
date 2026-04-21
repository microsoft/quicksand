"""Release a sandbox save to GitHub."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path
from urllib.error import HTTPError

REPO = "microsoft/quicksand"
GLOBAL_SAVES_DIR = Path.home() / ".quicksand" / "sandboxes"


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register the release subcommand."""
    parser = subparsers.add_parser(
        "release",
        help="Publish a sandbox save to GitHub releases",
    )
    parser.add_argument(
        "name",
        help="Save name (from .quicksand/sandboxes/) or path to save file",
    )
    parser.add_argument(
        "--tag",
        help="Custom release tag (default: <name>)",
    )
    parser.add_argument(
        "--title",
        help="Release title (default: 'Sandbox: <name>')",
    )
    parser.add_argument(
        "--notes",
        default="",
        help="Release notes",
    )
    parser.add_argument(
        "--repo",
        default=REPO,
        help=f"GitHub repo (default: {REPO})",
    )


_SAVE_EXTS = (".tar.gz", ".tar")


def _resolve_save(name: str) -> Path | None:
    """Resolve a save name to an existing save path.

    Precedence:
    1. Literal path (if contains / or has a save extension)
    2. Directory, .tar.gz, .tar in project-local sandboxes
    3. Directory, .tar.gz, .tar in user-global sandboxes
    """
    if "/" in name or "\\" in name or any(name.endswith(ext) for ext in _SAVE_EXTS):
        p = Path(name)
        return p if p.exists() else None

    suffixes = ["", *_SAVE_EXTS]
    for base_dir in [Path.cwd() / ".quicksand" / "sandboxes", GLOBAL_SAVES_DIR]:
        for suffix in suffixes:
            candidate = base_dir / f"{name}{suffix}"
            if candidate.exists():
                return candidate

    return None


def _get_gh_token() -> str:
    """Get GitHub token from gh CLI."""
    result = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, check=True)
    return result.stdout.strip()


def _format_size(size_bytes: int) -> str:
    """Format size in bytes to human readable string."""
    size = float(size_bytes)
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}TB"


def _get_release_id(tag: str, repo: str) -> int | None:
    """Get release ID by tag, or None if not found."""
    result = subprocess.run(
        ["gh", "api", f"repos/{repo}/releases/tags/{tag}", "--jq", ".id"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return int(result.stdout.strip())


def _upload_with_progress(tar_path: Path, release_id: int, repo: str, token: str) -> bool:
    """Upload a file to a GitHub release with a progress bar."""
    file_size = tar_path.stat().st_size
    file_name = tar_path.name
    upload_url = (
        f"https://uploads.github.com/repos/{repo}/releases/{release_id}/assets?name={file_name}"
    )

    # Read the file and upload with progress
    start_time = time.time()
    uploaded = 0

    class ProgressReader:
        """File-like wrapper that prints progress on read()."""

        def __init__(self, fp, total: int):
            self._fp = fp
            self._total = total

        def read(self, n: int = -1) -> bytes:
            nonlocal uploaded
            data = self._fp.read(n)
            if data:
                uploaded += len(data)
                elapsed = time.time() - start_time
                speed = uploaded / elapsed if elapsed > 0 else 0
                percent = (uploaded / self._total * 100) if self._total > 0 else 0
                bar_width = 30
                filled = int(bar_width * uploaded / self._total) if self._total > 0 else 0
                bar = "#" * filled + "-" * (bar_width - filled)
                sys.stdout.write(
                    f"\r  {bar} {percent:5.1f}% "
                    f"{_format_size(uploaded)}/{_format_size(self._total)} "
                    f"({_format_size(int(speed))}/s)"
                )
                sys.stdout.flush()
            return data

        def __len__(self) -> int:
            return self._total

    with open(tar_path, "rb") as f:
        reader = ProgressReader(f, file_size)
        req = urllib.request.Request(upload_url, data=reader, method="POST")
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Content-Type", "application/octet-stream")
        req.add_header("Content-Length", str(file_size))

        try:
            urllib.request.urlopen(req)
            sys.stdout.write("\n")
            return True
        except HTTPError as e:
            sys.stdout.write("\n")
            print(f"Upload failed: {e.code} {e.reason}", file=sys.stderr)
            body = e.read().decode(errors="replace")
            if body:
                print(f"  {body[:200]}", file=sys.stderr)
            return False


def cmd(args: argparse.Namespace) -> int:
    """Publish a sandbox save as a GitHub release."""
    name = args.name
    save_path = _resolve_save(name)

    if "/" in name or "\\" in name or any(name.endswith(ext) for ext in _SAVE_EXTS):
        release_name = Path(name).stem
        if release_name.endswith(".tar"):
            release_name = release_name[:-4]
    else:
        release_name = name

    if save_path is None:
        print(f"Save not found: {name}", file=sys.stderr)
        return 1

    # Check gh CLI
    if not shutil.which("gh"):
        print("Error: gh CLI not found. Install from https://cli.github.com/", file=sys.stderr)
        return 1

    result = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True)
    if result.returncode != 0:
        print("Error: gh CLI not authenticated. Run: gh auth login", file=sys.stderr)
        return 1

    # If save is a directory, pack it into a temp .tar.gz for upload
    tmp_tar: Path | None = None
    if save_path.is_dir():
        print("Packing save directory...")
        tmp_dir = Path(tempfile.mkdtemp(prefix="quicksand-release-"))
        tmp_tar = tmp_dir / f"{release_name}.tar.gz"
        with tarfile.open(tmp_tar, "w:gz") as tar:
            for item in save_path.iterdir():
                tar.add(item, arcname=item.name)
        upload_path = tmp_tar
    else:
        upload_path = save_path

    tag = args.tag or release_name
    title = args.title or f"Sandbox: {release_name}"
    repo = args.repo
    size_mb = upload_path.stat().st_size / 1024 / 1024

    print(f"Publishing {upload_path.name} ({size_mb:.1f} MB)")
    print(f"  Repo: {repo}")
    print(f"  Tag:  {tag}")
    print()

    token = _get_gh_token()

    # Ensure release exists
    release_id = _get_release_id(tag, repo)
    if release_id is None:
        print(f"Creating release '{tag}'...")
        result = subprocess.run(
            [
                "gh",
                "release",
                "create",
                tag,
                "--repo",
                repo,
                "--title",
                title,
                "--notes",
                args.notes or f"Pre-configured sandbox environment: {release_name}",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"Error: Failed to create release: {result.stderr}", file=sys.stderr)
            return 1
        release_id = _get_release_id(tag, repo)
        if release_id is None:
            print("Error: Release created but could not fetch ID.", file=sys.stderr)
            return 1

    # Delete existing asset with same name (for --clobber behavior)
    existing = subprocess.run(
        [
            "gh",
            "api",
            f"repos/{repo}/releases/{release_id}/assets",
            "--jq",
            f'.[] | select(.name == "{upload_path.name}") | .id',
        ],
        capture_output=True,
        text=True,
    )
    if existing.returncode == 0 and existing.stdout.strip():
        asset_id = existing.stdout.strip()
        print("Replacing existing asset...")
        subprocess.run(
            ["gh", "api", "-X", "DELETE", f"repos/{repo}/releases/assets/{asset_id}"],
            capture_output=True,
        )

    # Upload with progress
    print(f"Uploading {upload_path.name}")
    success = _upload_with_progress(upload_path, release_id, repo, token)

    # Clean up temp tar
    if tmp_tar is not None:
        shutil.rmtree(tmp_tar.parent, ignore_errors=True)

    if not success:
        return 1

    print(f"\nPublished! Install with: quicksand install {release_name}")
    return 0
