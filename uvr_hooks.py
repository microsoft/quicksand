"""UVR release hooks for quicksand."""

from __future__ import annotations

import re
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any

from uv_release.dependencies.release.plan import Plan
from uv_release.dependencies.shared.hooks import Hooks as ReleaseHook

# Canonical runner per architecture — retag only runs on these.
# x64 images are built natively on Linux; arm64 images are built on
# macOS because the Linux arm64 runners lack KVM for overlay builds.
RETAG_RUNNERS: list[list[str]] = [
    ["self-hosted", "linux", "x64"],
    ["self-hosted", "macos", "arm64"],
]

_WHEEL_RE = re.compile(
    r"^(?P<name>.+?)-(?P<version>.+?)-(?P<python>\w+)-(?P<abi>\w+)-(?P<platform>.+)\.whl$"
)
_ARCH_RE = re.compile(r"(?:x86_64|aarch64|arm64|amd64)$")

# Architecture -> all platform tags the wheel should be available for.
_ARCH_TARGETS: dict[str, list[str]] = {
    "x86_64": ["linux_x86_64", "macosx_10_13_x86_64", "win_amd64"],
    "amd64": ["linux_x86_64", "macosx_10_13_x86_64", "win_amd64"],
    "aarch64": ["linux_aarch64", "macosx_11_0_arm64", "win_arm64"],
    "arm64": ["linux_aarch64", "macosx_11_0_arm64", "win_arm64"],
}

# Packages that are natively built per-platform (not retagged).
_SKIP = {"quicksand_qemu"}


def _retag_wheels(dist: Path) -> None:
    """Copy each image wheel into variants for every supported host platform."""
    for whl in sorted(dist.glob("*.whl")):
        m = _WHEEL_RE.match(whl.name)
        if not m:
            continue
        parts = m.groupdict()
        if parts["name"] in _SKIP:
            continue
        arch_m = _ARCH_RE.search(parts["platform"])
        if not arch_m:
            continue

        for target in _ARCH_TARGETS[arch_m.group()]:
            new_name = f"{parts['name']}-{parts['version']}-py3-none-{target}.whl"
            dest = dist / new_name
            if dest.resolve() != whl.resolve() and not dest.exists():
                shutil.copy2(whl, dest)
                print(f"  {whl.name} -> {new_name}")


# Runner used to build pure (py3-none-any) wheels for PyPI.
# Only one runner needs to do this since the output is platform-independent.
_PURE_WHEEL_RUNNER: list[str] = ["self-hosted", "linux", "x64"]

# PyPI per-file size limit.
_PYPI_MAX_SIZE = 100 * 1024 * 1024  # 100 MB


def _discover_contrib_packages() -> dict[str, str]:
    """Return ``{dist_name: package_path}`` for contrib packages with custom build hooks."""
    import tomllib

    contrib: dict[str, str] = {}
    for toml_path in sorted(Path("packages/contrib").glob("*/pyproject.toml")):
        with open(toml_path, "rb") as f:
            data = tomllib.load(f)
        hooks = (
            data.get("tool", {})
            .get("hatch", {})
            .get("build", {})
            .get("targets", {})
            .get("wheel", {})
            .get("hooks", {})
        )
        if "custom" in hooks:
            dist_name = data["project"]["name"].replace("-", "_")
            contrib[dist_name] = str(toml_path.parent)
    return contrib


def _build_pure_wheels(dist: Path) -> None:
    """Build ``py3-none-any`` wheels for contrib packages that have fat wheels in *dist*."""
    import os

    contrib = _discover_contrib_packages()
    if not contrib:
        return

    env = {**os.environ, "QUICKSAND_PURE_WHEEL": "1"}
    built: set[str] = set()
    for whl in sorted(dist.glob("*.whl")):
        name = whl.name.split("-")[0]
        if name in contrib and name not in built:
            built.add(name)
            pkg_path = contrib[name]
            print(f"  Building pure wheel: {pkg_path}")
            subprocess.run(
                [
                    "uv",
                    "build",
                    pkg_path,
                    "--wheel",
                    "--out-dir",
                    str(dist),
                    "--find-links",
                    "deps",
                    "--find-links",
                    str(dist),
                    "--no-sources",
                ],
                env=env,
                check=True,
            )


def _filter_wheels_for_pypi(dist: Path) -> None:
    """Keep either fat or pure wheels per package based on the PyPI size limit.

    For packages that have both platform-tagged (fat) and ``py3-none-any`` (pure)
    variants: delete the fat wheels if any exceeds 100 MB, otherwise delete the
    pure wheel so pip installs the platform-specific one.
    """
    packages: dict[str, list[Path]] = defaultdict(list)
    for whl in dist.glob("*.whl"):
        packages[whl.name.split("-")[0]].append(whl)

    for _dist_name, wheels in packages.items():
        pure = [w for w in wheels if "py3-none-any" in w.name]
        fat = [w for w in wheels if "py3-none-any" not in w.name]
        if not pure or not fat:
            continue

        oversized = any(w.stat().st_size > _PYPI_MAX_SIZE for w in fat)
        if oversized:
            for w in fat:
                size_mb = w.stat().st_size / 1024 / 1024
                print(f"  Removing oversized wheel: {w.name} ({size_mb:.0f} MB)")
                w.unlink()
        else:
            for w in pure:
                print(f"  Removing pure wheel (fat wheels are under 100 MB): {w.name}")
                w.unlink()


