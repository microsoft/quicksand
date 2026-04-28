"""UVR release hooks for quicksand."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from uv_release.types import Hooks as ReleaseHook
from uv_release.types import Plan

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


# Packages whose changes require integration tests.
_TEST_PACKAGES = {
    "quicksand",
    "quicksand-ubuntu",
    "quicksand-alpine",
    "quicksand-core",
    "quicksand-qemu",
    "quicksand-smb",
}


def _latest_release_tag(pkg: str) -> str | None:
    """Find the latest release tag for a package using git tags."""
    result = subprocess.run(
        ["git", "tag", "-l", f"{pkg}/v*", "--sort=-v:refname"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    for line in result.stdout.strip().split("\n"):
        tag = line.strip()
        if tag and ".dev" not in tag and not tag.endswith("-dev"):
            return tag
    return None


class Hooks(ReleaseHook):
    def post_plan(self, workspace, intent, plan: Plan) -> Plan:
        """Skip jobs whose packages didn't change."""
        changed = {c.package.name for c in plan.changes}
        if "test" not in plan.skip and not (changed & _TEST_PACKAGES):
            plan.skip.append("test")
        if "verify-licenses" not in plan.skip and "quicksand-qemu" not in changed:
            plan.skip.append("verify-licenses")

        # Inject per-VM test install commands into the plan.
        # The workflow downloads built wheels into dist/ via download-artifact.
        # For unchanged packages we fetch their latest release wheels too.
        install_lines = [
            "uv venv --seed",
            "uv pip install --reinstall pytest pytest-timeout pytest-asyncio",
        ]
        for pkg in sorted(_TEST_PACKAGES):
            if pkg in changed:
                continue
            tag = _latest_release_tag(pkg)
            if not tag:
                continue
            dist_name = pkg.replace("-", "_")
            install_lines.append(
                f"gh release download {tag} --repo $GITHUB_REPOSITORY"
                f" --dir dist/"
                f" --pattern '{dist_name}-*linux_x86_64.whl'"
                f" --pattern '{dist_name}-*any.whl'"
                f" --clobber || true"
            )
        install_lines.append(
            "uv pip install --reinstall --find-links dist/"
            " dist/quicksand-*.whl quicksand-ubuntu quicksand-alpine"
        )
        install = "\n".join(install_lines)
        plan = plan.model_copy(
            update={"test_install": {vm: install for vm in ("ubuntu", "alpine")}}
        )

        # Inject a pre-hook on the build job to extract base images from dep
        # wheels into workspace source dirs.  When building overlays via
        # --packages (base images not rebuilt), uv resolves build-system deps
        # from workspace sources which lack qcow2 images.  Extracting them
        # first and clearing the uv cache ensures the build env gets images.
        _EXTRACT_SCRIPT = (
            "python3 -c \""
            "import zipfile, pathlib, shutil, os\\n"
            "deps = pathlib.Path('deps')\\n"
            "if deps.exists():\\n"
            "  for whl in sorted(deps.glob('*.whl')):\\n"
            "    with zipfile.ZipFile(whl) as zf:\\n"
            "      for name in zf.namelist():\\n"
            "        exts=('.qcow2','.kernel','.initrd')\\n"
            "        if not any(name.endswith(e) for e in exts): continue\\n"
            "        parts = pathlib.Path(name).parts\\n"
            "        if len(parts) < 2: continue\\n"
            "        pkg = parts[0].replace('_','-')\\n"
            "        dest = pathlib.Path('packages') / pkg / name\\n"
            "        if dest.exists(): continue\\n"
            "        dest.parent.mkdir(parents=True, exist_ok=True)\\n"
            "        print(f'  Extracting {name} from {whl.name}')\\n"
            "        with zf.open(name) as s, open(dest,'wb') as d:\\n"
            "          shutil.copyfileobj(s,d)\\n"
            "\""
            " && uv cache clean quicksand-ubuntu quicksand-alpine || true"
        )
        new_jobs = []
        for job in plan.jobs:
            if job.name == "build" and not job.pre_hook:
                job = job.model_copy(update={"pre_hook": _EXTRACT_SCRIPT})
            new_jobs.append(job)
        plan = plan.model_copy(update={"jobs": new_jobs})

        return plan

    def pre_build(self) -> None:
        """Install gh CLI on Windows runners (not pre-installed on self-hosted)."""
        import os
        import platform
        import urllib.request

        if platform.system() != "Windows":
            return
        if shutil.which("gh"):
            return

        print("Installing gh CLI for Windows...")
        machine = platform.machine().lower()
        arch = "arm64" if machine in ("arm64", "aarch64") else "amd64"
        url = f"https://github.com/cli/cli/releases/download/v2.74.0/gh_2.74.0_windows_{arch}.zip"
        zip_path = Path("gh_cli.zip")
        gh_dir = Path("gh_cli")
        try:
            urllib.request.urlretrieve(url, zip_path)
            import zipfile

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
            print("Build may fail if dependency wheels need to be fetched")

    def post_build(self) -> None:
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