# Packages whose changes require integration tests.
_TEST_PACKAGES = {
    "quicksand",
    "quicksand-ubuntu",
    "quicksand-alpine",
    "quicksand-core",
    "quicksand-qemu",
    "quicksand-smb",
}


class Hooks(ReleaseHook):
    def post_plan(self, workspace, intent, plan: Plan) -> Plan:
        """Customize the release plan."""
        changed = {r.name for r in plan.releases}
        if "test" not in plan.skip and not (changed & _TEST_PACKAGES):
            plan.skip.append("test")
        if "verify-licenses" not in plan.skip and "quicksand-qemu" not in changed:
            plan.skip.append("verify-licenses")

        # Inject a platform-filter command after dep downloads.  uv
        # incorrectly picks linux_aarch64 over macosx_arm64 from find-links
        # when both are present, so we remove non-native wheels after download.
        from uv_release.commands import ShellCommand

        new_jobs = []
        for job in plan.jobs:
            if job.name == "build":
                new_cmds = []
                for cmd in job.commands:
                    new_cmds.append(cmd)
                    if getattr(cmd, "type", None) == "download_wheels":
                        new_cmds.append(
                            ShellCommand(
                                label="Remove non-native wheels from deps",
                                check=False,
                                args=[
                                    "python3",
                                    "scripts/ci/filter_deps_platform.py",
                                    "deps",
                                ],
                            )
                        )
                job = job.model_copy(update={"commands": new_cmds})
            new_jobs.append(job)
        plan = plan.model_copy(update={"jobs": new_jobs})

        return plan

    def ensure_gh(self) -> None:
        """Ensure gh CLI is available (not pre-installed on all runners)."""
        import platform

        if shutil.which("gh"):
            return
        system = platform.system().lower()
        if system == "linux":
            print("Installing gh CLI...")
            install_cmd = (
                "curl -fsSL https://cli.github.com/packages/"
                "githubcli-archive-keyring.gpg"
                " | sudo dd of=/usr/share/keyrings/"
                "githubcli-archive-keyring.gpg"
                " && echo 'deb [arch=amd64 signed-by="
                "/usr/share/keyrings/githubcli-archive-keyring.gpg]"
                " https://cli.github.com/packages stable main'"
                " | sudo tee /etc/apt/sources.list.d/github-cli.list"
                " > /dev/null"
                " && sudo apt-get update -qq"
                " && sudo apt-get install -y -qq gh"
            )
            subprocess.run(["bash", "-c", install_cmd], check=True)
        elif system == "windows":
            self._install_gh_windows()

    def _install_gh_windows(self) -> None:
        """Install gh CLI on Windows via zip download."""
        import os
        import platform
        import urllib.request
        import zipfile

        print("Installing gh CLI for Windows...")
        machine = platform.machine().lower()
        arch = "arm64" if machine in ("arm64", "aarch64") else "amd64"
        url = f"https://github.com/cli/cli/releases/download/v2.74.0/gh_2.74.0_windows_{arch}.zip"
        zip_path = Path("gh_cli.zip")
        gh_dir = Path("gh_cli")
        try:
            urllib.request.urlretrieve(url, zip_path)
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(gh_dir)
            zip_path.unlink(missing_ok=True)
            for candidate in gh_dir.rglob("gh.exe"):
                os.environ["PATH"] = str(candidate.parent) + os.pathsep + os.environ.get("PATH", "")
                print(f"Installed gh CLI: {candidate}")
                return
            print("Warning: gh.exe not found in zip archive")
        except Exception as e:
            print(f"Warning: failed to install gh CLI: {e}")

    def pre_build(self) -> None:
        """Ensure gh CLI is available before building (needed by some runners)."""
        self.ensure_gh()

    def pre_command(self, job_name: str, command: Any) -> None:
        """Filter oversized wheels before the first PyPI publish command."""
        if (
            job_name == "publish"
            and getattr(command, "type", None) == "publish_to_index"
            and not getattr(self, "_pypi_filtered", False)
        ):
            self._pypi_filtered = True
            print("Filtering oversized wheels for PyPI...")
            _filter_wheels_for_pypi(Path("dist"))

    def retag(self) -> None:
        """Retag platform-specific wheels for cross-OS distribution."""
        import json
        import os

        runner_json = os.environ.get("UVR_RUNNER")
        if not runner_json:
            return
        runner = json.loads(runner_json)
        if runner not in RETAG_RUNNERS:
            return
        print(f"Retagging wheels for cross-platform (runner: {runner})")
        _retag_wheels(Path("dist"))

        if runner == _PURE_WHEEL_RUNNER:
            print("Building pure wheels for contrib packages...")
            _build_pure_wheels(Path("dist"))

    def post_build(self) -> None:
        """Local builds — delegate to retag."""
        self.retag()
